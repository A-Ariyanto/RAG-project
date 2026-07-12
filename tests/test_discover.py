"""Unit tests for the sitemap corpus filter (no network)."""

from ingestion.discover import _CORPUS_RE, _locs

SITEMAP_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.handbook.unsw.edu.au/undergraduate/courses/2026/COMP1511</loc></url>
  <url><loc>https://www.handbook.unsw.edu.au/undergraduate/specialisations/2026/COMPA1</loc></url>
</urlset>"""


def _match(url):
    return _CORPUS_RE.search(url)


def test_matches_comp_and_seng_courses():
    m = _match("https://www.handbook.unsw.edu.au/undergraduate/courses/2026/COMP1511")
    assert m and m.group("code") == "COMP1511"
    assert m.group("career") == "undergraduate"
    assert m.group("content_type") == "courses"
    assert m.group("year") == "2026"

    m = _match("https://www.handbook.unsw.edu.au/postgraduate/courses/2026/SENG2011")
    assert m and m.group("code") == "SENG2011"


def test_matches_specialisation_codes():
    m = _match("https://www.handbook.unsw.edu.au/undergraduate/specialisations/2026/COMPA1")
    assert m and m.group("code") == "COMPA1"
    assert m.group("content_type") == "specialisations"


def test_rejects_other_subject_areas_and_doc_types():
    # Non-CSE subject area.
    assert _match("https://www.handbook.unsw.edu.au/undergraduate/courses/2026/MATH1131") is None
    # Programs are out of scope for v1.
    assert _match("https://www.handbook.unsw.edu.au/undergraduate/programs/2026/3778") is None
    # A code that merely starts with the letters but isn't the subject area.
    assert _match("https://www.handbook.unsw.edu.au/undergraduate/courses/2026/COMM1000") is None


def test_locs_extracts_urls_from_sitemap_xml():
    assert _locs(SITEMAP_SAMPLE) == [
        "https://www.handbook.unsw.edu.au/undergraduate/courses/2026/COMP1511",
        "https://www.handbook.unsw.edu.au/undergraduate/specialisations/2026/COMPA1",
    ]
