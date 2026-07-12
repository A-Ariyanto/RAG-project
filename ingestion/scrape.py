"""Scrape raw handbook documents for the CSE corpus.

For each URL from `discover`, fetch the page, pull the Next.js `__NEXT_DATA__`
JSON blob, and persist its academic-item payload (`props.pageProps.pageContent`)
plus provenance metadata as one JSON file per document. Raw-on-disk means Phase 2
re-parsing (chunking, enrolment-rule parsing) never needs the network again.

Polite by construction: single-threaded, rate-limited, retries with backoff
(see `net.py`), identifies itself via User-Agent, and skips already-downloaded
files unless `--refresh` is given — so reruns are cheap and incremental.

Run in the app container:
    docker compose exec app python -m ingestion.scrape --year 2026

For a quick smoke test, limit the count:
    docker compose exec app python -m ingestion.scrape --year 2026 --limit 3
"""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from ingestion.discover import CorpusEntry, discover_corpus
from ingestion.net import build_session

# The whole SSR payload lives in a single <script id="__NEXT_DATA__"> tag whose
# body is one line of JSON. Next.js escapes any "</script>" inside it, so a
# non-greedy match up to the closing tag is safe.
_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(?P<json>.*?)</script>',
    re.DOTALL,
)


class ScrapeError(RuntimeError):
    """Raised when a page doesn't contain the expected __NEXT_DATA__ payload."""


def extract_page_content(html: str) -> dict:
    """Pull `props.pageProps.pageContent` (the academic-item object) from a page."""
    m = _NEXT_DATA_RE.search(html)
    if not m:
        raise ScrapeError("no __NEXT_DATA__ script tag found")
    data = json.loads(m.group("json"))
    try:
        return data["props"]["pageProps"]["pageContent"]
    except (KeyError, TypeError) as exc:
        raise ScrapeError(f"unexpected __NEXT_DATA__ shape: {exc}") from exc


def _output_path(out_dir: Path, entry: CorpusEntry) -> Path:
    # (code, career) is unique: specialisation codes (COMPA1) never collide with
    # course codes (COMP1511), and career disambiguates a code offered at two levels.
    return out_dir / f"{entry.code}_{entry.career}.json"


def scrape_entry(
    session: requests.Session, entry: CorpusEntry, out_dir: Path, *, refresh: bool
) -> str:
    """Fetch and persist one document. Returns 'skipped', 'ok', or raises."""
    path = _output_path(out_dir, entry)
    if path.exists() and not refresh:
        return "skipped"

    resp = session.get(entry.url, timeout=30)
    resp.raise_for_status()
    page_content = extract_page_content(resp.text)

    document = {
        "url": entry.url,
        "code": entry.code,
        "career": entry.career,
        "content_type": entry.content_type,
        "year": entry.year,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "http_status": resp.status_code,
        "page_content": page_content,
    }
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")
    return "ok"


def run(year: int, out_dir: Path, delay: float, limit: int | None, refresh: bool) -> int:
    """Discover, then scrape the corpus. Returns the number of failures."""
    out_dir.mkdir(parents=True, exist_ok=True)
    session = build_session()

    print(f"Discovering corpus for {year}…")
    entries = discover_corpus(session, year)
    if limit is not None:
        entries = entries[:limit]
    print(f"Scraping {len(entries)} documents into {out_dir}/ (delay {delay}s, refresh={refresh})\n")

    counts = {"ok": 0, "skipped": 0}
    failures: list[tuple[str, str]] = []

    for i, entry in enumerate(entries, start=1):
        label = f"[{i}/{len(entries)}] {entry.code} ({entry.career})"
        try:
            result = scrape_entry(session, entry, out_dir, refresh=refresh)
        except (requests.RequestException, ScrapeError) as exc:
            failures.append((entry.url, str(exc)))
            print(f"{label}: FAILED — {exc}")
            continue

        counts[result] += 1
        print(f"{label}: {result}")
        # Rate-limit only when we actually hit the network.
        if result == "ok" and i < len(entries):
            time.sleep(delay)

    print(f"\nDone: {counts['ok']} scraped, {counts['skipped']} skipped, {len(failures)} failed")
    for url, err in failures:
        print(f"  FAIL {url}: {err}")
    return len(failures)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2026, help="Handbook year (default: 2026)")
    parser.add_argument("--out", type=Path, default=Path("data/raw"), help="Output dir (default: data/raw)")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between fetches (default: 1.0)")
    parser.add_argument("--limit", type=int, default=None, help="Scrape only the first N (for testing)")
    parser.add_argument("--refresh", action="store_true", help="Re-fetch even if the file exists")
    args = parser.parse_args()

    failures = run(args.year, args.out, args.delay, args.limit, args.refresh)
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
