"""Async Postgres access for the service: a shared asyncpg pool + query_logs.

The Phase 3 retrieval query (`app.retrieval`) is raw SQL returning `asyncpg`
records, so the service stays on asyncpg end-to-end rather than layering an ORM
over it. FastAPI holds one pool for the process lifetime (created in the app
lifespan) and hands each request a pooled connection via the `get_conn`
dependency.

`query_logs` is the service's own table — created here on startup, idempotently,
independent of the ingestion schema that owns `chunks`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import asyncpg

# Per-query observability: one row per /ask, capturing the retrieval/generation
# latency split, token counts + estimated cost, and exactly which chunks were
# retrieved (so an answer can be traced back to its grounding). Guarded so app
# startup is a no-op on an already-built DB.
QUERY_LOGS_DDL = """
CREATE TABLE IF NOT EXISTS query_logs (
    id                  BIGSERIAL PRIMARY KEY,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    query               TEXT NOT NULL,
    refused             BOOLEAN NOT NULL,
    top_rrf_score       DOUBLE PRECISION,       -- fused score of the top chunk
    retrieved_chunk_ids BIGINT[],               -- chunks handed to the generator
    retrieval_ms        DOUBLE PRECISION,       -- embed + hybrid SQL wall time
    generation_ms       DOUBLE PRECISION,       -- streaming completion wall time
    prompt_tokens       INTEGER,
    completion_tokens   INTEGER,
    cost_usd            DOUBLE PRECISION,
    model               TEXT
);
"""


class Database:
    """Owns the asyncpg pool for the app's lifetime."""

    def __init__(self) -> None:
        self.pool: asyncpg.Pool | None = None

    async def connect(self, dsn: str) -> None:
        self.pool = await asyncpg.create_pool(dsn, min_size=1, max_size=10)
        async with self.pool.acquire() as conn:
            await conn.execute(QUERY_LOGS_DDL)

    async def disconnect(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None


# Module-level singleton; wired to a real pool in the FastAPI lifespan.
db = Database()


async def get_conn() -> AsyncIterator[asyncpg.Connection]:
    """FastAPI dependency: yield a pooled connection, returned on request exit."""
    assert db.pool is not None, "DB pool not initialised — is the app lifespan running?"
    async with db.pool.acquire() as conn:
        yield conn


async def insert_query_log(
    conn: asyncpg.Connection,
    *,
    query: str,
    refused: bool,
    top_rrf_score: float | None,
    retrieved_chunk_ids: list[int],
    retrieval_ms: float | None,
    generation_ms: float | None,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    cost_usd: float | None,
    model: str | None,
) -> None:
    """Write one row describing a completed /ask, including refusals."""
    await conn.execute(
        """
        INSERT INTO query_logs (
            query, refused, top_rrf_score, retrieved_chunk_ids,
            retrieval_ms, generation_ms, prompt_tokens, completion_tokens,
            cost_usd, model
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """,
        query,
        refused,
        top_rrf_score,
        retrieved_chunk_ids,
        retrieval_ms,
        generation_ms,
        prompt_tokens,
        completion_tokens,
        cost_usd,
        model,
    )
