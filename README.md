# RAG-project

[![CI](https://github.com/A-Ariyanto/RAG-project/actions/workflows/ci.yml/badge.svg)](https://github.com/A-Ariyanto/RAG-project/actions/workflows/ci.yml)

A self made project that is applicibale and can be used to answer questions about the UNSW Handbook (CSE courses). The project is a RAG question-answering app that allows students to ask questions like "I've done COMP1531 and COMP2521 — can I enrol in COMP3311 in T1?" and receive a short grounded answer with citations linking to the exact source sections. The primary user of this project is the creator, and it is designed to be genuinely usable during enrolment.

## Tech Stack

- Backend: FastAPI, async SQLAlchemy/asyncpg, Pydantic
- Database: PostgreSQL with pgvector (one DB, both retrieval jobs; `pgvector ` extension is required)

Credit to Claude as I will work alongside it to make this project with the goal of upskilling myself in the field of AI and LLMs.

## Getting started

Requirements: Docker + Docker Compose.

```bash
# 1. Clone, then create your local env file from the template
cp .env.example .env

# 2. Bring up Postgres (pgvector image) + the app container
docker compose up --build
```

On startup the app container runs a healthcheck that connects to Postgres and
confirms the `pgvector` extension is available — you should see
`Healthcheck passed ✅` in the logs. To run it again on demand:

```bash
docker compose exec app python -m scripts.healthcheck
```

psql into the database directly (host port set by `POSTGRES_HOST_PORT`):

```bash
docker compose exec db psql -U rag -d handbook
```

Run the tests inside the app container:

```bash
docker compose exec app python -m pytest tests/
```

## Building the corpus (ingestion pipeline)

Two steps, both rerunnable and both run in the app container:

```bash
# 1. Scrape the COMP/SENG corpus to data/raw/ (polite, rate-limited, resumable)
docker compose exec app python -m ingestion.scrape --year 2026

# 2. Chunk + embed + load into the `chunks` table (idempotent: rerun = refresh)
docker compose exec app python -m ingestion.ingest
```

The first ingest downloads the local embedding model (`bge-small-en-v1.5`,
~130 MB) into `models/` (gitignored); later runs reuse it. `data/` and `models/`
are local-only, so a fresh clone reproduces both from these two commands.

## Evaluation

The retrieval and answer quality are measured against a hand-verified golden set
(`eval/golden.yaml`: 34 questions — 28 answerable across the prerequisite /
offering / UOC / exclusion / equivalent / corequisite shapes, 6 unanswerable
where the correct behaviour is refusal). One command regenerates every number
(`eval/results.md`):

```bash
docker compose exec app python -m scripts.eval            # full: adds the LLM judge
docker compose exec app python -m scripts.eval --no-judge # retrieval + refusal only (no API key)
```

The eval is run **manually**, not in CI — this is a side project, so a committed
chunks fixture + a pgvector service + paid judge calls on every push aren't worth
it. (The eval's pure scoring logic *is* unit-tested in CI via `tests/test_eval.py`.)

### Retrieval hit-rate (answerable questions)

Did the retriever surface a gold chunk — matched on the stable
`(doc_code, section_type)` pair — in its top *k*?

| Method | hit@1 | hit@3 | hit@5 |
|---|---|---|---|
| hybrid (fused) | 68% | 82% | 93% |
| vector-only | 39% | 64% | 64% |
| **fts-only** | **79%** | **89%** | **96%** |

**The honest finding: on this corpus a well-tuned FTS is the strongest single
retriever, and hybrid does not beat it.** Every chunk is prefixed with its course
*code and title* (`COMP3311 Database Systems — …`), so course names are verbatim
lexical tokens and lexical search rarely needs semantic help — even for questions
that name a course by title rather than code:

| Method | code-anchored hit@3 (16 q) | name-only hit@3 (12 q) |
|---|---|---|
| hybrid (fused) | 88% | 75% |
| vector-only | 69% | 58% |
| fts-only | 88% | 92% |

Fusion **rescues 7** questions (2 where FTS alone fails on genuine paraphrase —
"what do I need *before* enrolling…" — recovered by the vector side; 5 where
vector alone fails, anchored by FTS) but **regresses 4** (offering/term questions
where blending in a noisier vector ranking pushes the gold *offering* chunk below
a same-course *overview* chunk), netting just under FTS overall. Hybrid's real,
measurable value here is **robustness** — it never collapses the way vector-only
does on code-anchored queries — and **semantic recovery** on true paraphrases. On
a messier corpus without code+title prefixing (a post-v1 roadmap item), the gap
would widen in fusion's favour; this corpus is a near-best case for lexical search
and the eval reports that rather than hiding it.

### Refusal

The top fused RRF score gates generation: below `refusal_threshold` the service
skips the model and returns the nearest matches. The threshold was tuned by a
sweep against the golden set — balanced-accuracy optimum **0.96 at 0.0306**
(keeps 26/28 answerable, refuses 4/4 off-corpus). The serving value sits just
under it at **0.030** to keep answerable recall at 28/28 while refusing 3/4 clean
off-corpus questions; the citation-enforcing prompt is the second net for the
residual (course-named but attribute-missing) questions.

### Answer groundedness (LLM-as-judge)

DeepSeek judges each generated answer against only the retrieved sources:

| Metric | Result |
|---|---|
| Grounded answers (every claim supported) | 93% |
| Answers with an inline `[n]` citation | 96% |
| Correct decline on missing-attribute questions | 2/2 |
| Cost of a full judged run | ~$0.011 |

## Repo layout

| Path | Purpose |
|---|---|
| `app/` | FastAPI service: `/ask` streaming endpoint, hybrid retrieval, provider, config |
| `ingestion/` | Scraper → parser → chunker → embed pipeline (Phases 1–2) |
| `scripts/` | Operational scripts: `healthcheck.py`, `probe_retrieval.py`, `eval.py` |
| `eval/` | Golden set (`golden.yaml`) + generated results (`results.md`) |
| `tests/` | Test suite |
| `docker-compose.yml`, `Dockerfile` | Local Postgres + app container |
| `.github/workflows/` | GitHub Actions CI (tests + Docker image build) |
| `.env.example` | Template for the `.env` you create locally |

## Project Docs

- [IMPLEMENTATION.md](IMPLEMENTATION.md) — design rationale: requirements, stack decisions, architecture
- [ROADMAP.md](ROADMAP.md) — trackable phase-by-phase task checklists and current status

## Progress Report

07/07/2026 - Setup the project repository after planning and discussing with Claude.

12/07/2026 - Added ROADMAP.md, a trackable phase-by-phase task checklist for the build.

12/07/2026 - Completed Phase 0 (Scaffold): repo skeleton, Docker Compose with a pgvector Postgres + Python app container, `.env` handling, and a healthcheck script confirming the `pgvector` extension.

12/07/2026 - Phase 1 (Corpus acquisition) scraper: sitemap-based discovery of the COMP/SENG course + specialisation corpus (~216 docs) and a polite, cache-to-disk scraper extracting each page's `__NEXT_DATA__` payload.

12/07/2026 - Added GitHub Actions CI (tests + Docker image build) running on pushes to main and all pull requests. Deploy (CD) and eval jobs are deferred to Phases 7 and 5 per the roadmap.

12/07/2026 - Completed Phase 1 (Corpus acquisition): scraped 216 COMP/SENG course + specialisation documents (0 failures) into `data/raw/`, rerunnable without re-scraping.

12/07/2026 - Phase 2 (Chunking + ingestion): structure-aware chunker splitting each document by section semantics (overview / enrolment conditions / offering / learning outcomes / structure), an enrolment-rule parser extracting rule type + referenced course codes, local `bge-small-en-v1.5` embeddings, and an idempotent ingest into a `chunks` table with a `vector(384)` column and a generated `tsvector` (GIN-indexed) — the substrate for Phase 3 hybrid retrieval.

13/07/2026 - Completed Phase 3 (Hybrid retrieval): the RRF SQL query (`app/retrieval.py:hybrid_search`) fusing a vector-KNN CTE and a `ts_rank` FTS CTE with Reciprocal Rank Fusion in one round-trip, with `vector_search`/`fts_search` as single-method baselines. Iterated against a fixed 15-probe set (`scripts/probe_retrieval.py`) until fused results beat the baselines by inspection — hybrid gets the right chunk in the top 3 on 14/15 probes (vector 9/15, FTS 14/15), recovering 5 that vector alone missed. Surfaced two lexical fixes (OR-rewritten `plainto_tsquery`, length-normalised `ts_rank`); the *why* is written up in [docs/RETRIEVAL.md](docs/RETRIEVAL.md).

13/07/2026 - Completed Phase 4 (Service): a FastAPI `/ask` endpoint (`app/`) that embeds the query off the event loop, runs the hybrid RRF query, and streams a grounded, citation-marked answer over SSE (`meta` → `token` → `done`). Generation is DeepSeek V4 Flash behind a swappable provider interface (`app/provider.py`); a citation-enforcing prompt maps inline `[n]` markers to chunk IDs + source URLs. Below the refusal threshold it skips generation and returns the nearest matches. A shared asyncpg pool backs it, and every request — answered or refused — writes one `query_logs` row (retrieval/generation latency split, token counts, dollar cost, retrieved chunk IDs). Verified end-to-end against the real stack.

13/07/2026 - Completed Phase 5 (Evaluation): a 34-question hand-verified golden set and a one-command eval harness (`scripts/eval.py`) reporting retrieval hit-rate@k (fused vs vector vs FTS, split by query phrasing), a refusal-threshold sweep, and answer groundedness via a DeepSeek LLM-as-judge. The headline, reported straight: on this code+title-prefixed corpus a well-tuned FTS is the strongest single retriever and hybrid does not beat it — hybrid's measured value is robustness and semantic recovery on genuine paraphrases. Tuned `refusal_threshold` to 0.030 from the sweep. Eval runs manually by design (not in CI); its scoring logic is unit-tested. See the [Evaluation](#evaluation) section.
