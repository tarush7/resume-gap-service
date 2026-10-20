from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import ValidationError

from langextract_requirements import (
    CLEANUP_VERSION,
    EXTRACTION_PARAMETERS,
    LANGEXTRACT_PROMPT_VERSION,
    MODEL_PARAMETERS,
    extract_requirement_records_with_warnings,
)
from main import (
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    JobInput,
    JobRequirements,
    RequirementItem,
    call_nhost_graphql,
    replace_job_requirements,
    validate_job_requirements,
)

EXTRACTOR_NAME = "langextract"
AUDIT_TABLE_NAME = "job_requirement_extraction_audits"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def description_hash(description: str) -> str:
    return hashlib.sha256(description.encode("utf-8")).hexdigest()


def extraction_metadata(job: dict[str, Any], filter_policy: str) -> dict[str, Any]:
    root = Path(__file__).resolve().parent
    code = b"".join(
        (root / name).read_bytes()
        for name in (
            "langextract_requirements.py",
            "main.py",
            "batch_extract_requirements.py",
        )
    )
    revision = (
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        or "unknown"
    )
    description = job.get("job_description") or ""
    return {
        "description_sha256": description_hash(description),
        "job_description": description,
        "code_revision": revision,
        "code_hash": hashlib.sha256(code).hexdigest(),
        "cleanup_version": CLEANUP_VERSION,
        "prompt_version": LANGEXTRACT_PROMPT_VERSION,
        "model_name": OLLAMA_MODEL,
        "model_parameters": MODEL_PARAMETERS,
        "extraction_parameters": EXTRACTION_PARAMETERS,
        "filter_policy": filter_policy,
    }


def freeze_jobs(path: Path, jobs: list[dict[str, Any]]) -> None:
    payload = {
        "format_version": 1,
        "jobs": [
            {
                **job,
                "description_sha256": description_hash(
                    job.get("job_description") or ""
                ),
            }
            for job in jobs
        ],
    }
    with path.open("x") as output:
        json.dump(payload, output, ensure_ascii=False, indent=2)


def load_job_snapshot(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text())
    if payload.get("format_version") != 1:
        raise ValueError("Unsupported job snapshot format")
    jobs = payload["jobs"]
    seen = set()
    for job in jobs:
        job_id = UUID(job["id"])
        if job_id in seen:
            raise ValueError("Duplicate job in snapshot")
        seen.add(job_id)
        if job["description_sha256"] != description_hash(
            job.get("job_description") or ""
        ):
            raise ValueError(f"JD hash mismatch: {job_id}")
    return jobs


def count_actions(items: list[dict[str, Any]], action: str) -> int:
    return sum(item.get("action", "rejected") == action for item in items)


def failure_kind(error: Exception, stage: str) -> str:
    if stage in {"saving", "audit"}:
        return stage
    chain = error
    while chain is not None:
        if (
            "timeout" in type(chain).__name__.lower()
            or "timed out" in str(chain).lower()
        ):
            return "timeout"
        if "pars" in type(chain).__name__.lower():
            return "parsing"
        chain = chain.__cause__
    if isinstance(error, ValidationError) or stage in {"input", "validation"}:
        return "schema_or_validation"
    outcome = getattr(error, "outcome", None)
    if outcome is not None:
        if any(
            warning["type"] in {"ungrounded_evidence", "invalid_evidence_interval"}
            for warning in outcome.warnings
        ):
            return "grounding"
        if any(event["action"] == "filtered" for event in outcome.events):
            return "filtering"
    return "extraction"


def outcome_details(outcome, warnings: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "raw_extractions": outcome.raw_extractions,
        "pre_cleanup_records": outcome.pre_cleanup_records,
        "events": outcome.events,
        "validation_events": warnings,
        "raw_items": len(outcome.raw_extractions),
        "grounded_items": len(outcome.pre_cleanup_records),
        "recovered_items": count_actions(outcome.events, "recovered"),
        "rejected_items": count_actions(warnings, "rejected"),
        "filtered_items": count_actions(outcome.events, "filtered"),
        "merged_duplicates": count_actions(outcome.events + warnings, "merged"),
        "ungrounded_items": sum(
            warning["type"] in {"ungrounded_evidence", "invalid_evidence_interval"}
            for warning in warnings
        ),
        "structural_rejections": sum(
            warning["type"] in {"unexpected_extraction_class", "invalid_attributes"}
            or (
                warning["type"] == "invalid_requirement_record"
                and warning.get("reason_type") == "schema"
            )
            for warning in warnings
        ),
    }


def fetch_jobs(page_size: int = 100) -> list[dict[str, Any]]:
    """Fetch every job in stable ID order using bounded GraphQL pages."""
    query = """
    query BatchJobs($limit: Int!, $offset: Int!) {
        jobs(order_by: {id: asc}, limit: $limit, offset: $offset) {
            id
            job_title
            company
            source
            job_description
        }
    }
    """
    jobs: list[dict[str, Any]] = []

    while True:
        page = call_nhost_graphql(
            query=query,
            variables={"limit": page_size, "offset": len(jobs)},
        )["jobs"]
        jobs.extend(page)

        if len(page) < page_size:
            return jobs


def fetch_batch_audits(batch_id: UUID, page_size: int = 100) -> list[dict[str, Any]]:
    query = """
    query BatchAudits($batchId: uuid!, $limit: Int!, $offset: Int!) {
        job_requirement_extraction_audits(
            where: {batch_id: {_eq: $batchId}}
            order_by: [{job_id: asc}, {attempt_number: asc}]
            limit: $limit
            offset: $offset
        ) {
            id
            job_id
            status
            duration_ms
            requirements_extracted
            requirements_rejected
            requirements_saved
            extraction_warnings
            error_type
            error_message
            attempt_number
            audit_details
        }
    }
    """
    audits = []
    while True:
        page = call_nhost_graphql(
            query=query,
            variables={
                "batchId": str(batch_id),
                "limit": page_size,
                "offset": len(audits),
            },
        )[AUDIT_TABLE_NAME]
        audits.extend(page)
        if len(page) < page_size:
            return audits


def latest_attempts(audits: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    chosen = {}
    for audit in audits:
        previous = chosen.get(audit["job_id"])
        if previous is None or audit["attempt_number"] > previous["attempt_number"]:
            chosen[audit["job_id"]] = audit
    return chosen


def insert_audit_attempt(
    *,
    batch_id: UUID,
    job: dict[str, Any],
    save_requirements: bool,
    attempt_number: int = 1,
    filter_policy: str = "legacy",
) -> UUID:
    mutation = """
    mutation InsertExtractionAudit(
        $object: job_requirement_extraction_audits_insert_input!
    ) {
        insert_job_requirement_extraction_audits_one(object: $object) {
            id
        }
    }
    """
    description = job.get("job_description") or ""
    result = call_nhost_graphql(
        query=mutation,
        variables={
            "object": {
                "batch_id": str(batch_id),
                "attempt_number": attempt_number,
                "audit_details": extraction_metadata(job, filter_policy),
                "job_id": job["id"],
                "job_title": job.get("job_title"),
                "company": job.get("company"),
                "source": job.get("source"),
                "description_length": len(description),
                "extractor": EXTRACTOR_NAME,
                "prompt_version": LANGEXTRACT_PROMPT_VERSION,
                "model_name": OLLAMA_MODEL,
                "save_requirements": save_requirements,
                "status": "running",
                "started_at": utc_now(),
            }
        },
    )["insert_job_requirement_extraction_audits_one"]
    return UUID(result["id"])


def update_audit_attempt(audit_id: UUID, changes: dict[str, Any]) -> None:
    mutation = """
    mutation UpdateExtractionAudit(
        $auditId: uuid!
        $changes: job_requirement_extraction_audits_set_input!
    ) {
        update_job_requirement_extraction_audits_by_pk(
            pk_columns: {id: $auditId}
            _set: $changes
        ) {
            id
        }
    }
    """
    changes["updated_at"] = utc_now()
    result = call_nhost_graphql(
        query=mutation,
        variables={"auditId": str(audit_id), "changes": changes},
    )["update_job_requirement_extraction_audits_by_pk"]

    if result is None:
        raise RuntimeError(f"Audit row disappeared during update: {audit_id}")


def serialize_error(error: Exception) -> tuple[str, str, dict[str, Any]]:
    error_type = type(error).__name__

    if isinstance(error, HTTPException):
        detail: Any = error.detail
        error_detail = {"status_code": error.status_code, "detail": detail}
    elif isinstance(error, ValidationError):
        error_detail = {"errors": error.errors(include_input=False)}
        detail = error_detail
    else:
        detail = str(error)
        error_detail = {"detail": detail}

    if isinstance(detail, str):
        error_message = detail
    else:
        error_message = json.dumps(detail, ensure_ascii=False, default=str)

    return error_type, error_message[:10000], error_detail


def validate_requirement_records_for_audit(
    *,
    records: list[dict[str, str]],
    job: JobInput,
    warnings: list[dict[str, Any]],
) -> JobRequirements:
    """Validate records independently so one invalid item does not erase the rest."""
    accepted_requirements: list[RequirementItem] = []
    seen_names: set[str] = set()

    for record in records:
        try:
            requirement = RequirementItem.model_validate(record)
            validated = validate_job_requirements(
                JobRequirements(requirements=[requirement]),
                job,
                "LangExtract",
            ).requirements[0]
        except (HTTPException, ValidationError) as error:
            if isinstance(error, HTTPException):
                detail: Any = error.detail
            else:
                detail = {"errors": error.errors(include_input=False)}

            warnings.append(
                {
                    "type": "invalid_requirement_record",
                    "message": (
                        detail if isinstance(detail, str) else json.dumps(detail)
                    ),
                    "model_evidence": record.get("evidence"),
                    "record": record,
                    "action": "rejected",
                    "reason_type": (
                        detail.get(
                            "type",
                            "schema"
                            if isinstance(error, ValidationError)
                            else "validation",
                        )
                        if isinstance(detail, dict)
                        else "grounding"
                    ),
                }
            )
            continue

        normalized_name = validated.name.strip().lower()
        if normalized_name in seen_names:
            warnings.append(
                {
                    "type": "duplicate_requirement",
                    "action": "merged",
                    "message": f"Duplicate requirement rejected: {validated.name}",
                    "model_evidence": validated.evidence,
                    "record": validated.model_dump(mode="json"),
                }
            )
            continue

        seen_names.add(normalized_name)
        accepted_requirements.append(validated)

    if not accepted_requirements:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "LangExtract produced no valid requirement records",
                "warnings": warnings,
            },
        )

    return JobRequirements(requirements=accepted_requirements)


def summarize_batch(audits: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {
        "running": 0,
        "succeeded": 0,
        "succeeded_with_warnings": 0,
        "failed": 0,
    }
    durations: list[int] = []

    for audit in audits:
        counts[audit["status"]] += 1
        if audit["duration_ms"] is not None:
            durations.append(audit["duration_ms"])

    return {
        "total": len(audits),
        **counts,
        "average_duration_ms": (
            round(sum(durations) / len(durations)) if durations else None
        ),
    }


def run_batch(
    *,
    batch_id: UUID,
    save_requirements: bool,
    retry_failed: bool,
    max_jobs: int | None,
    selected_job_ids: set[UUID],
    jobs_file: Path | None = None,
    filter_policy: str = "legacy",
) -> dict[str, Any]:
    if save_requirements and filter_policy == "review":
        raise ValueError("review filter policy is preview-only")
    jobs = load_job_snapshot(jobs_file) if jobs_file else fetch_jobs()

    if selected_job_ids:
        jobs = [job for job in jobs if UUID(job["id"]) in selected_job_ids]

    if max_jobs is not None:
        jobs = jobs[:max_jobs]

    existing_audits = {
        UUID(job_id): audit
        for job_id, audit in latest_attempts(fetch_batch_audits(batch_id)).items()
    }

    print(
        json.dumps(
            {
                "event": "batch_started",
                "batch_id": str(batch_id),
                "jobs_selected": len(jobs),
                "prompt_version": LANGEXTRACT_PROMPT_VERSION,
                "save_requirements": save_requirements,
            }
        ),
        flush=True,
    )

    for position, job in enumerate(jobs, start=1):
        job_id = UUID(job["id"])
        existing = existing_audits.get(job_id)

        if existing is not None:
            old = existing.get("audit_details") or {}
            new = extraction_metadata(job, filter_policy)
            for key in (
                "description_sha256",
                "cleanup_version",
                "code_hash",
                "filter_policy",
                "model_name",
                "model_parameters",
                "extraction_parameters",
            ):
                if old.get(key) != new[key]:
                    raise ValueError(
                        f"Batch input/configuration changed ({key}); use a new batch ID"
                    )

        if existing is not None and existing["status"] in {
            "succeeded",
            "succeeded_with_warnings",
        }:
            continue
        if existing is not None and existing["status"] == "failed" and not retry_failed:
            continue

        if existing is None:
            audit_id = insert_audit_attempt(
                batch_id=batch_id,
                job=job,
                save_requirements=save_requirements,
                filter_policy=filter_policy,
            )
        else:
            audit_id = insert_audit_attempt(
                batch_id=batch_id,
                job=job,
                save_requirements=save_requirements,
                attempt_number=existing["attempt_number"] + 1,
                filter_policy=filter_policy,
            )

        started = time.monotonic()
        extraction_outcome = None
        extraction_warnings = []
        requirements_payload = []
        requirements_saved = 0
        stage = "input"
        details = extraction_metadata(job, filter_policy)
        details["save_status"] = "pending" if save_requirements else "not_requested"

        try:
            job_input = JobInput(
                title=job.get("job_title") or "",
                description=job.get("job_description") or "",
            )
            stage = "extraction"
            extraction_outcome = extract_requirement_records_with_warnings(
                title=job_input.title,
                description=job_input.description,
                model_id=OLLAMA_MODEL,
                model_url=OLLAMA_BASE_URL,
                filter_policy=filter_policy,
            )
            extraction_warnings = list(extraction_outcome.warnings)
            stage = "validation"
            requirements = validate_requirement_records_for_audit(
                records=extraction_outcome.records,
                job=job_input,
                warnings=extraction_warnings,
            )
            requirements_payload = requirements.model_dump(mode="json")["requirements"]
            details.update(outcome_details(extraction_outcome, extraction_warnings))
            details["extraction_status"] = "succeeded"
            details["accepted_records"] = requirements_payload

            details["save_status"] = (
                "skipped_review" if save_requirements else "not_requested"
            )
            # Partial results remain audit-only until a person reviews the warnings.
            if (
                save_requirements
                and not extraction_warnings
                and not any(
                    event["action"] in {"filtered", "review"}
                    for event in extraction_outcome.events
                )
            ):
                stage = "saving"
                saved_result = replace_job_requirements(
                    job_id=job_id,
                    requirements=requirements,
                    prompt_version=LANGEXTRACT_PROMPT_VERSION,
                )
                requirements_saved = saved_result["saved"]["affected_rows"]
                details["save_status"] = "succeeded"

            duration_ms = round((time.monotonic() - started) * 1000)
            status = (
                "succeeded_with_warnings"
                if extraction_warnings
                or any(
                    event["action"] in {"filtered", "review"}
                    for event in extraction_outcome.events
                )
                else "succeeded"
            )
            stage = "audit"
            update_audit_attempt(
                audit_id,
                {
                    "status": status,
                    "audit_details": details,
                    "requirements_filtered": count_actions(
                        extraction_outcome.events, "filtered"
                    ),
                    "completed_at": utc_now(),
                    "duration_ms": duration_ms,
                    "requirements_extracted": len(requirements_payload),
                    "requirements_rejected": count_actions(
                        extraction_warnings, "rejected"
                    ),
                    "requirements_saved": requirements_saved,
                    "extracted_requirements": requirements_payload,
                    "extraction_warnings": extraction_warnings,
                    "error_type": None,
                    "error_message": None,
                    "error_detail": None,
                },
            )
            event = {
                "event": f"job_{status}",
                "position": position,
                "total": len(jobs),
                "job_id": str(job_id),
                "duration_ms": duration_ms,
                "requirements_extracted": len(requirements_payload),
                "requirements_rejected": count_actions(extraction_warnings, "rejected"),
                "requirements_saved": requirements_saved,
                "requirements_filtered": details["filtered_items"],
            }
        # Every job-level exception must be audited without terminating the batch.
        except Exception as error:  # noqa: BLE001
            duration_ms = round((time.monotonic() - started) * 1000)
            error_type, error_message, error_detail = serialize_error(error)
            extraction_outcome = getattr(error, "outcome", None) or extraction_outcome
            if extraction_outcome is not None:
                if not extraction_warnings:
                    extraction_warnings = list(extraction_outcome.warnings)
                details.update(outcome_details(extraction_outcome, extraction_warnings))
            details["failure_stage"] = stage
            details["failure_kind"] = failure_kind(error, stage)
            if stage == "saving":
                details["save_status"] = "failed"
            details["extraction_status"] = (
                "succeeded" if stage in {"saving", "audit"} else "failed"
            )
            details["accepted_records"] = requirements_payload
            update_audit_attempt(
                audit_id,
                {
                    "status": "failed",
                    "completed_at": utc_now(),
                    "duration_ms": duration_ms,
                    "requirements_extracted": len(requirements_payload),
                    "requirements_rejected": count_actions(
                        extraction_warnings, "rejected"
                    ),
                    "requirements_filtered": details.get("filtered_items", 0),
                    "requirements_saved": requirements_saved,
                    "extracted_requirements": requirements_payload,
                    "extraction_warnings": extraction_warnings,
                    "audit_details": details,
                    "error_type": error_type,
                    "error_message": error_message,
                    "error_detail": error_detail,
                },
            )
            event = {
                "event": "job_failed",
                "position": position,
                "total": len(jobs),
                "job_id": str(job_id),
                "duration_ms": duration_ms,
                "error_type": error_type,
                "error_message": error_message,
            }

        print(json.dumps(event, ensure_ascii=False), flush=True)

    attempts = fetch_batch_audits(batch_id)
    chosen = latest_attempts(attempts)
    summary = summarize_batch(list(chosen.values()))
    summary["attempts"] = len(attempts)
    summary["unique_jobs"] = len(chosen)
    summary["retries"] = len(attempts) - len(chosen)
    final_event = {
        "event": "batch_finished",
        "batch_id": str(batch_id),
        **summary,
    }
    print(json.dumps(final_event), flush=True)
    return final_event


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run LangExtract over database jobs and persist one audit result per job."
        )
    )
    parser.add_argument(
        "--jobs-file", type=Path, help="Frozen job snapshot to use instead of live JDs"
    )
    parser.add_argument(
        "--freeze-to",
        type=Path,
        help="Write selected JDs and hashes, then exit without extraction",
    )
    parser.add_argument(
        "--filter-policy", choices=["legacy", "review"], default="legacy"
    )
    parser.add_argument(
        "--batch-id",
        type=UUID,
        default=None,
        help="Resume an existing batch ID. A new UUID is created when omitted.",
    )
    parser.add_argument(
        "--job-id",
        action="append",
        default=[],
        type=UUID,
        help="Run only this job ID. Repeat the option to select multiple jobs.",
    )
    parser.add_argument(
        "--max-jobs",
        type=int,
        default=None,
        help="Limit jobs for a smoke test after filtering.",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Retry failed rows when resuming the same batch ID.",
    )
    parser.add_argument(
        "--save-requirements",
        action="store_true",
        help=(
            "Also replace canonical job_requirements rows on success. By default, "
            "validated output is stored only in the audit table."
        ),
    )
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()

    if arguments.max_jobs is not None and arguments.max_jobs < 1:
        raise SystemExit("--max-jobs must be at least 1")

    if arguments.save_requirements and arguments.filter_policy == "review":
        raise SystemExit("--filter-policy review is preview-only")
    if arguments.freeze_to:
        jobs = (
            load_job_snapshot(arguments.jobs_file)
            if arguments.jobs_file
            else fetch_jobs()
        )
        if arguments.job_id:
            jobs = [job for job in jobs if UUID(job["id"]) in set(arguments.job_id)]
        if arguments.max_jobs:
            jobs = jobs[: arguments.max_jobs]
        freeze_jobs(arguments.freeze_to, jobs)
        return

    run_batch(
        batch_id=arguments.batch_id or uuid4(),
        save_requirements=arguments.save_requirements,
        retry_failed=arguments.retry_failed,
        max_jobs=arguments.max_jobs,
        selected_job_ids=set(arguments.job_id),
        jobs_file=arguments.jobs_file,
        filter_policy=arguments.filter_policy,
    )


if __name__ == "__main__":
    main()
