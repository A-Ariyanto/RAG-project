"""Unit tests for the /ask orchestration.

`should_refuse` and the SSE framing are pure. `answer_events` is driven
end-to-end with a fake provider and monkeypatched retrieval/logging, so the
event contract (meta → token → done) and the query_logs write are exercised
without torch, the network, or a database. Async tests run via `asyncio.run`
to avoid a pytest-asyncio dependency in the light CI suite.
"""

from __future__ import annotations

import asyncio
import json

import app.service as service
from app.config import Settings
from app.provider import StreamEvent, Usage
from app.retrieval import Result
from app.service import answer_events, should_refuse


def _result(id: int, code: str, score: float) -> Result:
    return Result(
        id=id,
        doc_code=code,
        section_type="offering",
        title=f"{code} — Title",
        text=f"{code} body",
        source_url=f"https://handbook/{code}",
        rrf_score=score,
    )


def _parse_sse(chunks: list[str]) -> list[tuple[str, dict]]:
    """Parse framed 'event: X\\ndata: {...}\\n\\n' strings into (name, data) pairs."""
    events = []
    for chunk in chunks:
        name = data = None
        for line in chunk.strip().splitlines():
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: ") :])
        events.append((name, data))
    return events


# --- should_refuse ----------------------------------------------------------


def test_should_refuse_on_empty_results():
    assert should_refuse([], 0.015) is True


def test_should_refuse_below_threshold():
    assert should_refuse([_result(1, "COMP1511", 0.005)], 0.015) is True


def test_does_not_refuse_at_or_above_threshold():
    assert should_refuse([_result(1, "COMP1511", 0.02)], 0.015) is False


# --- answer_events harness ---------------------------------------------------


class _FakeProvider:
    """Streams canned text then a usage report; records whether it was called."""

    def __init__(self, events):
        self._events = events
        self.called = False

    async def stream(self, messages):
        self.called = True
        for event in self._events:
            yield event


def _patch_retrieval(monkeypatch, results):
    """Stub out embedding + hybrid search; capture the query_logs write."""
    monkeypatch.setattr(
        service, "embed_query", lambda q: type("V", (), {"tolist": lambda self: [0.0]})()
    )

    async def fake_hybrid(conn, query, **kwargs):
        return results

    monkeypatch.setattr(service, "hybrid_search", fake_hybrid)

    logged = {}

    async def fake_log(conn, **kwargs):
        logged.update(kwargs)

    monkeypatch.setattr(service, "insert_query_log", fake_log)
    return logged


def _drain(agen) -> list[str]:
    async def run():
        return [chunk async for chunk in agen]

    return asyncio.run(run())


def test_answer_path_streams_meta_tokens_done_and_logs(monkeypatch):
    results = [_result(11, "COMP3311", 0.03), _result(22, "COMP2521", 0.02)]
    logged = _patch_retrieval(monkeypatch, results)
    provider = _FakeProvider(
        [StreamEvent(text="In T1"), StreamEvent(text=" and T2."), StreamEvent(usage=Usage(120, 8, 0.001))]
    )

    chunks = _drain(answer_events(object(), provider, "terms for COMP3311?", Settings()))
    events = _parse_sse(chunks)

    assert provider.called is True
    assert events[0][0] == "meta"
    assert events[0][1]["refused"] is False
    assert [c["chunk_id"] for c in events[0][1]["citations"]] == [11, 22]
    # Text deltas forwarded verbatim, in order, then a terminal done.
    tokens = [d["text"] for name, d in events if name == "token"]
    assert tokens == ["In T1", " and T2."]
    assert events[-1][0] == "done"
    # query_logs captured the answer's accounting.
    assert logged["refused"] is False
    assert logged["retrieved_chunk_ids"] == [11, 22]
    assert logged["prompt_tokens"] == 120
    assert logged["completion_tokens"] == 8
    assert logged["cost_usd"] == 0.001


def test_refusal_path_skips_generation_and_logs_refusal(monkeypatch):
    results = [_result(11, "COMP3311", 0.004)]  # below default threshold
    logged = _patch_retrieval(monkeypatch, results)
    provider = _FakeProvider([StreamEvent(text="should not run")])

    chunks = _drain(answer_events(object(), provider, "unanswerable?", Settings()))
    events = _parse_sse(chunks)

    assert provider.called is False  # generation short-circuited
    assert events[0][0] == "meta"
    assert events[0][1]["refused"] is True
    assert events[1][0] == "token"
    assert "enough information" in events[1][1]["text"]
    assert events[-1][0] == "done"
    assert logged["refused"] is True
    assert logged["prompt_tokens"] is None
