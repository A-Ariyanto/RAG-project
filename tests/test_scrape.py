"""Unit tests for __NEXT_DATA__ payload extraction (no network)."""

import json

import pytest

from ingestion.discover import CorpusEntry
from ingestion.scrape import ScrapeError, _output_path, extract_page_content

from pathlib import Path


def _page(next_data: dict) -> str:
    blob = json.dumps(next_data)
    return (
        "<html><head>"
        f'<script id="__NEXT_DATA__" type="application/json">{blob}</script>'
        "</head><body>rendered content</body></html>"
    )


def test_extracts_page_content_object():
    html = _page({"props": {"pageProps": {"pageContent": {"code": "COMP1511", "credit_points": "6"}}}})
    content = extract_page_content(html)
    assert content == {"code": "COMP1511", "credit_points": "6"}


def test_raises_when_script_tag_missing():
    with pytest.raises(ScrapeError, match="no __NEXT_DATA__"):
        extract_page_content("<html><body>no data here</body></html>")


def test_raises_when_shape_unexpected():
    html = _page({"props": {"pageProps": {}}})  # no pageContent key
    with pytest.raises(ScrapeError, match="unexpected __NEXT_DATA__ shape"):
        extract_page_content(html)


def test_output_path_disambiguates_by_career():
    ug = CorpusEntry("u1", "COMP9020", "undergraduate", "courses", "2026")
    pg = CorpusEntry("u2", "COMP9020", "postgraduate", "courses", "2026")
    assert _output_path(Path("data/raw"), ug) == Path("data/raw/COMP9020_undergraduate.json")
    assert _output_path(Path("data/raw"), pg) == Path("data/raw/COMP9020_postgraduate.json")
