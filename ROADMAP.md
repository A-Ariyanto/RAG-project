# Roadmap — UNSW Handbook RAG project

A RAG question-answering app over the UNSW Handbook (CSE courses): ask "I've done COMP1531 and COMP2521 — can I enrol in COMP3311 in T1?" and get a short grounded answer with citations to the exact source sections.

This file is the **working tracker**: what to build, in what order, and what's done. The design rationale (requirements bar, stack decisions, architecture) lives in [IMPLEMENTATION.md](IMPLEMENTATION.md) — read that for *why*, this for *what*.

## Status legend

| Marker | Meaning |
|---|---|
| 🔲 | Not started |
| 🔨 | In progress |
| ✅ | Done |

## Phase overview

| Phase | Goal | Status |
|---|---|---|
| [0 — Scaffold](#phase-0--scaffold) | Repo + Docker Compose skeleton with a running Postgres | ✅ |
| [1 — Corpus acquisition](#phase-1--corpus-acquisition) | ~300 raw handbook documents on disk | ✅ |
| [2 — Chunking + ingestion](#phase-2--chunking--ingestion) | Populated chunks table with embeddings + tsvector | ✅ |
| [3 — Hybrid retrieval](#phase-3--hybrid-retrieval-the-centerpiece) | The RRF SQL query, proven better than either method alone | ✅ |
| [4 — Service](#phase-4--service) | Streaming `/ask` endpoint with citations, refusal, and query logging | ✅ |
| [5 — Evaluation](#phase-5--evaluation) | Golden set + one-command eval (manual, not CI), numbers in the README | ✅ |
| [6 — Minimal frontend](#phase-6--minimal-frontend) | Browser chat view with clickable citations | 🔲 |
| [7 — Deployment](#phase-7--deployment) | Public URL on Cloud Run + finished README | 🔲 |

Phases are sequenced by dependency, not dates. Each phase has an exit criterion — don't start the next until it's met.

---

## Phase 0 — Scaffold

**Goal:** a repo layout and local environment where `docker compose up` gives a working Postgres and an app container that can talk to it.

**Tasks**
- [x] Lay out the repo skeleton (app package, ingestion dir, scripts dir, tests dir)
- [x] Write `docker-compose.yml`: Postgres from the `pgvector/pgvector` image + a Python app container
- [x] Set up `.env` handling with a committed `.env.example` (DB credentials, ports)
- [x] Write a hello-world script in the app container that connects to Postgres and confirms the `pgvector` extension is available
- [x] Update the README skeleton (setup instructions: clone, `.env`, `docker compose up`)

**Exit criterion:** `docker compose up` gives a running Postgres I can psql into; a hello-world script in the app container connects to it.

---

## Phase 1 — Corpus acquisition

**Goal:** ~300 raw CSE handbook documents (COMP/SENG courses + CS programs) stored on disk, rerunnable without re-scraping.

**Tasks**
- [x] Discover the corpus URL list — used the handbook sitemap (robots-advertised) rather than the CloudFront-gated CourseLoop API, filtered to COMP/SENG courses + specialisations
- [x] Test whether it's callable with plain `requests` — yes; the SSR `__NEXT_DATA__` payload carries everything, no Playwright fallback needed
- [x] Build the scraper: rate-limited, response caching (skip-if-exists), identifies the client politely
- [x] Scrape COMP/SENG course pages + specialisation pages (216 docs, 0 failures)
- [x] Store raw responses to disk so parsing is rerunnable without re-scraping
- [x] Spot-check the raw data: prerequisites (`enrolment_rules[].description`), term offerings, UOC, and enrolment rules are present

**Cut from this phase (v1):** course outline PDFs — moved to the post-v1 roadmap.

**Exit criterion:** ✅ 216 raw documents on disk (`data/raw/`, gitignored), spot-checked that prerequisites, term offerings, UOC, and enrolment rules are present in the data. (Corpus is COMP/SENG only per the v1 scope, so ~216 rather than ~300.)

---

## Phase 2 — Chunking + ingestion

**Goal:** a populated chunks table where every chunk is a self-contained, correctly attributed unit with an embedding and a tsvector.

**Tasks**
- [x] Design the chunks table schema (`ingestion/schema.sql`): id, doc_code, section_type, text, source_url, scraped_at, `vector(384)` column, generated `tsvector` column with GIN index, plus queryable rule metadata
- [x] Build the structure-aware chunker (`ingestion/chunk.py`): split by section semantics (overview / enrolment_conditions / offering / learning_outcomes / structure / additional), not fixed token windows; every chunk code+title prefixed so it stands alone
- [x] Parse enrolment-rule strings (`ingestion/rules.py`) into queryable metadata: rule_type + referenced course codes, raw boolean text kept verbatim (AST deferred)
- [x] Embed chunks locally with sentence-transformers `bge-small-en-v1.5` (`ingestion/embed.py`)
- [x] Write an idempotent ingest script (`ingestion/ingest.py`): per-doc delete+insert = refresh
- [x] Quality check: sample chunks and confirm they read as self-contained, correctly attributed units

**Exit criterion:** ✅ `chunks` table populated (954 chunks from 216 docs, all embedded + tsvector'd); sampled chunks read as self-contained, correctly attributed units; ingest is idempotent (rerun holds at 954 rows); rule metadata is queryable (`referenced_codes`, `offering_terms`).

---

## Phase 3 — Hybrid retrieval (the centerpiece)

**Goal:** a single hybrid SQL query where fused results demonstrably beat vector-only and FTS-only.

**Tasks**
- [x] Write the RRF SQL query (`app/retrieval.py:hybrid_search`): CTE for vector KNN, CTE for `ts_rank`, joined with Reciprocal Rank Fusion scoring; `vector_search`/`fts_search` expose the single-method baselines. Query-side embeddings use the bge instruction prefix (`ingestion/embed.py:embed_query`)
- [x] Assemble a fixed set of 15 probe queries (`scripts/probe_retrieval.py`) covering prerequisites, term offerings, UOC, and enrolment rules (exclusion/equivalent)
- [x] Iterate from a plain script (no API) comparing fused vs vector-only vs FTS-only — surfaced two lexical fixes (OR-rewritten `plainto_tsquery`, length-normalised `ts_rank`) and a Phase 2 chunk-text fix (natural-language offering sentence)
- [x] Write down *why* hybrid beats each single method ([docs/RETRIEVAL.md](docs/RETRIEVAL.md) — README material)

Do not touch FastAPI until this works.

**Exit criterion:** ✅ hybrid gets the right chunk in the top 3 on **14/15** probes (vector 9/15, FTS 14/15). Fusion recovers 5 probes vector alone ranked out of top 3; the honest finding is that for code/name-anchored enrolment queries a well-tuned FTS is a strong baseline and hybrid's edge is robustness + semantic recovery (see [docs/RETRIEVAL.md](docs/RETRIEVAL.md)). One probe all three miss (subject-course disambiguation) is documented and deferred.

---

## Phase 4 — Service

**Goal:** a streaming `/ask` endpoint that returns grounded, citation-marked answers, refuses when confidence is low, and logs every query.

**Tasks**
- [x] Scaffold the FastAPI app with a shared asyncpg pool via dependency injection (`app/main.py`, `app/db.py`). Deviated from the planned SQLAlchemy: the Phase 3 retrieval query is raw SQL returning asyncpg records, so an ORM would mean a second DB layer or a rewrite of the proven query — asyncpg end-to-end is cleaner here.
- [x] `/ask` endpoint: embed query (in a threadpool) → RRF query → top-k chunks (`app/service.py`; `hybrid_search` gained an optional precomputed-embedding arg so the async path doesn't block on torch)
- [x] Refusal threshold check as config (`settings.refusal_threshold`, tuned later in Phase 5): below threshold, skip generation and stream "I don't have enough information" with the nearest matches as citations
- [x] Build the provider interface (`app/provider.py` — `Provider.stream`, prompt in → `StreamEvent` token stream out); generation is DeepSeek V4 Flash via the DeepSeek OpenAI-compatible API (key in `.env`), swappable via base_url/model config
- [x] Write the citation-enforcing prompt (`app/prompt.py`): inline markers [1], [2] mapped to chunk IDs and source URLs (returned in the `meta` SSE event)
- [x] SSE streaming via `StreamingResponse` (`meta` → `token` → `done` events)
- [x] `query_logs` table + logging (`app/db.py`): latency split (retrieval vs generation), token counts, dollar cost, retrieved chunk IDs — one row per request, refusals included

**Exit criterion:** ✅ `curl -N localhost:8000/ask` streams a grounded, citation-marked answer; refusals return nearest matches; every query lands a row in query_logs. Verified end-to-end against the real stack: "In which terms is COMP3311 offered?" streamed token-by-token to `COMP3311 is offered in T1 and T2 [1].` with the `[1]` marker mapped to the COMP3311 offering source (top fused score 0.031); "What is the capital of France?" refused with the five nearest matches (0.0164 < 0.02 threshold); both wrote query_logs rows (the answered one: retrieval + generation split, 438+14 tokens, ~$0.00013 on deepseek-chat). 36 unit tests green. Placeholder `refusal_threshold` set to 0.02 (sits in the observed gap between in-domain ~0.031 and the out-of-domain 0.0164); Phase 5 tunes it against the golden set. Note: the first query after a container restart pays a one-off ~10s sentence-transformers model load on the embed step; subsequent queries retrieve in ~15–25ms.

---

## Phase 5 — Evaluation

**Goal:** a golden set and an eval script that produce the headline numbers for the README, running in CI.

**Tasks**
- [x] Draft golden Q&A pairs (`eval/golden.yaml`, 34: 28 answerable + 6 unanswerable), LLM-drafted then grounded in real chunks. Gold keyed by the **stable `(doc_code, section_type)` pair**, not `chunks.id` — ids churn on every re-ingest (per-doc delete+insert), so an id-based gold set would rot; this is the same key the Phase 3 probe harness matches on.
- [ ] Hand-verify every pair against the source handbook pages ← **owner: me** (facts are grounded in the ingested chunks and cross-checked, but a final pass against the live pages is the last verification step)
- [x] Eval script: retrieval hit rate@k for fused vs vector-only vs FTS-only, **split by query phrasing** (code-anchored vs name-only) + per-question fusion rescue/regression diagnostics
- [x] Eval script: answer groundedness via DeepSeek LLM-as-judge (grounded / cited / correct-decline)
- [x] Eval script: refusal accuracy on the unanswerable questions (threshold sweep)
- [x] Tune the refusal threshold against the golden set → **0.030** (sweep optimum 0.0306; sit just under to keep answerable recall at 28/28, prompt is the second net)
- [x] ~~Wire the eval script into GitHub Actions CI~~ → **cut: eval runs manually.** For a side project, a committed chunks fixture + pgvector-in-CI + paid judge calls per push isn't worth it. The eval's scoring *logic* is unit-tested in CI (`tests/test_eval.py`); the full *run* is one local command.
- [x] Put the eval results table in the README ([Evaluation](../README.md#evaluation) section)

**Exit criterion:** ✅ eval table generated by one command (`docker compose exec app python -m scripts.eval`); numbers are in the README. (Deviation from the original criterion: eval runs manually, not in CI — a deliberate side-project scoping decision.)

**Headline finding (reported straight):** on this corpus a well-tuned FTS is the strongest single retriever and hybrid does **not** beat it — every chunk is prefixed with its code + title, so course names are verbatim lexical tokens. Hybrid hit@3 82% vs FTS 89% vs vector 64%; fusion rescues 7 questions (paraphrase recovery) but regresses 4 (offering-section blending), netting just below FTS. Hybrid's measured value is robustness (never collapses like vector on code-anchored queries) + semantic recovery on genuine paraphrases. Groundedness 93%, citation 96%, refusal sweep optimum 0.96 balanced accuracy. A messier corpus (post-v1 roadmap) would widen fusion's edge; this one is a near-best case for lexical search, and the eval says so.

---

## Phase 6 — Minimal frontend

**Goal:** a browser chat view — deliberately minimal, the backend is the star. Timebox hard.

**Stack:** Vite + React + Tailwind, in a `frontend/` directory in this repo (same repo, same deploy story; served same-origin from FastAPI so no CORS config). Minimal now, but the tooling holds up if the UI grows later.

**Tasks**
- [ ] Scaffold `frontend/` with Vite + React + Tailwind
- [ ] Single-page chat view consuming the SSE stream
- [ ] Render citations as links to the source handbook URLs
- [ ] Serve the built bundle from FastAPI (static files, same origin)

**Exit criterion:** I can ask a question in a browser and click a citation through to the handbook.

---

## Phase 7 — Deployment

**Goal:** a public URL answering real enrolment questions, and a README that tells the whole story.

**Tasks**
- [ ] Deploy to Cloud Run (scale-to-zero) + Cloud SQL Postgres with pgvector, reusing the same containers from Compose
- [ ] GitHub Actions deploy job
- [ ] Finalize the README: lead with the eval table and the RRF query

**Exit criterion:** public URL answers a real enrolment question; README tells the whole story.

---

## Explicitly cut from v1

Course outline PDFs, reranking, query rewriting, multi-turn memory, auth, corpus versioning, non-CSE faculties.

## Roadmap after v1 (one eval-measured change at a time)

1. Course outline PDFs — the messy-parsing story, now with an eval to prove the corpus addition helps
2. Cross-encoder reranker — report hit-rate delta before/after in the README
3. Query rewriting for vague questions
4. Corpus expansion or swap to a messier domain (e.g. Australian visa rules, with versioning)
