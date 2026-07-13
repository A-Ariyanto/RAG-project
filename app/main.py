"""FastAPI service exposing the streaming, citation-marked /ask endpoint.

    docker compose up                          # serves on :8000
    curl -N "localhost:8000/ask?query=In which terms is COMP3311 offered?"

The lifespan opens one asyncpg pool (and creates query_logs) for the process;
`/ask` runs the retrieve → refuse-or-generate → SSE pipeline in `app.service`.
Both GET (query string, easy to curl) and POST (JSON body) are accepted.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import asyncpg
from fastapi import Depends, FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.config import settings
from app.db import db, get_conn
from app.provider import DeepSeekProvider, Provider
from app.service import answer_events


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect(settings.database_url)
    try:
        yield
    finally:
        await db.disconnect()


app = FastAPI(title="UNSW Handbook RAG", lifespan=lifespan)


def get_provider() -> Provider:
    """DI seam: overridable in tests to stream canned tokens without the network."""
    return DeepSeekProvider(settings)


class AskRequest(BaseModel):
    query: str


def _stream(query: str, conn: asyncpg.Connection, provider: Provider) -> StreamingResponse:
    return StreamingResponse(
        answer_events(conn, provider, query, settings),
        media_type="text/event-stream",
        # Disable proxy buffering so tokens reach the client as they stream.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/ask")
async def ask_get(
    query: str,
    conn: asyncpg.Connection = Depends(get_conn),
    provider: Provider = Depends(get_provider),
) -> StreamingResponse:
    return _stream(query, conn, provider)


@app.post("/ask")
async def ask_post(
    body: AskRequest,
    conn: asyncpg.Connection = Depends(get_conn),
    provider: Provider = Depends(get_provider),
) -> StreamingResponse:
    return _stream(body.query, conn, provider)


@app.get("/healthz")
async def healthz(conn: asyncpg.Connection = Depends(get_conn)) -> dict:
    """Liveness + DB reachability for compose/Cloud Run health checks."""
    await conn.fetchval("SELECT 1")
    return {"status": "ok"}
