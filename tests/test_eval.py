"""Unit tests for the Phase 5 eval scoring logic (`scripts/eval.py`).

Everything here is the pure metric math — hit@k matching, the refusal threshold
sweep, fusion rescue/regression diagnostics, and golden-set parsing — driven with
synthetic `Result`s so no database, embeddings, or DeepSeek judge is needed. The
full eval *run* stays manual (see the module docstring); this guards the numbers
it produces against silent regressions.
"""

from __future__ import annotations

from app.retrieval import Result
from scripts.eval import (
    Gold,
    Item,
    Retrieved,
    _hit_rank,
    fusion_diagnostics,
    load_golden,
    retrieval_report,
    threshold_sweep,
)


def _res(code: str, section: str, rrf: float | None = None) -> Result:
    return Result(
        id=0,
        doc_code=code,
        section_type=section,
        title=f"{code} title",
        text=f"{code} body",
        source_url=f"https://handbook/{code}",
        rrf_score=rrf,
    )


def _item(id: str, *, answerable=True, phrasing=None, refusal_layer=None, gold=()) -> Item:
    return Item(
        id=id,
        question=f"q for {id}",
        shape="prerequisite",
        answerable=answerable,
        gold=[Gold(*g) for g in gold],
        answer="",
        phrasing=phrasing,
        refusal_layer=refusal_layer,
    )


# --- _hit_rank ---------------------------------------------------------------


def test_hit_rank_matches_doc_and_section():
    results = [_res("COMP1511", "offering"), _res("COMP3311", "enrolment_conditions")]
    gold = [Gold("COMP3311", "enrolment_conditions")]
    assert _hit_rank(results, gold, within=3) == 2


def test_hit_rank_respects_within_cutoff():
    results = [_res("X", "a"), _res("Y", "b"), _res("COMP3311", "enrolment_conditions")]
    gold = [Gold("COMP3311", "enrolment_conditions")]
    assert _hit_rank(results, gold, within=2) is None
    assert _hit_rank(results, gold, within=3) == 3


def test_hit_rank_requires_matching_section():
    # Right course, wrong section is not a hit — sections are the whole point.
    results = [_res("COMP3311", "offering")]
    gold = [Gold("COMP3311", "enrolment_conditions")]
    assert _hit_rank(results, gold, within=3) is None


# --- threshold_sweep ---------------------------------------------------------


def _retrieved(top_score: float) -> Retrieved:
    return Retrieved(hybrid=[_res("C", "offering", top_score)], vector=[], fts=[])


def test_threshold_sweep_finds_separating_optimum():
    # Answerable score high, off-corpus low — a clean split at ~0.02 should be found.
    items = [
        _item("a1", answerable=True),
        _item("a2", answerable=True),
        _item("n1", answerable=False, refusal_layer="retrieval"),
        _item("n2", answerable=False, refusal_layer="retrieval"),
    ]
    retrieved = {
        "a1": _retrieved(0.030),
        "a2": _retrieved(0.031),
        "n1": _retrieved(0.010),
        "n2": _retrieved(0.012),
    }
    report = threshold_sweep(items, retrieved)
    assert report.n_answerable == 2
    assert report.n_offcorpus == 2
    assert report.best.balanced_acc == 1.0
    # Optimal threshold sits between the off-corpus and answerable clusters.
    assert 0.012 < report.best.threshold <= 0.030


def test_threshold_sweep_excludes_generation_layer_negatives():
    # A generation-layer unanswerable retrieves strongly; it must not count as an
    # off-corpus negative in the score-threshold sweep.
    items = [
        _item("a1", answerable=True),
        _item("g1", answerable=False, refusal_layer="generation"),
        _item("n1", answerable=False, refusal_layer="retrieval"),
    ]
    retrieved = {"a1": _retrieved(0.03), "g1": _retrieved(0.03), "n1": _retrieved(0.01)}
    report = threshold_sweep(items, retrieved)
    assert report.n_offcorpus == 1  # only the retrieval-layer negative


# --- fusion_diagnostics ------------------------------------------------------


def test_fusion_rescue_and_regression():
    gold = [Gold("COMP3311", "enrolment_conditions")]
    hit = _res("COMP3311", "enrolment_conditions")
    miss = _res("OTHER", "overview")

    items = [
        _item("rescue", gold=[("COMP3311", "enrolment_conditions")]),
        _item("regress", gold=[("COMP3311", "enrolment_conditions")]),
    ]
    retrieved = {
        # hybrid hits, fts misses -> rescue
        "rescue": Retrieved(hybrid=[hit], vector=[hit], fts=[miss]),
        # hybrid misses, fts hits -> regression
        "regress": Retrieved(hybrid=[miss], vector=[miss], fts=[hit]),
    }
    rescues, regressions = fusion_diagnostics(items, retrieved, within=3)
    assert [c.id for c in rescues] == ["rescue"]
    assert rescues[0].others == ["fts"]
    assert [c.id for c in regressions] == ["regress"]
    assert regressions[0].others == ["fts"]


# --- retrieval_report phrasing split -----------------------------------------


def test_retrieval_report_splits_by_phrasing():
    gold_kv = [("COMP3311", "enrolment_conditions")]
    hit = _res("COMP3311", "enrolment_conditions")
    items = [
        _item("c1", phrasing="code", gold=gold_kv),
        _item("n1", phrasing="name", gold=gold_kv),
    ]
    retrieved = {
        "c1": Retrieved(hybrid=[hit], vector=[hit], fts=[hit]),
        "n1": Retrieved(hybrid=[hit], vector=[hit], fts=[hit]),
    }
    report = retrieval_report(items, retrieved)
    assert report.by_phrasing["code"][0].n == 1
    assert report.by_phrasing["name"][0].n == 1
    # Every method hits both, so overall hit@1 is 2/2.
    assert report.overall[0].hits[1] == 2


# --- load_golden (real file) -------------------------------------------------


def test_load_golden_parses_the_committed_set():
    items = load_golden()
    assert len(items) >= 28
    answerable = [it for it in items if it.answerable]
    unanswerable = [it for it in items if not it.answerable]
    assert len(unanswerable) == 6
    # Answerable items carry a phrasing tag and at least one gold chunk.
    for it in answerable:
        assert it.phrasing in ("code", "name")
        assert it.gold, f"{it.id} has no gold chunk"
    # Unanswerable items declare which layer should catch them and carry no gold.
    for it in unanswerable:
        assert it.refusal_layer in ("retrieval", "generation")
        assert it.gold == []
