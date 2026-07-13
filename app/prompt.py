"""Citation-enforcing prompt: turn retrieved chunks into a grounded LLM request.

The retrieved chunks are numbered [1], [2], … and the model is instructed to
answer *only* from them and cite the number(s) it used inline. Those same numbers
map back to chunk ids and source URLs (`citations_from`) so the frontend can turn
a marker into a link to the exact handbook section. Keeping the numbering in one
place here is what keeps the markers the model emits and the citation list the
API returns in lock-step.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.retrieval import Result

SYSTEM_PROMPT = (
    "You are a UNSW Handbook assistant. Answer the student's enrolment question "
    "using ONLY the numbered sources provided. Every claim must be grounded in a "
    "source; cite the source number(s) you used with inline markers like [1] or "
    "[2] immediately after the claim they support. Do not use any outside "
    "knowledge. If the sources do not contain enough information to answer, say "
    "you don't have enough information rather than guessing. Be concise and "
    "direct — a couple of sentences is usually enough."
)


@dataclass
class Citation:
    """A source number the model can cite, mapped back to its chunk + URL."""

    n: int  # 1-based marker shown to the model and returned to the client
    chunk_id: int
    doc_code: str
    title: str
    section_type: str
    source_url: str


def citations_from(chunks: Sequence[Result]) -> list[Citation]:
    """Number the retrieved chunks 1..k — the mapping behind the [n] markers."""
    return [
        Citation(
            n=i,
            chunk_id=c.id,
            doc_code=c.doc_code,
            title=c.title,
            section_type=c.section_type,
            source_url=c.source_url,
        )
        for i, c in enumerate(chunks, start=1)
    ]


def _context_block(chunks: Sequence[Result]) -> str:
    """Render the numbered sources the model must ground its answer in."""
    blocks = []
    for i, c in enumerate(chunks, start=1):
        blocks.append(
            f"[{i}] {c.doc_code} — {c.title} ({c.section_type})\n{c.text}"
        )
    return "\n\n".join(blocks)


def build_messages(query: str, chunks: Sequence[Result]) -> list[dict[str, str]]:
    """Assemble the chat messages: system rules + numbered sources + question."""
    user = (
        f"Sources:\n\n{_context_block(chunks)}\n\n"
        f"Question: {query}\n\n"
        "Answer using only the sources above, with inline [n] citations."
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
