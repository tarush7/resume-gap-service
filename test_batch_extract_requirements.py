import unittest

from fastapi import HTTPException

from batch_extract_requirements import (
    serialize_error,
    summarize_batch,
    validate_requirement_records_for_audit,
)
from main import JobInput


class BatchExtractionTest(unittest.TestCase):
    def test_keeps_valid_records_and_warns_about_invalid_records(self):
        warnings = []
        records = [
            {
                "name": "SQL",
                "category": "required_skill",
                "requirement_level": "required",
                "evidence": "SQL",
            },
            {
                "name": "Python",
                "category": "not_a_real_category",
                "requirement_level": "required",
                "evidence": "Python",
            },
        ]
        job = JobInput(
            title="Product Manager",
            description=(
                "Strong SQL skills are required. Python experience is also required."
            ),
        )

        requirements = validate_requirement_records_for_audit(
            records=records,
            job=job,
            warnings=warnings,
        )

        self.assertEqual(
            [requirement.name for requirement in requirements.requirements],
            ["SQL"],
        )
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["type"], "invalid_requirement_record")

    def test_serializes_http_error_detail(self):
        error = HTTPException(
            status_code=502,
            detail={"message": "Bad model output", "field": "evidence"},
        )

        error_type, error_message, error_detail = serialize_error(error)

        self.assertEqual(error_type, "HTTPException")
        self.assertIn("Bad model output", error_message)
        self.assertEqual(error_detail["status_code"], 502)
        self.assertEqual(error_detail["detail"]["field"], "evidence")

    def test_summarizes_mixed_batch(self):
        audits = [
            {
                "status": "succeeded",
                "duration_ms": 1000,
            },
            {
                "status": "failed",
                "duration_ms": 2000,
            },
            {
                "status": "running",
                "duration_ms": None,
            },
        ]

        self.assertEqual(
            summarize_batch(audits),
            {
                "total": 3,
                "running": 1,
                "succeeded": 1,
                "succeeded_with_warnings": 0,
                "failed": 1,
                "average_duration_ms": 1500,
            },
        )

    def test_summarizes_success_with_warnings(self):
        audits = [
            {
                "status": "succeeded_with_warnings",
                "duration_ms": 1250,
            }
        ]

        self.assertEqual(
            summarize_batch(audits),
            {
                "total": 1,
                "running": 0,
                "succeeded": 0,
                "succeeded_with_warnings": 1,
                "failed": 0,
                "average_duration_ms": 1250,
            },
        )


if __name__ == "__main__":
    unittest.main()
