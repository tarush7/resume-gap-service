import unittest
from unittest.mock import patch

import langextract as lx

from langextract_requirements import (
    REQUIREMENT_EXAMPLES,
    LangExtractRequirementError,
    clean_requirement_records,
    extract_requirement_records,
    find_ignored_boilerplate_ranges,
    find_responsibility_section,
    has_grounded_responsibility,
    requirement_records_from_document,
    requirement_records_from_document_with_warnings,
)


class RequirementRecordsTest(unittest.TestCase):
    def test_examples_use_exact_source_spans(self):
        for example in REQUIREMENT_EXAMPLES:
            for extraction in example.extractions:
                self.assertIn(extraction.extraction_text, example.text)

    def test_converts_interval_to_original_evidence(self):
        source = "Strong SQL skills are required."
        extraction = lx.data.Extraction(
            extraction_class="job_requirement",
            extraction_text="SQL",
            char_interval=lx.data.CharInterval(start_pos=7, end_pos=10),
            attributes={
                "name": "SQL",
                "category": "required_skill",
                "requirement_level": "required",
            },
        )
        document = lx.data.AnnotatedDocument(
            text=source,
            extractions=[extraction],
        )

        self.assertEqual(
            requirement_records_from_document(document, source),
            [
                {
                    "name": "SQL",
                    "category": "required_skill",
                    "requirement_level": "required",
                    "evidence": "SQL",
                }
            ],
        )

    def test_rejects_ungrounded_evidence(self):
        extraction = lx.data.Extraction(
            extraction_class="job_requirement",
            extraction_text="invented evidence",
            attributes={
                "name": "Invented",
                "category": "required_skill",
                "requirement_level": "required",
            },
        )
        document = lx.data.AnnotatedDocument(
            text="Original source",
            extractions=[extraction],
        )

        with self.assertRaises(LangExtractRequirementError):
            requirement_records_from_document(document, "Original source")

    def test_returns_valid_records_and_rejected_items_separately(self):
        source = "Strong SQL skills are required."
        valid_extraction = lx.data.Extraction(
            extraction_class="job_requirement",
            extraction_text="SQL",
            attributes={
                "name": "SQL",
                "category": "required_skill",
                "requirement_level": "required",
            },
        )
        invalid_extraction = lx.data.Extraction(
            extraction_class="job_requirement",
            extraction_text="Python",
            attributes={
                "name": "Python",
                "category": "required_skill",
                "requirement_level": "required",
            },
        )
        document = lx.data.AnnotatedDocument(
            text=source,
            extractions=[valid_extraction, invalid_extraction],
        )

        outcome = requirement_records_from_document_with_warnings(document, source)

        self.assertEqual([record["name"] for record in outcome.records], ["SQL"])
        self.assertEqual(len(outcome.warnings), 1)
        self.assertEqual(outcome.warnings[0]["type"], "ungrounded_evidence")
        self.assertEqual(outcome.warnings[0]["model_evidence"], "Python")

    def test_falls_back_for_unaligned_exact_overlapping_evidence(self):
        source = "Excellent communication and stakeholder management skills."
        extraction = lx.data.Extraction(
            extraction_class="job_requirement",
            extraction_text="stakeholder management",
            attributes={
                "name": "Stakeholder management",
                "category": "required_skill",
                "requirement_level": "required",
            },
        )
        document = lx.data.AnnotatedDocument(
            text=source,
            extractions=[extraction],
        )

        records = requirement_records_from_document(document, source)

        self.assertEqual(records[0]["evidence"], "stakeholder management")

    def test_recovers_model_completion_of_truncated_source_word(self):
        source = (
            "- Contribute to continuous improvement initiatives that enhance "
            "operational effici\n\n**Expertise You'll Bring:**"
        )
        extraction = lx.data.Extraction(
            extraction_class="job_requirement",
            extraction_text=(
                "Contribute to continuous improvement initiatives that enhance "
                "operational efficiency"
            ),
            attributes={
                "name": "Continuous improvement",
                "category": "responsibility",
                "requirement_level": "required",
            },
        )
        document = lx.data.AnnotatedDocument(text=source, extractions=[extraction])

        records = requirement_records_from_document(document, source)

        self.assertEqual(
            records[0]["evidence"],
            "Contribute to continuous improvement initiatives that enhance operational",
        )

    def test_does_not_recover_an_invented_evidence_clause(self):
        source = "Experience working with large-scale datasets\n"
        extraction = lx.data.Extraction(
            extraction_class="job_requirement",
            extraction_text="Experience working with large-scale datasets and Python",
            attributes={
                "name": "Large datasets and Python",
                "category": "required_skill",
                "requirement_level": "required",
            },
        )
        document = lx.data.AnnotatedDocument(text=source, extractions=[extraction])

        with self.assertRaises(LangExtractRequirementError):
            requirement_records_from_document(document, source)

    def test_finds_markdown_role_section_before_qualifications(self):
        source = """Overview
Company description.

**Role**
- Own the product roadmap.
- Partner with engineering.

**All About You**
- Strong communication skills.
"""

        self.assertEqual(
            find_responsibility_section(source),
            "- Own the product roadmap.\n- Partner with engineering.",
        )

    def test_stops_before_combined_qualifications_heading(self):
        source = """**Responsibilities**
- Define the platform roadmap.

**Qualifications** **Required/Minimum Qualifications:**
- Eight years of product experience.
"""

        self.assertEqual(
            find_responsibility_section(source),
            "- Define the platform roadmap.",
        )

    def test_stops_what_youll_do_before_expertise_youll_bring(self):
        source = """**What You'll Do:**

- Define and execute product strategy.

**Expertise You'll Bring:**

- Strong SQL skills.
"""

        section = find_responsibility_section(source)
        self.assertEqual(section, "- Define and execute product strategy.")

        records = [
            {
                "name": "Product strategy",
                "category": "required_skill",
                "requirement_level": "required",
                "evidence": "Define and execute product strategy",
            },
            {
                "name": "SQL",
                "category": "required_skill",
                "requirement_level": "required",
                "evidence": "SQL",
            },
        ]
        cleaned = clean_requirement_records(records, source, section)

        self.assertEqual(cleaned[0]["category"], "required_skill")
        self.assertEqual(cleaned[1]["category"], "required_skill")

    def test_responsibility_must_be_grounded_in_role_section(self):
        section = "- Own the product roadmap."
        mislabeled_trait = [
            {
                "name": "Minimal supervision",
                "category": "responsibility",
                "requirement_level": "required",
                "evidence": "Requires minimal supervision",
            }
        ]

        self.assertFalse(has_grounded_responsibility(mislabeled_trait, section))

    def test_filters_corporate_security_boilerplate(self):
        source = """All About You
- Strong communication skills.

Corporate Security Responsibility
- Complete all mandatory security training.
"""
        records = [
            {
                "name": "Communication",
                "category": "required_skill",
                "requirement_level": "required",
                "evidence": "Strong communication skills",
            },
            {
                "name": "Security training",
                "category": "responsibility",
                "requirement_level": "required",
                "evidence": "Complete all mandatory security training",
            },
        ]

        self.assertTrue(find_ignored_boilerplate_ranges(source))
        self.assertEqual(
            clean_requirement_records(records, source, None),
            [records[0]],
        )

    def test_role_section_preserves_model_category(self):
        source = """Role
- Exhibit expertise and coordinate with interdependent teams.

All About You
- Strong communication skills.
"""
        section = find_responsibility_section(source)
        records = [
            {
                "name": "Platform expertise",
                "category": "required_skill",
                "requirement_level": "required",
                "evidence": (
                    "Exhibit expertise and coordinate with interdependent teams"
                ),
            }
        ]

        cleaned = clean_requirement_records(records, source, section)

        self.assertEqual(cleaned[0]["category"], "required_skill")

    def test_technical_background_preserves_model_classification(self):
        source = "Technical background (e.g., Computer Science) a plus."
        records = [
            {
                "name": "Technical background",
                "category": "education",
                "requirement_level": "preferred",
                "evidence": "Technical background (e.g., Computer Science)",
            }
        ]

        cleaned = clean_requirement_records(records, source, None)

        self.assertEqual(cleaned[0]["category"], "education")

    def test_degree_cleanup_preserves_model_classification(self):
        source = "An advanced degree is helpful."
        records = [
            {
                "name": "Advanced degree",
                "category": "preferred_skill",
                "requirement_level": "preferred",
                "evidence": "An advanced degree is helpful",
            }
        ]

        cleaned = clean_requirement_records(records, source, None)

        self.assertEqual(cleaned[0]["category"], "preferred_skill")
        self.assertEqual(cleaned[0]["requirement_level"], "preferred")

    @patch("langextract_requirements.lx.extract")
    def test_retries_explicit_role_section_when_primary_omits_it(self, mock_extract):
        source = """Role
- Own the product roadmap.

All About You
- Requires minimal supervision.
"""
        trait_evidence = "Requires minimal supervision"
        role_evidence = "Own the product roadmap"
        primary_document = lx.data.AnnotatedDocument(
            text=source,
            extractions=[
                lx.data.Extraction(
                    extraction_class="job_requirement",
                    extraction_text=trait_evidence,
                    char_interval=lx.data.CharInterval(
                        start_pos=source.index(trait_evidence),
                        end_pos=source.index(trait_evidence) + len(trait_evidence),
                    ),
                    attributes={
                        "name": "Minimal supervision",
                        "category": "responsibility",
                        "requirement_level": "required",
                    },
                )
            ],
        )
        responsibility_section = "- Own the product roadmap."
        responsibility_document = lx.data.AnnotatedDocument(
            text=responsibility_section,
            extractions=[
                lx.data.Extraction(
                    extraction_class="job_requirement",
                    extraction_text=role_evidence,
                    char_interval=lx.data.CharInterval(
                        start_pos=responsibility_section.index(role_evidence),
                        end_pos=(
                            responsibility_section.index(role_evidence)
                            + len(role_evidence)
                        ),
                    ),
                    attributes={
                        "name": "Product roadmap ownership",
                        "category": "responsibility",
                        "requirement_level": "required",
                    },
                )
            ],
        )
        mock_extract.side_effect = [primary_document, responsibility_document]

        records = extract_requirement_records(
            title="Product Manager",
            description=source,
            model_id="test-model",
            model_url="http://ollama.test",
        )

        self.assertEqual(mock_extract.call_count, 2)
        self.assertEqual(
            [record["name"] for record in records],
            ["Product roadmap ownership", "Minimal supervision"],
        )


if __name__ == "__main__":
    unittest.main()
