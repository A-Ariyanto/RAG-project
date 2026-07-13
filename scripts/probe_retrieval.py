"""Phase 3 probe harness: compare hybrid RRF vs vector-only vs FTS-only.

A fixed set of ~15 enrolment-style probe queries, each labelled with the chunk
it *should* surface (by course code + section). For every probe we run all three
retrievers and record where the right chunk landed. The summary reports hit@1
and hit@3 for each method, and — the point of the phase — lists the probes where
fusion rescued a chunk that at least one single method missed from its top 3.

No API, no FastAPI: a plain script against the same Postgres the service will
use. Run inside the app container:

    docker compose exec app python -m scripts.probe_retrieval
    docker compose exec app python -m scripts.probe_retrieval --show 5   # top-5 dump
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field

import asyncpg

from app.config import settings
from app.retrieval import Result, fts_search, hybrid_search, vector_search

TOP_K = 5          # depth each retriever returns for inspection
HIT_RANK = 3       # a "hit" = right chunk within the top-N (exit criterion: top 3)


@dataclass
class Probe:
    """A probe query and the chunk that answers it (course code + section(s))."""

    query: str
    expect_code: str
    # Empty = any section of the course counts; otherwise one of these sections.
    expect_sections: tuple[str, ...] = ()
    note: str = ""


# Fixed probe set covering the four question shapes Phase 3 must handle:
# prerequisites, term offerings, UOC, and enrolment rules (exclusion/equivalent).
# Phrasings deliberately vary between code-heavy (favours FTS) and paraphrased
# natural language (favours vector) to expose where each single method breaks.
PROBES: list[Probe] = [
    # --- prerequisites -------------------------------------------------------
    Probe("What do I need to complete before enrolling in COMP3311?",
          "COMP3311", ("enrolment_conditions",),
          "mixes NL intent with an exact code"),
    Probe("prerequisites for Database Systems",
          "COMP3311", ("enrolment_conditions",),
          "course by name, no code — vector must bridge name→code"),
    Probe("before I can study database systems what should I have finished first",
          "COMP3311", ("enrolment_conditions",),
          "fully paraphrased, no lexical anchor at all"),
    Probe("COMP3231 prerequisites",
          "COMP3231", ("enrolment_conditions",),
          "bare code + keyword — FTS territory"),
    Probe("prerequisite for Computer Networks and Applications",
          "COMP3331", ("enrolment_conditions",)),
    Probe("what are the requirements to enrol in the computer science capstone project",
          "COMP3900", ("enrolment_conditions",),
          "'capstone' never appears — pure semantic match to 'Project'"),
    # --- term offerings ------------------------------------------------------
    Probe("In which terms is COMP3311 offered?",
          "COMP3311", ("offering",)),
    Probe("Is Data Structures and Algorithms offered in summer term?",
          "COMP2521", ("offering",),
          "name + 'summer' — offering chunk lists Summer,T1,T2,T3"),
    Probe("when can I take Software Engineering Fundamentals",
          "COMP1531", ("offering",)),
    # --- units of credit -----------------------------------------------------
    Probe("How many units of credit is COMP1531 worth?",
          "COMP1531", ("offering",)),
    Probe("credit points for Data Structures and Algorithms",
          "COMP2521", ("offering",)),
    # --- enrolment rules: exclusions / equivalents ---------------------------
    Probe("Can I enrol in COMP2521 if I've already done COMP1927?",
          "COMP2521", ("enrolment_conditions",),
          "answer is the exclusion COMP1927 — both codes are exact tokens"),
    Probe("which course is excluded if I enrol in Microprocessors and Interfacing",
          "COMP9032", ("enrolment_conditions",),
          "'excluded' is the semantic cue; course named, no code given"),
    Probe("which courses is COMP3231 equivalent to",
          "COMP3231", ("enrolment_conditions",),
          "equivalents live in COMP3231's own enrolment chunk — exact-code find"),
    Probe("what is the exclusion for the Database Systems course",
          "COMP3311", ("enrolment_conditions",)),
]


def _is_hit(results: list[Result], probe: Probe, within: int) -> int | None:
    """1-based rank of the first matching result within `within`, else None."""
    for rank, r in enumerate(results[:within], start=1):
        if r.doc_code == probe.expect_code and (
            not probe.expect_sections or r.section_type in probe.expect_sections
        ):
            return rank
    return None


@dataclass
class Tally:
    hit1: int = 0
    hit3: int = 0
    misses: list[str] = field(default_factory=list)


def _fmt(rank: int | None) -> str:
    if rank is None:
        return "  —  "
    mark = "①" if rank == 1 else ("✓" if rank <= HIT_RANK else "·")
    return f" {mark}@{rank} "


async def run(show: int) -> None:
    conn = await asyncpg.connect(settings.database_url)
    methods = {"vector": vector_search, "fts": fts_search, "hybrid": hybrid_search}
    tallies = {name: Tally() for name in methods}
    hybrid_rescues: list[str] = []

    try:
        print(f"Probing {len(PROBES)} queries (hit = right chunk within top {HIT_RANK})\n")
        header = f"{'query':<58}" + "".join(f"{m:^9}" for m in methods)
        print(header)
        print("-" * len(header))

        for probe in PROBES:
            ranks: dict[str, int | None] = {}
            results: dict[str, list[Result]] = {}
            for name, fn in methods.items():
                res = await fn(conn, probe.query, top_k=max(TOP_K, show))
                results[name] = res
                rank = _is_hit(res, probe, HIT_RANK)
                ranks[name] = rank
                t = tallies[name]
                if rank == 1:
                    t.hit1 += 1
                if rank is not None:
                    t.hit3 += 1
                else:
                    t.misses.append(probe.query)

            q = (probe.query[:55] + "…") if len(probe.query) > 56 else probe.query
            print(f"{q:<58}" + "".join(f"{_fmt(ranks[m]):^9}" for m in methods))

            # The phase's payoff: fusion in top-3 where a single method wasn't.
            if ranks["hybrid"] is not None and (
                ranks["vector"] is None or ranks["fts"] is None
            ):
                lost = [m for m in ("vector", "fts") if ranks[m] is None]
                hybrid_rescues.append(f"  • {probe.query}\n      missed by: {', '.join(lost)}"
                                      + (f" — {probe.note}" if probe.note else ""))

            if show:
                for name in methods:
                    print(f"    [{name}]")
                    for i, r in enumerate(results[name][:show], 1):
                        print(f"      {i}. {r.doc_code} {r.section_type} :: {r.text[:70]}")
                print()

        n = len(PROBES)
        print("\n=== hit rate ===")
        print(f"{'method':<10}{'hit@1':>10}{'hit@3':>10}")
        for name, t in tallies.items():
            print(f"{name:<10}{f'{t.hit1}/{n}':>10}{f'{t.hit3}/{n}':>10}")

        if hybrid_rescues:
            print("\n=== where fusion beat a single method (top-3) ===")
            print("\n".join(hybrid_rescues))
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--show", type=int, default=0,
                        help="Also dump the top-N results per method (default: off)")
    args = parser.parse_args()
    asyncio.run(run(args.show))


if __name__ == "__main__":
    main()
