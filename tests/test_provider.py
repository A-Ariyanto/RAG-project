"""Unit tests for the provider interface: cost, SSE parsing, chunk decoding.

Pure logic only — no network. `DeepSeekProvider._events_from_chunk` and
`_parse_sse_line` are the parts that turn raw streamed JSON into StreamEvents.
"""

from app.config import Settings
from app.provider import (
    DeepSeekProvider,
    Usage,
    _parse_sse_line,
    estimate_cost,
)


def test_estimate_cost_blends_input_and_output_rates():
    cost = estimate_cost(
        1_000_000, 2_000_000, input_per_mtok=0.27, output_per_mtok=1.10
    )
    assert cost == 0.27 + 2 * 1.10


def test_parse_sse_line_handles_data_done_and_noise():
    assert _parse_sse_line('data: {"a": 1}') == {"a": 1}
    assert _parse_sse_line("data: [DONE]") is None
    assert _parse_sse_line("") is None
    assert _parse_sse_line(": keep-alive comment") is None


def _provider() -> DeepSeekProvider:
    return DeepSeekProvider(
        Settings(
            deepseek_price_input_per_mtok=0.27,
            deepseek_price_output_per_mtok=1.10,
        )
    )


def test_content_delta_becomes_a_text_event():
    events = _provider()._events_from_chunk(
        {"choices": [{"delta": {"content": "Data "}}]}
    )
    assert len(events) == 1
    assert events[0].text == "Data "
    assert events[0].usage is None


def test_final_usage_chunk_becomes_a_usage_event_with_cost():
    events = _provider()._events_from_chunk(
        {"choices": [], "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 0}}
    )
    assert len(events) == 1
    assert events[0].text is None
    assert events[0].usage == Usage(1_000_000, 0, 0.27)


def test_empty_delta_yields_no_events():
    # Role-only opening chunk (no content, no usage) produces nothing.
    assert _provider()._events_from_chunk({"choices": [{"delta": {"role": "assistant"}}]}) == []
