from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import ValidationError

from langextract_requirements import (
    LANGEXTRACT_PROMPT_VERSION,
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


def fetch_batch_audits(batch_id: UUID) -> list[dict[str, Any]]:
    query = """
    query BatchAudits($batchId: uuid!) {
        job_requirement_extraction_audits(
            where: {batch_id: {_eq: $batchId}}
            order_by: {created_at: asc}
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
        }
    }
    """
    return call_nhost_graphql(
        query=query,
        variables={"batchId": str(batch_id)},
    )[AUDIT_TABLE_NAME]


def insert_audit_attempt(
    *,
    batch_id: UUID,
    job: dict[str, Any],
    save_requirements: bool,
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


def restart_audit_attempt(audit_id: UUID) -> None:
    update_audit_attempt(
        audit_id,
        {
            "status": "running",
            "started_at": utc_now(),
            "completed_at": None,
            "duration_ms": None,
            "requirements_extracted": None,
            "requirements_rejected": 0,
            "requirements_saved": None,
            "extracted_requirements": None,
            "extraction_warnings": [],
            "error_type": None,
            "error_message": None,
            "error_detail": None,
        },
    )


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
                }
            )
            continue

        normalized_name = validated.name.strip().lower()
        if normalized_name in seen_names:
            warnings.append(
                {
                    "type": "duplicate_requirement",
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
) -> dict[str, Any]:
    jobs = fetch_jobs()

    if selected_job_ids:
        jobs = [job for job in jobs if UUID(job["id"]) in selected_job_ids]

    if max_jobs is not None:
        jobs = jobs[:max_jobs]

    existing_audits = {
        UUID(audit["job_id"]): audit for audit in fetch_batch_audits(batch_id)
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
            )
        else:
            audit_id = UUID(existing["id"])
            restart_audit_attempt(audit_id)

        started = time.monotonic()

        try:
            job_input = JobInput(
                title=job.get("job_title") or "",
                description=job.get("job_description") or "",
            )
            extraction_outcome = extract_requirement_records_with_warnings(
                title=job_input.title,
                description=job_input.description,
                model_id=OLLAMA_MODEL,
                model_url=OLLAMA_BASE_URL,
            )
            extraction_warnings = list(extraction_outcome.warnings)
            requirements = validate_requirement_records_for_audit(
                records=extraction_outcome.records,
                job=job_input,
                warnings=extraction_warnings,
            )
            requirements_payload = requirements.model_dump(mode="json")["requirements"]
            requirements_saved: int | None = None

            # Partial results remain audit-only until a person reviews the warnings.
            if save_requirements and not extraction_warnings:
                saved_result = replace_job_requirements(
                    job_id=job_id,
                    requirements=requirements,
                    prompt_version=LANGEXTRACT_PROMPT_VERSION,
                )
                requirements_saved = saved_result["saved"]["affected_rows"]

            duration_ms = round((time.monotonic() - started) * 1000)
            status = (
                "succeeded_with_warnings"
                if extraction_warnings
                else "succeeded"
            )
            update_audit_attempt(
                audit_id,
                {
                    "status": status,
                    "completed_at": utc_now(),
                    "duration_ms": duration_ms,
                    "requirements_extracted": len(requirements_payload),
                    "requirements_rejected": len(extraction_warnings),
                    "requirements_saved": requirements_saved,
                    "extracted_requirements": requirements_payload,
                    "extraction_warnings": extraction_warnings,
                    "error_type": None,
                    "error_message": None,
                    "error_detail": None,
                },
            )
            event = {
                "event": (
                    "job_succeeded_with_warnings"
                    if extraction_warnings
                    else "job_succeeded"
                ),
                "position": position,
                "total": len(jobs),
                "job_id": str(job_id),
                "duration_ms": duration_ms,
                "requirements_extracted": len(requirements_payload),
                "requirements_rejected": len(extraction_warnings),
                "requirements_saved": requirements_saved,
            }
        # Every job-level exception must be audited without terminating the batch.
        except Exception as error:  # noqa: BLE001
            duration_ms = round((time.monotonic() - started) * 1000)
            error_type, error_message, error_detail = serialize_error(error)
            update_audit_attempt(
                audit_id,
                {
                    "status": "failed",
                    "completed_at": utc_now(),
                    "duration_ms": duration_ms,
                    "requirements_extracted": None,
                    "requirements_rejected": 0,
                    "requirements_saved": None,
                    "extracted_requirements": None,
                    "extraction_warnings": [],
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

    summary = summarize_batch(fetch_batch_audits(batch_id))
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

    run_batch(
        batch_id=arguments.batch_id or uuid4(),
        save_requirements=arguments.save_requirements,
        retry_failed=arguments.retry_failed,
        max_jobs=arguments.max_jobs,
        selected_job_ids=set(arguments.job_id),
    )


if __name__ == "__main__":
    main()
