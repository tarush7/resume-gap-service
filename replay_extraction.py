"""Replay captured LangExtract items through cleanup and shared validation offline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import langextract as lx
from fastapi import HTTPException
from pydantic import ValidationError

from batch_extract_requirements import (
    description_hash,
    outcome_details,
    validate_requirement_records_for_audit,
)
from langextract_requirements import (
    LangExtractRequirementError,
    extract_requirement_records_with_warnings,
)
from main import JobInput


def replay_audit(audit: dict, filter_policy: str = "legacy") -> dict:
    details = audit["audit_details"]
    if "raw_extractions" not in details:
        raise ValueError(
            "This audit has no captured pre-cleanup model output; rerun extraction"
        )
    description = details["job_description"]
    if description_hash(description) != details["description_sha256"]:
        raise ValueError("JD hash mismatch")
    job = JobInput(title=audit["job_title"], description=description)

    def run_document(*, text, context_label, **_kwargs):
        items = [
            item
            for item in details["raw_extractions"]
            if item["context_label"] == context_label
        ]
        if not items:
            raise ValueError(
                f"No captured items for {context_label}; a new extraction is required"
            )
        if any(item["source_text"] != text for item in items):
            raise ValueError("Replay source differs from captured source")
        return lx.data.AnnotatedDocument(
            text=text,
            extractions=[
                lx.data.Extraction(
                    extraction_class=item["extraction_class"],
                    extraction_text=item["extraction_text"],
                    attributes=item["attributes"],
                    char_interval=(
                        lx.data.CharInterval(**item["char_interval"])
                        if item["char_interval"] is not None
                        else None
                    ),
                )
                for item in items
            ],
        )

    outcome = None
    warnings = []
    try:
        outcome = extract_requirement_records_with_warnings(
            title=job.title,
            description=description,
            model_id="offline-replay",
            model_url="offline-replay",
            filter_policy=filter_policy,
            _document_runner=run_document,
        )
        warnings = list(outcome.warnings)
        validated = validate_requirement_records_for_audit(
            records=outcome.records,
            job=job,
            warnings=warnings,
        )
        return {
            "status": "succeeded",
            "filter_policy": filter_policy,
            "requirements_saved": 0,
            "requirements": validated.model_dump()["requirements"],
            **outcome_details(outcome, warnings),
        }
    except (
        LangExtractRequirementError,
        HTTPException,
        ValidationError,
        ValueError,
    ) as error:
        outcome = getattr(error, "outcome", None) or outcome
        if outcome is not None and not warnings:
            warnings = list(outcome.warnings)
        return {
            "status": "failed",
            "filter_policy": filter_policy,
            "requirements_saved": 0,
            "error": str(error),
            **(outcome_details(outcome, warnings) if outcome else {}),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audit", type=Path, help="One exported audit row as JSON")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit = json.loads(args.audit.read_text())
    comparison = {
        policy: replay_audit(audit, policy) for policy in ("legacy", "review")
    }
    with args.output.open("x") as output:
        json.dump(comparison, output, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
