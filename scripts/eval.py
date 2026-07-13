"""Phase 5 evaluation harness — the project's headline numbers, one command.

Scores the golden set (`eval/golden.yaml`) three ways and prints a markdown
report (also written to `eval/results.md` for the README):

1. **Retrieval hit-rate@k** — for every answerable question, did each retriever
   (fused / vector-only / FTS-only) surface a gold chunk in its top k? This is
   the number that justifies hybrid retrieval over either single method.
2. **Refusal threshold sweep** — treating the top fused RRF score as a confidence
   signal, sweep the refusal threshold and report where off-corpus questions get
   refused without over-refusing answerable ones. Prints the balanced-accuracy
   optimum to feed back into `settings.refusal_threshold`.
3. **Answer groundedness (LLM-as-judge)** — generate a real answer for each
   answerable question and have DeepSeek judge whether every claim is supported by
   the retrieved sources and cited; for the "names a real course but the attribute
   isn't in the corpus" questions, whether the model correctly declined. Needs
   `DEEPSEEK_API_KEY`; auto-skipped (with a note) when unset so the retrieval and
   sweep sections still run key-free.

Deliberately NOT wired into CI (side project) — run it locally when the corpus,
retrieval query, or prompt changes:

    docker compose exec app python -m scripts.eval
    docker compose exec app python -m scripts.eval --no-judge   # skip the paid judge

Gold chunks are matched on the stable (doc_code, section_type) pair, mirroring the
Phase 3 probe harness — `chunks.id` churns on re-ingest and can't anchor a fixture.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

import asyncpg
import yaml

from app.config import Settings, settings
from app.prompt import build_messages
from app.provider import DeepSeekProvider, Provider, Usage, estimate_cost
from app.retrieval import Result, fts_search, hybrid_search, vector_search
from ingestion.embed import embed_query

GOLDEN_PATH = Path(__file__).resolve().parent.parent / "eval" / "golden.yaml"
RESULTS_PATH = Path(__file__).resolve().parent.parent / "eval" / "results.md"

# hit@k depths reported for retrieval. k=3 is the Phase 3 exit-criterion depth;
# @1 and @5 bracket it. Each retriever fetches this many candidates.
HIT_KS = (1, 3, 5)
FETCH_K = max(HIT_KS)


# --- golden set model --------------------------------------------------------


@dataclass(frozen=True)
class Gold:
    doc_code: str
    section_type: str


@dataclass
class Item:
    id: str
    question: str
    shape: str
    answerable: bool
    gold: list[Gold]
    answer: str
    refusal_layer: str | None  # unanswerable only: "retrieval" | "generation"


def load_golden(path: Path = GOLDEN_PATH) -> list[Item]:
    raw = yaml.safe_load(path.read_text())
    items: list[Item] = []
    for r in raw:
        items.append(
            Item(
                id=r["id"],
                question=r["question"],
                shape=r["shape"],
                answerable=r["answerable"],
                gold=[Gold(g["doc_code"], g["section_type"]) for g in r.get("gold") or []],
                answer=r.get("answer", ""),
                refusal_layer=r.get("refusal_layer"),
            )
        )
    return items


def _hit_rank(results: list[Result], gold: list[Gold], within: int) -> int | None:
    """1-based rank of the first result matching any gold (doc_code, section_type)."""
    wanted = {(g.doc_code, g.section_type) for g in gold}
    for rank, r in enumerate(results[:within], start=1):
        if (r.doc_code, r.section_type) in wanted:
            return rank
    return None


# --- retrieval, run once per item and reused across sections -----------------


@dataclass
class Retrieved:
    hybrid: list[Result]
    vector: list[Result]
    fts: list[Result]

    @property
    def top_score(self) -> float | None:
        return self.hybrid[0].rrf_score if self.hybrid else None


async def retrieve_all(conn: asyncpg.Connection, item: Item) -> Retrieved:
    """Run all three retrievers for one question (one shared query embedding)."""
    embedding = embed_query(item.question).tolist()
    hybrid = await hybrid_search(conn, item.question, top_k=FETCH_K, query_embedding=embedding)
    vector = await vector_search(conn, item.question, top_k=FETCH_K)
    fts = await fts_search(conn, item.question, top_k=FETCH_K)
    return Retrieved(hybrid=hybrid, vector=vector, fts=fts)


# --- 1. retrieval hit-rate ---------------------------------------------------


@dataclass
class HitRates:
    method: str
    hits: dict[int, int]  # k -> count of items with a gold chunk in top-k
    n: int


def retrieval_report(items: list[Item], retrieved: dict[str, Retrieved]) -> list[HitRates]:
    answerable = [it for it in items if it.answerable]
    methods = {"hybrid (fused)": "hybrid", "vector-only": "vector", "fts-only": "fts"}
    reports: list[HitRates] = []
    for label, attr in methods.items():
        hits = {k: 0 for k in HIT_KS}
        for it in answerable:
            results = getattr(retrieved[it.id], attr)
            for k in HIT_KS:
                if _hit_rank(results, it.gold, k) is not None:
                    hits[k] += 1
        reports.append(HitRates(method=label, hits=hits, n=len(answerable)))
    return reports


# --- 2. refusal threshold sweep ----------------------------------------------


@dataclass
class SweepRow:
    threshold: float
    answerable_answered: int  # of answerable, kept (score >= t) — higher is better
    offcorpus_refused: int    # of off-corpus, refused (score < t) — higher is better
    balanced_acc: float


@dataclass
class SweepReport:
    rows: list[SweepRow]
    best: SweepRow
    n_answerable: int
    n_offcorpus: int
    grid: list[SweepRow]  # a few representative thresholds for display


def threshold_sweep(items: list[Item], retrieved: dict[str, Retrieved]) -> SweepReport:
    """Sweep the refusal threshold over the fused top-score.

    Positives = answerable (should be kept). Negatives = off-corpus unanswerable
    (refusal_layer == 'retrieval', should be refused). Generation-layer
    unanswerables retrieve their named course with a high score, so no score
    threshold can catch them — they're excluded here and handled by the judge.
    """
    answerable = [it for it in items if it.answerable]
    offcorpus = [it for it in items if not it.answerable and it.refusal_layer == "retrieval"]

    def top(it: Item) -> float:
        s = retrieved[it.id].top_score
        return s if s is not None else 0.0

    ans_scores = [top(it) for it in answerable]
    neg_scores = [top(it) for it in offcorpus]
    n_ans, n_neg = len(ans_scores), len(neg_scores)

    # Candidate thresholds: midpoints between adjacent distinct observed scores
    # (the only points where any classification flips), plus the open ends.
    all_scores = sorted(set(ans_scores + neg_scores))
    eps = 1e-4
    candidates = [all_scores[0] - eps]
    candidates += [(a + b) / 2 for a, b in zip(all_scores, all_scores[1:])]
    candidates.append(all_scores[-1] + eps)

    def score_at(t: float) -> SweepRow:
        answered = sum(1 for s in ans_scores if s >= t)
        refused = sum(1 for s in neg_scores if s < t)
        acc = 0.5 * ((answered / n_ans if n_ans else 0) + (refused / n_neg if n_neg else 0))
        return SweepRow(t, answered, refused, acc)

    rows = [score_at(t) for t in candidates]
    # Best balanced accuracy; tie-break to the widest separating gap (most robust).
    best = max(rows, key=lambda r: (r.balanced_acc, r.threshold))

    # A compact, human-readable grid spanning the interesting range for display.
    lo, hi = min(all_scores), max(all_scores)
    grid_ts = [round(lo + (hi - lo) * f, 4) for f in (0.0, 0.25, 0.5, 0.75, 1.0)]
    grid = [score_at(t) for t in sorted(set(grid_ts))]

    return SweepReport(rows=rows, best=best, n_answerable=n_ans, n_offcorpus=n_neg, grid=grid)


# --- 3. answer groundedness (LLM-as-judge) -----------------------------------

JUDGE_SYSTEM = (
    "You are a strict evaluator of a retrieval-augmented answer. You are given a "
    "student's question, the numbered sources the assistant was shown, and the "
    "assistant's answer. Decide, using ONLY the sources as ground truth:\n"
    "- verdict: 'grounded' if every factual claim in the answer is supported by "
    "the sources; 'declined' if the answer declines / says it lacks enough "
    "information rather than answering; 'ungrounded' if it asserts anything not "
    "supported by the sources.\n"
    "- cited: true if the answer includes at least one inline [n] citation marker.\n"
    'Respond with ONLY a JSON object: {"verdict": "...", "cited": true/false, '
    '"reason": "..."}.'
)


@dataclass
class Judgement:
    verdict: str  # grounded | ungrounded | declined
    cited: bool
    reason: str


async def _drain(provider: Provider, messages) -> tuple[str, Usage | None]:
    """Run one generation to completion, returning the full text + usage."""
    text_parts: list[str] = []
    usage: Usage | None = None
    async for ev in provider.stream(messages):
        if ev.text:
            text_parts.append(ev.text)
        if ev.usage:
            usage = ev.usage
    return "".join(text_parts), usage


async def judge_answer(
    cfg: Settings, question: str, sources_block: str, answer: str
) -> tuple[Judgement, Usage | None]:
    """Ask DeepSeek to judge one answer; parse its JSON verdict."""
    import httpx

    body = {
        "model": cfg.deepseek_model,
        "messages": [
            {"role": "system", "content": JUDGE_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Question: {question}\n\nSources:\n{sources_block}\n\n"
                    f"Assistant's answer:\n{answer}"
                ),
            },
        ],
        "temperature": 0.0,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {cfg.deepseek_api_key}"}
    url = f"{cfg.deepseek_base_url.rstrip('/')}/chat/completions"
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        resp = await client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    content = data["choices"][0]["message"]["content"].strip()
    # Models sometimes wrap JSON in ```json fences; strip them before parsing.
    if content.startswith("```"):
        content = content.strip("`")
        content = content[content.find("{") : content.rfind("}") + 1]
    parsed = json.loads(content)
    usage = None
    if u := data.get("usage"):
        usage = Usage(
            u.get("prompt_tokens", 0),
            u.get("completion_tokens", 0),
            estimate_cost(
                u.get("prompt_tokens", 0),
                u.get("completion_tokens", 0),
                input_per_mtok=cfg.deepseek_price_input_per_mtok,
                output_per_mtok=cfg.deepseek_price_output_per_mtok,
            ),
        )
    return Judgement(parsed["verdict"], bool(parsed.get("cited", False)), parsed.get("reason", "")), usage


@dataclass
class GroundednessReport:
    grounded: int
    cited: int
    over_refused: int          # answerable questions the generator wrongly declined
    n_answerable: int
    gen_declined: int          # generation-layer unanswerables correctly declined
    n_gen_unanswerable: int
    cost_usd: float
    per_item: list[tuple[str, str, str]]  # (id, verdict, reason) for the appendix


async def groundedness_report(
    conn: asyncpg.Connection,
    provider: Provider,
    cfg: Settings,
    items: list[Item],
    retrieved: dict[str, Retrieved],
) -> GroundednessReport:
    answerable = [it for it in items if it.answerable]
    gen_unans = [it for it in items if not it.answerable and it.refusal_layer == "generation"]
    grounded = cited = over_refused = gen_declined = 0
    cost = 0.0
    per_item: list[tuple[str, str, str]] = []

    for it in answerable + gen_unans:
        # Generate with exactly the service's top-k grounding context.
        chunks = retrieved[it.id].hybrid[: cfg.retrieval_top_k]
        messages = build_messages(it.question, chunks)
        answer, gen_usage = await _drain(provider, messages)
        if gen_usage:
            cost += gen_usage.cost_usd

        block = messages[1]["content"]  # the numbered-sources user turn
        judgement, judge_usage = await judge_answer(cfg, it.question, block, answer)
        if judge_usage:
            cost += judge_usage.cost_usd

        per_item.append((it.id, judgement.verdict, judgement.reason))
        if it.answerable:
            if judgement.verdict == "grounded":
                grounded += 1
            elif judgement.verdict == "declined":
                over_refused += 1
            if judgement.cited:
                cited += 1
        else:  # generation-layer unanswerable: declining is the correct behavior
            if judgement.verdict == "declined":
                gen_declined += 1

    return GroundednessReport(
        grounded=grounded,
        cited=cited,
        over_refused=over_refused,
        n_answerable=len(answerable),
        gen_declined=gen_declined,
        n_gen_unanswerable=len(gen_unans),
        cost_usd=cost,
        per_item=per_item,
    )


# --- markdown rendering ------------------------------------------------------


def _pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "n/a"


def render_markdown(
    items: list[Item],
    hit: list[HitRates],
    sweep: SweepReport,
    ground: GroundednessReport | None,
) -> str:
    n_ans = sum(1 for it in items if it.answerable)
    n_unans = sum(1 for it in items if not it.answerable)
    out: list[str] = []
    out.append("## Evaluation results\n")
    out.append(
        f"Golden set: **{len(items)} questions** "
        f"({n_ans} answerable across prerequisite / offering / UOC / exclusion / "
        f"equivalent / corequisite shapes, {n_unans} unanswerable). Gold chunks "
        f"matched on `(doc_code, section_type)`. Generated by "
        f"`python -m scripts.eval`.\n"
    )

    # 1. retrieval
    out.append("### Retrieval hit-rate (answerable questions)\n")
    header = "| Method | " + " | ".join(f"hit@{k}" for k in HIT_KS) + " |"
    out.append(header)
    out.append("|" + "---|" * (len(HIT_KS) + 1))
    for hr in hit:
        cells = " | ".join(_pct(hr.hits[k], hr.n) for k in HIT_KS)
        out.append(f"| {hr.method} | {cells} |")
    out.append("")

    # 2. refusal sweep
    out.append("### Refusal threshold sweep\n")
    out.append(
        f"Off-corpus questions ({sweep.n_offcorpus}) should be refused; answerable "
        f"questions ({sweep.n_answerable}) should be kept. Score = top fused RRF "
        f"score. (Generation-layer unanswerables — a real course, missing "
        f"attribute — retrieve strongly and are handled by the prompt, not the "
        f"threshold; see groundedness.)\n"
    )
    out.append("| Threshold | Answerable kept | Off-corpus refused | Balanced acc |")
    out.append("|---|---|---|---|")
    for r in sweep.grid:
        out.append(
            f"| {r.threshold:.4f} | {_pct(r.answerable_answered, sweep.n_answerable)} "
            f"| {_pct(r.offcorpus_refused, sweep.n_offcorpus)} | {r.balanced_acc:.2f} |"
        )
    b = sweep.best
    out.append("")
    out.append(
        f"**Best balanced accuracy {b.balanced_acc:.2f} at threshold "
        f"`{b.threshold:.4f}`** — keeps {_pct(b.answerable_answered, sweep.n_answerable)} "
        f"answerable, refuses {_pct(b.offcorpus_refused, sweep.n_offcorpus)} off-corpus.\n"
    )

    # 3. groundedness
    out.append("### Answer groundedness (LLM-as-judge)\n")
    if ground is None:
        out.append(
            "_Skipped — `DEEPSEEK_API_KEY` not set. Retrieval and sweep above need "
            "no key; groundedness runs the real generation + a DeepSeek judge._\n"
        )
    else:
        out.append("| Metric | Result |")
        out.append("|---|---|")
        out.append(f"| Grounded answers | {_pct(ground.grounded, ground.n_answerable)} |")
        out.append(f"| Answers with inline citation | {_pct(ground.cited, ground.n_answerable)} |")
        out.append(
            f"| Over-refusal (answerable wrongly declined) | "
            f"{_pct(ground.over_refused, ground.n_answerable)} |"
        )
        out.append(
            f"| Correct decline (missing-attribute questions) | "
            f"{_pct(ground.gen_declined, ground.n_gen_unanswerable)} |"
        )
        out.append(f"| Judge + generation cost | ${ground.cost_usd:.4f} |")
        out.append("")
    return "\n".join(out)


# --- orchestration -----------------------------------------------------------


async def run(no_judge: bool) -> None:
    cfg = settings
    items = load_golden()
    conn = await asyncpg.connect(cfg.database_url)
    try:
        print(f"Retrieving for {len(items)} golden questions...", flush=True)
        retrieved = {it.id: await retrieve_all(conn, it) for it in items}

        hit = retrieval_report(items, retrieved)
        sweep = threshold_sweep(items, retrieved)

        ground: GroundednessReport | None = None
        if no_judge:
            print("Groundedness: skipped (--no-judge).", flush=True)
        elif not cfg.deepseek_api_key:
            print("Groundedness: skipped (DEEPSEEK_API_KEY unset).", flush=True)
        else:
            n_gen = sum(1 for it in items if not it.answerable and it.refusal_layer == "generation")
            n_ans = sum(1 for it in items if it.answerable)
            print(
                f"Groundedness: generating + judging {n_ans + n_gen} answers "
                f"via {cfg.deepseek_model}...",
                flush=True,
            )
            provider = DeepSeekProvider(cfg)
            ground = await groundedness_report(conn, provider, cfg, items, retrieved)
    finally:
        await conn.close()

    report = render_markdown(items, hit, sweep, ground)
    RESULTS_PATH.write_text(report + "\n")
    print("\n" + report)
    print(f"\n(Written to {RESULTS_PATH.relative_to(GOLDEN_PATH.parent.parent)})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-judge",
        action="store_true",
        help="Skip the paid LLM-as-judge groundedness section (retrieval + sweep only).",
    )
    args = parser.parse_args()
    asyncio.run(run(args.no_judge))


if __name__ == "__main__":
    main()
