"""Generation provider interface: prompt in → token stream out.

One narrow contract (`Provider.stream`) so the rest of the service never knows
which model answers. The concrete `DeepSeekProvider` streams from DeepSeek's
OpenAI-compatible `/chat/completions` endpoint; swapping to any other cheap
OpenAI-compatible model is a config change (base_url + model), not a code change.

The stream yields `StreamEvent`s: text deltas as they arrive, then a final event
carrying `Usage` (token counts + estimated cost) once the upstream reports it.
Splitting text from usage lets the service forward tokens to the client
immediately and log cost after the stream closes.

`httpx` is imported lazily inside the network call so importing this module (for
the pure `estimate_cost` / event types) stays dependency-light for unit tests.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Protocol

from app.config import Settings

# A chat message: {"role": "system"|"user"|"assistant", "content": str}.
Message = dict[str, str]


@dataclass
class Usage:
    """Token accounting for one completion, with cost derived from config rates."""

    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


@dataclass
class StreamEvent:
    """Either a text delta (`text` set) or the terminal usage report (`usage` set)."""

    text: str | None = None
    usage: Usage | None = None


def estimate_cost(
    prompt_tokens: int,
    completion_tokens: int,
    *,
    input_per_mtok: float,
    output_per_mtok: float,
) -> float:
    """Dollar cost of a completion from per-1M-token input/output rates."""
    return (
        prompt_tokens / 1_000_000 * input_per_mtok
        + completion_tokens / 1_000_000 * output_per_mtok
    )


class Provider(Protocol):
    """The swappable generation contract the service depends on."""

    async def stream(self, messages: Sequence[Message]) -> AsyncIterator[StreamEvent]:
        ...


def _parse_sse_line(line: str) -> dict | None:
    """Decode one OpenAI-style SSE `data:` line to a JSON object.

    Returns None for blank lines, comments, and the terminal `[DONE]` sentinel —
    anything that isn't a JSON payload the caller should act on.
    """
    line = line.strip()
    if not line or not line.startswith("data:"):
        return None
    payload = line[len("data:") :].strip()
    if not payload or payload == "[DONE]":
        return None
    return json.loads(payload)


class DeepSeekProvider:
    """Streams completions from DeepSeek's OpenAI-compatible chat API."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def stream(self, messages: Sequence[Message]) -> AsyncIterator[StreamEvent]:
        import httpx

        s = self._settings
        if not s.deepseek_api_key:
            raise RuntimeError(
                "DEEPSEEK_API_KEY is not set — cannot generate an answer. "
                "Set it in .env (retrieval and refusal still work without it)."
            )

        body = {
            "model": s.deepseek_model,
            "messages": list(messages),
            "stream": True,
            # Ask the API to append a final chunk carrying token usage so we can
            # log cost without a second (billed) token-counting call.
            "stream_options": {"include_usage": True},
            # Low but non-zero: grounded, near-deterministic answers over the same
            # retrieved context, without collapsing to degenerate repetition.
            "temperature": 0.2,
        }
        headers = {"Authorization": f"Bearer {s.deepseek_api_key}"}
        url = f"{s.deepseek_base_url.rstrip('/')}/chat/completions"

        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
            async with client.stream("POST", url, json=body, headers=headers) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    obj = _parse_sse_line(line)
                    if obj is None:
                        continue
                    for event in self._events_from_chunk(obj):
                        yield event

    def _events_from_chunk(self, obj: dict) -> list[StreamEvent]:
        """Turn one streamed JSON chunk into 0+ StreamEvents.

        A chunk may carry a content delta, a usage report (the final chunk when
        `include_usage` is on, which has an empty `choices` list), or both.
        """
        events: list[StreamEvent] = []
        for choice in obj.get("choices") or []:
            text = (choice.get("delta") or {}).get("content")
            if text:
                events.append(StreamEvent(text=text))
        usage = obj.get("usage")
        if usage:
            prompt = usage.get("prompt_tokens", 0)
            completion = usage.get("completion_tokens", 0)
            cost = estimate_cost(
                prompt,
                completion,
                input_per_mtok=self._settings.deepseek_price_input_per_mtok,
                output_per_mtok=self._settings.deepseek_price_output_per_mtok,
            )
            events.append(
                StreamEvent(usage=Usage(prompt, completion, cost))
            )
        return events
