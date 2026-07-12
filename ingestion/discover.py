"""Discover the CSE corpus URL list from the UNSW Handbook sitemap.

The handbook is a Next.js site; robots.txt advertises a sitemap index at
`/sitemap.xml` that points to per-shard sitemaps listing every course, program,
and specialisation URL. We filter that to the v1 corpus: COMP/SENG **courses**
and **specialisations** for a given handbook year, across all careers
(undergraduate / postgraduate / research).

Using the sitemap (rather than the CloudFront-gated CourseLoop search API) keeps
enumeration reliable and polite — it's the source robots.txt tells us to use.

Run standalone to preview the list:
    python -m ingestion.discover --year 2026
"""

from __future__ import annotations

import argparse
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import requests

from ingestion.net import build_session

SITEMAP_INDEX = "https://www.handbook.unsw.edu.au/sitemap.xml"
_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"

# CSE v1 corpus: a handbook URL for a COMP/SENG course or specialisation.
# Course codes look like COMP1511 / SENG2011; specialisation codes like
# COMPA1 / SENGAH — both share the COMP/SENG subject-area prefix.
_CORPUS_RE = re.compile(
    r"/(?P<career>undergraduate|postgraduate|research)"
    r"/(?P<content_type>courses|specialisations)"
    r"/(?P<year>\d{4})"
    r"/(?P<code>(?:COMP|SENG)[A-Z0-9]+)$"
)


@dataclass(frozen=True)
class CorpusEntry:
    """One document to scrape, with the provenance we carry through to disk."""

    url: str
    code: str
    career: str
    content_type: str
    year: str


def _locs(xml_text: str) -> list[str]:
    """Every <loc> URL in a sitemap or sitemap-index document."""
    root = ET.fromstring(xml_text)
    return [el.text.strip() for el in root.iter(f"{_SITEMAP_NS}loc") if el.text]


def _all_sitemap_urls(session: requests.Session) -> list[str]:
    """Walk the sitemap index and return every page URL across all shards."""
    index = session.get(SITEMAP_INDEX, timeout=30)
    index.raise_for_status()

    urls: list[str] = []
    for shard_url in _locs(index.text):
        shard = session.get(shard_url, timeout=30)
        shard.raise_for_status()
        urls.extend(_locs(shard.text))
    return urls


def discover_corpus(session: requests.Session, year: int) -> list[CorpusEntry]:
    """Filter the sitemap to the COMP/SENG course + specialisation corpus."""
    entries: list[CorpusEntry] = []
    for url in _all_sitemap_urls(session):
        m = _CORPUS_RE.search(url)
        if m and m.group("year") == str(year):
            entries.append(CorpusEntry(url=url, **{k: m.group(k) for k in ("code", "career", "content_type", "year")}))
    # Deterministic order, and defend against any duplicate <loc> entries.
    unique = {e.url: e for e in entries}
    return sorted(unique.values(), key=lambda e: (e.content_type, e.code, e.career))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2026, help="Handbook year (default: 2026)")
    args = parser.parse_args()

    session = build_session()
    entries = discover_corpus(session, args.year)

    courses = sum(1 for e in entries if e.content_type == "courses")
    specs = sum(1 for e in entries if e.content_type == "specialisations")
    print(f"Discovered {len(entries)} documents for {args.year}: {courses} courses, {specs} specialisations")
    for e in entries:
        print(f"  {e.content_type:15} {e.career:15} {e.code:10} {e.url}")


if __name__ == "__main__":
    main()
