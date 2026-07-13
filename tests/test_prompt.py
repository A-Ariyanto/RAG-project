"""Unit tests for the citation-enforcing prompt builder (pure, no DB/torch)."""

from app.prompt import SYSTEM_PROMPT, build_messages, citations_from
from app.retrieval import Result


def _result(id: int, code: str, section: str = "offering") -> Result:
    return Result(
        id=id,
        doc_code=code,
        section_type=section,
        title=f"{code} — Title",
        text=f"{code} body text",
        source_url=f"https://handbook/{code}",
        rrf_score=0.03,
    )


def test_citations_are_numbered_from_one_and_map_to_chunks():
    chunks = [_result(11, "COMP3311"), _result(22, "COMP2521")]
    cites = citations_from(chunks)
    assert [(c.n, c.chunk_id, c.doc_code) for c in cites] == [
        (1, 11, "COMP3311"),
        (2, 22, "COMP2521"),
    ]
    assert cites[0].source_url == "https://handbook/COMP3311"


def test_build_messages_numbers_sources_and_carries_citation_rules():
    chunks = [_result(11, "COMP3311"), _result(22, "COMP2521")]
    messages = build_messages("terms for COMP3311?", chunks)

    assert messages[0] == {"role": "system", "content": SYSTEM_PROMPT}
    user = messages[1]["content"]
    # Numbered sources the [n] markers refer back to.
    assert "[1] COMP3311" in user
    assert "[2] COMP2521" in user
    # The question and the inline-citation instruction are present.
    assert "terms for COMP3311?" in user
    assert "[n] citations" in user
