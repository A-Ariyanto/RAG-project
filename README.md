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

## Repo layout

| Path | Purpose |
|---|---|
| `app/` | Application package (config now; FastAPI service in Phase 4) |
| `ingestion/` | Scraper → parser → chunker → embed pipeline (Phases 1–2) |
| `scripts/` | Operational scripts (`healthcheck.py` today) |
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
