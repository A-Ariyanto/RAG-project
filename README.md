# RAG-project

A self made project that is applicibale and can be used to answer questions about the UNSW Handbook (CSE courses). The project is a RAG question-answering app that allows students to ask questions like "I've done COMP1531 and COMP2521 — can I enrol in COMP3311 in T1?" and receive a short grounded answer with citations linking to the exact source sections. The primary user of this project is the creator, and it is designed to be genuinely usable during enrolment.

## Tech Stack

- Backend: FastAPI, async SQLAlchemy/asyncpg, Pydantic
- Database: PostgreSQL with pgvector (one DB, both retrieval jobs; `pgvector ` extension is required)

Credit to Claude as I will work alongside it to make this project with the goal of upskilling myself in the field of AI and LLMs.

## Project Docs

- [IMPLEMENTATION.md](IMPLEMENTATION.md) — design rationale: requirements, stack decisions, architecture
- [ROADMAP.md](ROADMAP.md) — trackable phase-by-phase task checklists and current status

## Progress Report

07/07/2026 - Setup the project repository after planning and discussing with Claude.

12/07/2026 - Added ROADMAP.md, a trackable phase-by-phase task checklist for the build.
