import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import langextract as lx
from fastapi import HTTPException
from fastapi.testclient import TestClient

from batch_extract_requirements import (
    description_hash,
    extraction_metadata,
    fetch_batch_audits,
    freeze_jobs,
    latest_attempts,
    load_job_snapshot,
    outcome_details,
    run_batch,
    validate_requirement_records_for_audit,
)
from langextract_requirements import (
    LangExtractRequirementError,
    RequirementExtractionOutcome,
    clean_requirement_records,
    extract_requirement_records,
    extract_requirement_records_with_warnings,
    find_responsibility_section,
)
from main import JobInput, JobRequirements, app, validate_job_requirements
from replay_extraction import replay_audit


def record(name, category, level, evidence):
    return {
        "name": name,
        "category": category,
        "requirement_level": level,
        "evidence": evidence,
    }


def document(source, records, aligned=True):
    return lx.data.AnnotatedDocument(
        text=source,
        extractions=[
            lx.data.Extraction(
                extraction_class="job_requirement",
                extraction_text=item["evidence"],
                attributes={
                    key: item[key] for key in ("name", "category", "requirement_level")
                },
                char_interval=(
                    lx.data.CharInterval(
                        start_pos=source.index(item["evidence"]),
                        end_pos=source.index(item["evidence"]) + len(item["evidence"]),
                    )
                    if aligned
                    else None
                ),
            )
            for item in records
        ],
    )


class ChangeLogRegressionTest(unittest.TestCase):
    def test_cleanup_preserves_all_recorded_classifications_and_evidence(self):
        cases = [
            (
                "Responsibilities\n",
                "A/B testing",
                "preferred_skill",
                "preferred",
                "Experience supporting A/B testing is preferred.",
            ),
            ("Responsibilities\n", "MBA", "education", "preferred", "MBA preferred"),
            (
                "Responsibilities\n",
                "Backlog",
                "responsibility",
                "required",
                "Own and manage the product backlog",
            ),
            (
                "Requirements\n",
                "SQL",
                "required_skill",
                "required",
                "Ability to master SQL is required.",
            ),
            (
                "Requirements\n",
                "Scrum Master",
                "preferred_skill",
                "preferred",
                "Scrum Master skills are preferred.",
            ),
            (
                "Requirements\n",
                "Mastercard expertise",
                "required_skill",
                "required",
                "Experience with Mastercard payment products is required.",
            ),
            (
                "Requirements\n",
                "Master's degree",
                "education",
                "required",
                "A master's degree is required.",
            ),
            (
                "Requirements\n",
                "Technical background",
                "education",
                "unspecified",
                "Technical background",
            ),
        ]
        for heading, name, category, level, evidence in cases:
            with self.subTest(name=name):
                source = heading + evidence
                original = record(name, category, level, evidence)
                self.assertEqual(
                    clean_requirement_records(
                        [original], source, find_responsibility_section(source)
                    ),
                    [original],
                )
                self.assertEqual(
                    validate_job_requirements(
                        JobRequirements(requirements=[original]),
                        JobInput(title="Product Manager", description=source.ljust(30)),
                        "test",
                    ).model_dump()["requirements"],
                    [original],
                )

    def test_ambiguous_skill_level_is_rejected_without_mutation_in_both_paths(self):
        source = "Technical background is mentioned for this position."
        for category, level in (
            ("required_skill", "unspecified"),
            ("required_skill", "preferred"),
            ("preferred_skill", "required"),
            ("preferred_skill", "unspecified"),
        ):
            with self.subTest(category=category, level=level):
                item = record(
                    "Technical background", category, level, "Technical background"
                )
                requirements = JobRequirements(requirements=[item])
                with self.assertRaises(HTTPException) as caught:
                    validate_job_requirements(
                        requirements,
                        JobInput(title="Engineer", description=source),
                        "test",
                    )
                self.assertEqual(
                    caught.exception.detail["type"], "inconsistent_category_level"
                )
                self.assertEqual(requirements.model_dump()["requirements"], [item])
                warnings = []
                valid = record("Position", "responsibility", "required", "position")
                validated = validate_requirement_records_for_audit(
                    records=[item, valid],
                    job=JobInput(title="Engineer", description=source),
                    warnings=warnings,
                )
                self.assertEqual(len(validated.requirements), 1)
                self.assertEqual(warnings[0]["record"], item)
                self.assertEqual(
                    warnings[0]["reason_type"], "inconsistent_category_level"
                )

    def test_filters_are_separate_events_and_review_retains_requirements(self):
        cases = [
            ("Disclaimer", "SQL is required for this role.", "SQL", "required_skill"),
            (
                "Disclaimer",
                "CISSP certification is required for this role.",
                "CISSP",
                "certification",
            ),
            (
                "Corporate Security Responsibility",
                "Perform security compliance audits.",
                "Security audits",
                "responsibility",
            ),
            (
                "Corporate Security Responsibility",
                "Complete all mandatory security training.",
                "Training",
                "responsibility",
            ),
        ]
        for heading, evidence, name, category in cases:
            source = f"{heading}\n{evidence}"
            item = record(name, category, "required", evidence)
            events = []
            self.assertEqual(
                clean_requirement_records([item], source, None, events=events), []
            )
            self.assertEqual(events[0]["action"], "filtered")
            self.assertEqual(events[0]["record"], item)
            self.assertEqual(events[0]["matched_sections"][0]["heading"], heading)
            events = []
            self.assertEqual(
                clean_requirement_records(
                    [item], source, None, events=events, filter_policy="review"
                ),
                [item],
            )
            self.assertEqual(events[0]["action"], "review")

    def test_repeated_evidence_uses_aligned_occurrence_or_retains_ambiguity(self):
        source = "Requirements\nSQL is required.\nDisclaimer\nSQL is required.\n"
        item = record("SQL", "required_skill", "required", "SQL is required.")
        self.assertEqual(clean_requirement_records([item], source, None), [item])
        for position, expected in (
            (source.index(item["evidence"]), [item]),
            (source.rindex(item["evidence"]), []),
        ):
            self.assertEqual(
                clean_requirement_records(
                    [item],
                    source,
                    None,
                    source_intervals=[(position, position + len(item["evidence"]))],
                ),
                expected,
            )

    @patch("langextract_requirements.lx.extract")
    def test_all_filtered_failure_preserves_replayable_model_items(self, extract):
        source = "Disclaimer\nSQL is required for this position."
        item = record("SQL", "required_skill", "required", "SQL")
        extract.return_value = document(source, [item])
        with self.assertRaises(LangExtractRequirementError) as caught:
            extract_requirement_records_with_warnings(
                title="Engineer", description=source, model_id="test", model_url="test"
            )
        outcome = caught.exception.outcome
        self.assertEqual(outcome.pre_cleanup_records, [item])
        self.assertEqual(len(outcome.raw_extractions), 1)
        self.assertEqual(outcome_details(outcome, [])["filtered_items"], 1)
        audit = {
            "job_title": "Engineer",
            "audit_details": {
                "job_description": source,
                "description_sha256": description_hash(source),
                "raw_extractions": outcome.raw_extractions,
            },
        }
        replay = replay_audit(audit, "review")
        self.assertEqual(replay["requirements"], [item])
        self.assertEqual(replay["requirements_saved"], 0)
        self.assertEqual(extract.call_count, 1)  # Replay performs no model request.

    @patch("langextract_requirements.lx.extract")
    def test_api_preview_exercises_grounding_cleanup_and_shared_validation(
        self, extract
    ):
        source = (
            "Responsibilities\nExperience supporting A/B testing is preferred.\n"
            "MBA preferred.\nOwn and manage the product backlog.\n"
            "Requirements\nAbility to master SQL is required."
        )
        records = [
            record(
                "A/B testing",
                "preferred_skill",
                "preferred",
                "Experience supporting A/B testing is preferred.",
            ),
            record("MBA", "education", "preferred", "MBA preferred"),
            record(
                "Backlog",
                "responsibility",
                "required",
                "Own and manage the product backlog",
            ),
            record(
                "SQL",
                "required_skill",
                "required",
                "Ability to master SQL is required.",
            ),
        ]
        extract.return_value = document(source, records)
        with (
            patch(
                "main.fetch_database_job",
                return_value={
                    "job_title": "Product Manager",
                    "job_description": source,
                    "company": "Test",
                },
            ),
            patch("main.replace_job_requirements") as save,
        ):
            response = TestClient(app).post(
                f"/database/jobs/{uuid4()}/extract-requirements/langextract/preview"
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["requirements"], records)
        save.assert_not_called()

    @patch("langextract_requirements.lx.extract")
    def test_strict_api_reports_filtering_instead_of_returning_partial_results(
        self, extract
    ):
        source = "Requirements\nStrong SQL skills.\nDisclaimer\nCISSP certification required."
        records = [
            record("SQL", "required_skill", "required", "SQL"),
            record("CISSP", "certification", "required", "CISSP"),
        ]
        extract.return_value = document(source, records)
        with self.assertRaises(LangExtractRequirementError) as caught:
            extract_requirement_records(
                title="Engineer", description=source, model_id="test", model_url="test"
            )
        self.assertEqual(caught.exception.outcome.events[-1]["action"], "filtered")

    def test_snapshot_hash_and_no_overwrite(self):
        job = {"id": str(uuid4()), "job_description": "Original JD"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.json"
            freeze_jobs(path, [job])
            self.assertEqual(
                load_job_snapshot(path)[0]["job_description"], "Original JD"
            )
            with self.assertRaises(FileExistsError):
                freeze_jobs(path, [job])
            payload = json.loads(path.read_text())
            payload["jobs"][0]["job_description"] = "changed"
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                load_job_snapshot(path)

    def test_save_failure_preserves_extraction_and_preview_saves_nothing(self):
        job = {
            "id": str(uuid4()),
            "job_title": "Engineer",
            "job_description": "Strong SQL skills are required for this role.",
        }
        item = record("SQL", "required_skill", "required", "SQL")
        outcome = RequirementExtractionOutcome(records=[item], warnings=[])
        for should_save in (False, True):
            updates = []
            with (
                patch("batch_extract_requirements.fetch_jobs", return_value=[job]),
                patch("batch_extract_requirements.fetch_batch_audits", return_value=[]),
                patch(
                    "batch_extract_requirements.insert_audit_attempt",
                    return_value=uuid4(),
                ),
                patch(
                    "batch_extract_requirements.update_audit_attempt",
                    side_effect=lambda _, changes, updates=updates: updates.append(
                        copy.deepcopy(changes)
                    ),
                ),
                patch(
                    "batch_extract_requirements.extract_requirement_records_with_warnings",
                    return_value=outcome,
                ),
                patch(
                    "batch_extract_requirements.replace_job_requirements",
                    side_effect=RuntimeError("save failed"),
                ) as save,
                redirect_stdout(io.StringIO()),
            ):
                run_batch(
                    batch_id=uuid4(),
                    save_requirements=should_save,
                    retry_failed=False,
                    max_jobs=None,
                    selected_job_ids=set(),
                )
            final = updates[-1]
            self.assertEqual(final["extracted_requirements"], [item])
            self.assertEqual(final["requirements_saved"], 0)
            if should_save:
                self.assertEqual(final["status"], "failed")
                self.assertEqual(
                    final["audit_details"]["extraction_status"], "succeeded"
                )
                self.assertEqual(final["audit_details"]["failure_kind"], "saving")
            else:
                save.assert_not_called()
                self.assertEqual(final["status"], "succeeded")

    @patch("langextract_requirements.lx.extract")
    def test_stage_counts_account_for_filter_rejection_and_merge_separately(
        self, extract
    ):
        source = (
            "Requirements\nSQL and Python are required.\nDisclaimer\nCISSP is required."
        )
        sql = record("SQL", "required_skill", "required", "SQL")
        invalid = record("Python", "invalid_category", "required", "Python")
        certification = record("CISSP", "certification", "required", "CISSP")
        primary = document(source, [sql, sql, invalid, certification])
        primary.extractions.append(
            lx.data.Extraction(
                extraction_class="job_requirement",
                extraction_text="invented evidence",
                attributes={
                    "name": "Invented",
                    "category": "required_skill",
                    "requirement_level": "required",
                },
            )
        )
        extract.return_value = primary
        outcome = extract_requirement_records_with_warnings(
            title="Engineer", description=source, model_id="test", model_url="test"
        )
        warnings = list(outcome.warnings)
        accepted = validate_requirement_records_for_audit(
            records=outcome.records,
            job=JobInput(title="Engineer", description=source),
            warnings=warnings,
        )
        details = outcome_details(outcome, warnings)
        self.assertEqual(len(accepted.requirements), 1)
        self.assertEqual(details["raw_items"], 5)
        self.assertEqual(details["rejected_items"], 2)
        self.assertEqual(details["filtered_items"], 1)
        self.assertEqual(details["merged_duplicates"], 1)
        self.assertEqual(details["ungrounded_items"], 1)
        self.assertEqual(details["structural_rejections"], 1)
        self.assertEqual(
            len(accepted.requirements)
            + details["rejected_items"]
            + details["filtered_items"]
            + details["merged_duplicates"],
            details["raw_items"],
        )

    def test_latest_attempt_is_chosen_by_number_instead_of_list_order(self):
        rows = [
            {"job_id": "a", "attempt_number": 2},
            {"job_id": "b", "attempt_number": 1},
            {"job_id": "a", "attempt_number": 1},
        ]
        self.assertEqual(latest_attempts(rows), {"a": rows[0], "b": rows[1]})

    @patch("batch_extract_requirements.call_nhost_graphql")
    def test_attempts_are_paginated(self, graphql):
        graphql.side_effect = [
            {"job_requirement_extraction_audits": [{"id": 1}, {"id": 2}]},
            {"job_requirement_extraction_audits": [{"id": 3}]},
        ]
        self.assertEqual(len(fetch_batch_audits(uuid4(), page_size=2)), 3)
        self.assertEqual(graphql.call_args.kwargs["variables"]["offset"], 2)

    def test_retry_appends_attempt_and_flags_prevent_saving(self):
        job = {
            "id": str(uuid4()),
            "job_title": "Engineer",
            "job_description": "Strong SQL skills are required for this role.",
        }
        sql = record("SQL", "required_skill", "required", "SQL")
        outcome = RequirementExtractionOutcome(
            records=[sql],
            warnings=[],
            events=[
                {"type": "boilerplate_section", "action": "filtered", "record": sql}
            ],
        )
        previous = {
            "id": str(uuid4()),
            "job_id": job["id"],
            "status": "failed",
            "attempt_number": 1,
            "duration_ms": 1,
            "audit_details": extraction_metadata(job, "legacy"),
        }
        updates = []
        with (
            patch("batch_extract_requirements.fetch_jobs", return_value=[job]),
            patch(
                "batch_extract_requirements.fetch_batch_audits", return_value=[previous]
            ),
            patch(
                "batch_extract_requirements.insert_audit_attempt", return_value=uuid4()
            ) as insert,
            patch(
                "batch_extract_requirements.update_audit_attempt",
                side_effect=lambda _, changes: updates.append(changes),
            ),
            patch(
                "batch_extract_requirements.extract_requirement_records_with_warnings",
                return_value=outcome,
            ),
            patch("batch_extract_requirements.replace_job_requirements") as save,
            redirect_stdout(io.StringIO()),
        ):
            run_batch(
                batch_id=uuid4(),
                save_requirements=True,
                retry_failed=True,
                max_jobs=None,
                selected_job_ids=set(),
            )
        self.assertEqual(insert.call_args.kwargs["attempt_number"], 2)
        self.assertEqual(updates[-1]["status"], "succeeded_with_warnings")
        self.assertEqual(updates[-1]["requirements_filtered"], 1)
        self.assertEqual(updates[-1]["requirements_rejected"], 0)
        self.assertEqual(updates[-1]["audit_details"]["save_status"], "skipped_review")
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
