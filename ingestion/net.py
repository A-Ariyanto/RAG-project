"""Shared HTTP setup for the ingestion scraper: a polite, retrying session.

Kept deliberately small so both `discover` (sitemap fetches) and `scrape`
(page fetches) share one honest User-Agent and one retry/backoff policy.
"""

from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Identify the client honestly rather than masquerading as a browser, so the
# handbook operators can see what's hitting them and why.
USER_AGENT = "unsw-handbook-rag/0.1 (personal educational RAG project)"

# Transient statuses worth retrying; everything else fails fast.
_RETRY_STATUSES = (429, 500, 502, 503, 504)


def build_session(user_agent: str = USER_AGENT) -> requests.Session:
    """A requests session with our User-Agent and exponential backoff retries."""
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})

    retry = Retry(
        total=4,
        backoff_factor=1.0,  # sleeps 1s, 2s, 4s, 8s between attempts
        status_forcelist=_RETRY_STATUSES,
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session
