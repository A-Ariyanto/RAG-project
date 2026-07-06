# Implementation Plan — UNSW Handbook RAG project

## The project
A RAG question-answering app over the UNSW Handbook (CSE courses). A student asks things like "I've done COMP1531 and COMP2521 — can I enrol in COMP3311 in T1?" and gets a short grounded answer with citations linking to the exact source sections. I'm the primary user; it must be genuinely usable during enrolment.

At the product level it's a chatbot; the engineering substance is a measurable retrieval system: ingestion (data engineering) → hybrid search (the hard part) → evaluation (what almost nobody does) → generation (one prompt).

## Non-negotiable requirements (the bar)
1. **Not a thin wrapper.** Structure-aware chunking driven by the corpus's real messiness (prerequisite chains, term offerings, UOC rules, enrolment-rule strings), with metadata per chunk: course code, section type, source URL, scrape date.
2. **Hybrid retrieval in one database.** pgvector column + tsvector column with GIN index on the same chunks table. Fusion via Reciprocal Rank Fusion in a single SQL query (CTE for vector KNN, CTE for ts_rank, joined with RRF scoring). This query is the centerpiece of the README.
3. **Eval pipeline.** ~30 golden Q&A pairs labeled with gold chunk IDs — LLM-drafted but every one hand-verified against source (~24 answerable + ~6 deliberately unanswerable where correct behavior is refusal; grow toward 50 later). Metrics reported in the README: retrieval hit rate@k for fused vs vector-only vs FTS-only, answer groundedness via LLM-as-judge, and refusal accuracy. Eval script runs in CI.
4. **Grounded citations + refusal.** Answers carry inline markers [1], [2] mapped to chunk IDs and source URLs. If the top fused retrieval score is below a tuned threshold, skip generation and return "I don't have enough information" with nearest matches shown.
5. **Production hygiene.** Fully containerized with Docker Compose from day one (runs 100% locally); SSE streaming responses; a query_logs table capturing per-query latency breakdown (retrieval vs generation), token counts, dollar cost, and retrieved chunk IDs. Cloud deployment is the final phase, not a prerequisite.

## Stack (decided)
- Backend: FastAPI, async SQLAlchemy/asyncpg, Pydantic
- Database: PostgreSQL with pgvector (one DB, both retrieval jobs; `pgvector/pgvector` Docker image)
- Embeddings: **local** via sentence-transformers (e.g. `bge-small-en-v1.5`) — free, no API key, CI can embed queries without secrets
- Generation: a cheap hosted model behind a **provider interface** (one function: prompt in → token stream out). Candidate: Gemini free tier via AI Studio; swappable to any paid cheap model later without touching the rest of the code. Final choice deferred to Phase 4.
- Frontend: deliberately minimal single-page React chat view — the backend is the star
- Infra: Docker Compose for all local dev; GitHub Actions CI (tests + evals); GCP Cloud Run + Cloud SQL **only in the final phase**

## Architecture (decided)
Two pipelines sharing one Postgres:
- **Ingestion (offline, rerunnable):** scraper (handbook data via the CourseLoop API behind handbook.unsw.edu.au — the site is a client-rendered SPA, so raw HTML is empty; fall back to Playwright if the API isn't callable directly) → parser → structure-aware chunker with metadata → write embedding to vector column and text to tsvector column.
- **Query path:** /ask endpoint → embed query → single hybrid RRF SQL query → top-k chunks to LLM with citation-enforcing prompt → confidence check → SSE stream → log to query_logs.
- **Evals:** script hitting the same endpoint/tables, run in CI.

---

## Phases (sequenced by dependency, not dates — each phase has an exit criterion; don't start the next until it's met)

### Phase 0 — Scaffold
Repo layout, Docker Compose with Postgres (pgvector image) + a Python container, `.env` handling, README skeleton.
**Exit:** `docker compose up` gives a running Postgres I can psql into; a hello-world script in the app container connects to it.

### Phase 1 — Corpus acquisition
Discover the CourseLoop API request in browser DevTools (Network tab on a handbook course page — data domain is `api-ap-southeast-2.prod.courseloop.com`). If it's callable with plain requests, the scraper consumes JSON; if not, fall back to Playwright rendering. Scrape CSE only: COMP/SENG course pages + CS program pages (~300 docs). Store raw responses to disk so parsing is rerunnable without re-scraping. Be polite: rate-limit, cache, identify the client.
**Cut from v1:** course outline PDFs (moved to roadmap — biggest time sink, least predictable).
**Exit:** ~300 raw documents on disk, spot-checked that prerequisites, term offerings, UOC, and enrolment rules are present in the data.

### Phase 2 — Chunking + ingestion
Chunks table schema (id, course code, section type, text, source URL, scrape date, `vector` column, generated `tsvector` column with GIN index). Structure-aware chunker: split by section semantics (overview / conditions-for-enrolment / offering terms / rules), not by fixed token windows; parse enrolment-rule strings ("Prerequisite: COMP1531 AND (COMP2521 OR MTRN2500)") into queryable metadata. Embed locally with sentence-transformers; ingest script is idempotent (rerun = refresh).
**Exit:** chunks table populated; sampled chunks read as self-contained, correctly attributed units.

### Phase 3 — Hybrid retrieval (the centerpiece)
The single RRF SQL query: CTE for vector KNN, CTE for `ts_rank`, joined with RRF scoring. Iterate from a plain script (no API) against a fixed set of ~15 probe queries until fused results beat vector-only and FTS-only by inspection. Do not touch FastAPI until this works.
**Exit:** for the probe queries, the right chunk is in the top 3 nearly always, and I can articulate *why* hybrid beats each single method on at least a few queries.

### Phase 4 — Service
FastAPI app: `/ask` endpoint → embed query → RRF query → refusal threshold check (threshold as config, tuned in Phase 5) → generation via the provider interface with a citation-enforcing prompt → SSE stream → query_logs middleware (latency split retrieval/generation, token counts, cost, retrieved chunk IDs). Pick and wire the generation provider here (Gemini free tier first candidate). Learn FastAPI idioms as they come up: async SQLAlchemy sessions via dependency injection (vs DRF's request-scoped ORM), `StreamingResponse` for SSE.
**Exit:** `curl -N localhost:8000/ask` streams a grounded, citation-marked answer; refusals return nearest matches; every query lands a row in query_logs.

### Phase 5 — Evaluation
Golden set: ~30 hand-verified Q&A pairs with gold chunk IDs (LLM-drafted, every one checked against source), including ~6 unanswerable. Eval script reports hit rate@k for fused vs vector-only vs FTS-only, groundedness via LLM-as-judge, refusal accuracy. Tune the refusal threshold against this set. Wire into GitHub Actions CI (local embeddings mean CI needs no embedding key; judge calls need one secret or a free-tier key).
**Exit:** eval table generated by one command; CI runs it; numbers are in the README.

### Phase 6 — Minimal frontend
Single-page React chat view consuming the SSE stream, rendering citations as links to source URLs. Timebox hard — the backend is the star.
**Exit:** I can ask a question in a browser and click a citation through to the handbook.

### Phase 7 — Deployment (last, deliberately)
Cloud Run (scale-to-zero) + Cloud SQL Postgres with pgvector, reusing the same containers from Compose. GitHub Actions deploy job. README finalized, leading with the eval table and the RRF query.
**Exit:** public URL answers a real enrolment question; README tells the whole story.

---

## Explicitly cut from v1
Course outline PDFs, reranking, query rewriting, multi-turn memory, auth, corpus versioning, non-CSE faculties.

## Roadmap after v1 (one eval-measured change at a time)
1. Course outline PDFs — the messy-parsing story, now with an eval to prove the corpus addition helps
2. Cross-encoder reranker — report hit-rate delta before/after in the README
3. Query rewriting for vague questions
4. Corpus expansion or swap to a messier domain (e.g. Australian visa rules, with versioning)

## Working agreement
Work incrementally — one component at a time, with me running and verifying each before moving on. Every phase ends with something I ran myself.
