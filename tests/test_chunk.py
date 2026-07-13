"""Unit tests for the structure-aware chunker (pure, no DB, no torch).

Fixtures mirror the real scraped `page_content` shape (a compact synthetic
document rather than a committed copy of scraped handbook content).
"""

import pytest

from ingestion.chunk import chunk_document


def _course_doc() -> dict:
    return {
        "url": "https://www.handbook.unsw.edu.au/undergraduate/courses/2026/COMP3311",
        "code": "COMP3311",
        "career": "undergraduate",
        "content_type": "courses",
        "scraped_at": "2026-07-12T00:00:00+00:00",
        "page_content": {
            "code": "COMP3311",
            "title": "Database Systems",
            "credit_points": "6",
            "description": "<p>An introduction to database systems.</p>",
            "study_level": [{"label": "Undergraduate", "value": "ugrd"}],
            "campus": "Sydney",
            "offering_detail": {"offering_terms": "Term 1, Term 3"},
            "enrolment_rules": [
                {"description": "Prerequisite: COMP2521 or COMP1927<br/><br/>"}
            ],
            "exclusion": [
                {"assoc_code": "COMP9311", "assoc_title": "Database Systems"}
            ],
            "eqivalents": [],
            "unit_learning_outcomes": [
                {"code": "CLO1", "description": "Design a relational schema."}
            ],
            "additional_information": "",
            "notes": "",
        },
    }


def _by_section(chunks):
    out: dict[str, list] = {}
    for c in chunks:
        out.setdefault(c.section_type, []).append(c)
    return out


def test_course_produces_expected_sections():
    sections = _by_section(chunk_document(_course_doc()))
    assert set(sections) == {"overview", "enrolment_conditions", "offering", "learning_outcomes"}


def test_every_chunk_is_prefixed_and_nonempty():
    for c in chunk_document(_course_doc()):
        assert c.text.strip()
        assert c.text.startswith("COMP3311 Database Systems — ")


def test_offering_parses_terms_and_uoc():
    (offering,) = _by_section(chunk_document(_course_doc()))["offering"]
    assert offering.offering_terms == ["T1", "T3"]
    assert offering.credit_points == 6


def test_prerequisite_chunk_carries_rule_metadata():
    enrol = _by_section(chunk_document(_course_doc()))["enrolment_conditions"]
    prereq = next(c for c in enrol if c.rule_type == "prerequisite")
    assert prereq.referenced_codes == ["COMP2521", "COMP1927"]
    assert "Prerequisite: COMP2521 or COMP1927" in prereq.text


def test_structured_exclusion_emitted_when_not_in_free_text():
    enrol = _by_section(chunk_document(_course_doc()))["enrolment_conditions"]
    exclusion = next(c for c in enrol if c.rule_type == "exclusion")
    assert exclusion.referenced_codes == ["COMP9311"]
    assert "Exclusion: COMP9311" in exclusion.text


def test_empty_sections_are_skipped():
    doc = _course_doc()
    doc["page_content"]["unit_learning_outcomes"] = []
    doc["page_content"]["description"] = ""
    sections = _by_section(chunk_document(doc))
    assert "learning_outcomes" not in sections
    assert "overview" not in sections


def _specialisation_doc() -> dict:
    return {
        "url": "https://www.handbook.unsw.edu.au/undergraduate/specialisations/2026/COMPA1",
        "code": "COMPA1",
        "career": "undergraduate",
        "content_type": "specialisations",
        "scraped_at": "2026-07-12T00:00:00+00:00",
        "page_content": {
            "code": "COMPA1",
            "title": "Computer Science",
            "credit_points": "96",
            "description": "<p>The Computer Science major.</p>",
            "study_level": [{"label": "Undergraduate", "value": "ugrd"}],
            "structure_summary": "<p>Students must complete 96 UOC.</p>",
            "curriculumStructure": {
                "container": [
                    {
                        "title": "Core Courses",
                        "credit_points": "66",
                        "relationship": [
                            {"child_record": {"value": "Course: COMP1511"}},
                            {"child_record": {"value": "Course: COMP2521"}},
                        ],
                        "container": [],
                    }
                ]
            },
        },
    }


def test_specialisation_structure_chunk_flattens_curriculum():
    sections = _by_section(chunk_document(_specialisation_doc()))
    assert "structure" in sections
    (structure,) = sections["structure"]
    assert "Students must complete 96 UOC" in structure.text
    assert "Core Courses (66 UOC)" in structure.text
    assert structure.referenced_codes == ["COMP1511", "COMP2521"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
