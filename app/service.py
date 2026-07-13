"""Orchestrates one /ask: retrieve → refuse-or-generate → stream SSE → log.

`answer_events` is the whole query path as an async generator of Server-Sent
Events, framework-agnostic so it can be unit-tested without an HTTP client and
handed to FastAPI's `StreamingResponse` unchanged. The event contract the
frontend consumes:

* `meta`   — once, first: citations, whether we refused, the top fused score.
* `token`  — zero or more: `{"text": "..."}` answer deltas as they stream.
* `done`   — once, last: `{}` (usage/latency go to query_logs, not the client).

Refusal (top fused score below `settings.refusal_threshold`) short-circuits
generation: the meta event carries the nearest matches and a fixed "not enough
information" message streams as `token`s, so the client renders refusals and
answers through the same code path. Every request — refused or answered — writes
one `query_logs` row in a `finally`, even if the client disconnects mid-stream.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator

import asyncpg

from app.config import Settings
from app.db import insert_query_log
from app.prompt import Citation, build_messages, citations_from
from app.provider import Provider
from app.retrieval import Result, hybrid_search
from ingestion.embed import embed_query

REFUSAL_MESSAGE = (
    "I don't have enough information in the UNSW Handbook to answer that "
    "confidently. The closest sections I found are listed as citations — you may "
    "want to check them directly."
)


def should_refuse(results: list[Result], threshold: float) -> bool:
    """Refuse when nothing was retrieved or the top fused score is below floor."""
    if not results:
        return True
    top = results[0].rrf_score
    return top is None or top < threshold


def _sse(event: str, data: dict) -> str:
    """Frame one Server-Sent Event: named event + JSON data, blank-line terminated."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _meta_payload(citations: list[Citation], refused: bool, top_score: float | None) -> dict:
    return {
        "refused": refused,
        "top_rrf_score": top_score,
        "citations": [
            {
                "n": c.n,
                "chunk_id": c.chunk_id,
                "doc_code": c.doc_code,
                "title": c.title,
                "section_type": c.section_type,
                "source_url": c.source_url,
            }
            for c in citations
        ],
    }


async def answer_events(
    conn: asyncpg.Connection,
    provider: Provider,
    query: str,
    settings: Settings,
) -> AsyncIterator[str]:
    """Stream SSE strings for one question; log the query when the stream ends."""
    refused = False
    top_score: float | None = None
    chunk_ids: list[int] = []
    retrieval_ms: float | None = None
    generation_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: float | None = None
    model: str | None = None

    try:
        # --- retrieval (embed off the event loop, then the fused SQL) ---
        t0 = time.perf_counter()
        embedding = await asyncio.to_thread(embed_query, query)
        results = await hybrid_search(
            conn,
            query,
            top_k=settings.retrieval_top_k,
            query_embedding=embedding.tolist(),
        )
        retrieval_ms = (time.perf_counter() - t0) * 1000

        citations = citations_from(results)
        chunk_ids = [c.id for c in results]
        top_score = results[0].rrf_score if results else None
        refused = should_refuse(results, settings.refusal_threshold)

        yield _sse("meta", _meta_payload(citations, refused, top_score))

        if refused:
            # Same event shape as an answer — a canned message, no model call.
            yield _sse("token", {"text": REFUSAL_MESSAGE})
            yield _sse("done", {})
            return

        # --- generation (stream tokens through untouched; capture usage) ---
        model = settings.deepseek_model
        messages = build_messages(query, results)
        g0 = time.perf_counter()
        async for event in provider.stream(messages):
            if event.text:
                yield _sse("token", {"text": event.text})
            if event.usage:
                prompt_tokens = event.usage.prompt_tokens
                completion_tokens = event.usage.completion_tokens
                cost_usd = event.usage.cost_usd
        generation_ms = (time.perf_counter() - g0) * 1000

        yield _sse("done", {})
    finally:
        # Log every attempt, including client disconnects mid-stream. Uses the
        # same request connection; best-effort so a logging failure never breaks
        # a response that already streamed.
        try:
            await insert_query_log(
                conn,
                query=query,
                refused=refused,
                top_rrf_score=top_score,
                retrieved_chunk_ids=chunk_ids,
                retrieval_ms=retrieval_ms,
                generation_ms=generation_ms,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=cost_usd,
                model=model,
            )
        except Exception:  # noqa: BLE001 — never let logging mask the response
            pass
