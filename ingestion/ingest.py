"""Ingest raw handbook documents into the `chunks` table.

Pipeline: apply the schema (idempotent) → read every `data/raw/*.json` →
chunk each document (`ingestion.chunk`) → embed all chunk texts in one batch
(`ingestion.embed`) → write to Postgres.

Idempotent by design: each document's chunks are replaced atomically
(delete-by-doc + insert inside a transaction), so a rerun refreshes the table
and handles a document whose chunk set changed. "Rerun = refresh."

Run inside the app container:
    docker compose exec app python -m ingestion.ingest
    docker compose exec app python -m ingestion.ingest --limit 5   # smoke test
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import asyncpg

from app.config import settings
from ingestion.chunk import Chunk, chunk_document
from ingestion.embed import EMBED_DIM, embed_texts

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _load_docs(in_dir: Path, limit: int | None) -> list[dict]:
    """Read every raw JSON document from disk, sorted for deterministic order."""
    paths = sorted(in_dir.glob("*.json"))
    if limit is not None:
        paths = paths[:limit]
    return [json.loads(p.read_text(encoding="utf-8")) for p in paths]


def _vector_literal(row: list[float]) -> str:
    """Format an embedding as a pgvector text literal: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{x:.7g}" for x in row) + "]"


def _sql_statements(sql: str) -> list[str]:
    """Split schema.sql into executable statements, dropping comment-only fragments.

    asyncpg runs one statement per execute(), and splitting on ';' can leave a
    trailing block of only `--` comments (e.g. the closing note in schema.sql);
    those have no runnable SQL, so skip them. Our DDL has no ';' inside literals.
    """
    statements: list[str] = []
    for raw in sql.split(";"):
        code = "\n".join(
            line for line in raw.splitlines() if not line.strip().startswith("--")
        ).strip()
        if code:
            statements.append(code)
    return statements


async def _apply_schema(conn: asyncpg.Connection) -> None:
    """Apply schema.sql idempotently (each statement guarded with IF NOT EXISTS)."""
    for statement in _sql_statements(SCHEMA_PATH.read_text(encoding="utf-8")):
        await conn.execute(statement)


async def _write_document(
    conn: asyncpg.Connection, doc_code: str, career: str, rows: list[tuple]
) -> None:
    """Replace all chunks for one (doc_code, career) atomically."""
    async with conn.transaction():
        await conn.execute(
            "DELETE FROM chunks WHERE doc_code = $1 AND career = $2", doc_code, career
        )
        await conn.executemany(
            """
            INSERT INTO chunks (
                doc_code, career, content_type, title, section_type, text,
                credit_points, offering_terms, rule_type, referenced_codes,
                source_url, scraped_at, embedding
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::vector
            )
            """,
            rows,
        )


def _row(chunk: Chunk, embedding: list[float]) -> tuple:
    """Map a Chunk + its embedding to the INSERT parameter tuple."""
    return (
        chunk.doc_code,
        chunk.career,
        chunk.content_type,
        chunk.title,
        chunk.section_type,
        chunk.text,
        chunk.credit_points,
        chunk.offering_terms or None,
        chunk.rule_type,
        chunk.referenced_codes or None,
        chunk.source_url,
        # asyncpg binds timestamptz from a datetime, not an ISO string.
        datetime.fromisoformat(chunk.scraped_at) if chunk.scraped_at else None,
        _vector_literal(embedding),
    )


async def ingest(in_dir: Path, limit: int | None) -> None:
    docs = _load_docs(in_dir, limit)
    print(f"Loaded {len(docs)} documents from {in_dir}/")

    # Chunk every document up front, tracking which chunks belong to which doc.
    all_chunks: list[Chunk] = []
    per_doc: list[tuple[str, str, list[int]]] = []  # (code, career, chunk indices)
    for doc in docs:
        start = len(all_chunks)
        chunks = chunk_document(doc)
        all_chunks.extend(chunks)
        per_doc.append((doc.get("code", ""), doc.get("career", ""), list(range(start, len(all_chunks)))))

    print(f"Chunked into {len(all_chunks)} chunks; embedding ({EMBED_DIM}-dim)…")
    embeddings = embed_texts([c.text for c in all_chunks])

    conn = await asyncpg.connect(settings.database_url)
    try:
        await _apply_schema(conn)
        sections: Counter[str] = Counter()
        for code, career, idxs in per_doc:
            rows = [_row(all_chunks[i], embeddings[i].tolist()) for i in idxs]
            for i in idxs:
                sections[all_chunks[i].section_type] += 1
            await _write_document(conn, code, career, rows)

        total = await conn.fetchval("SELECT count(*) FROM chunks")
        embedded = await conn.fetchval("SELECT count(*) FROM chunks WHERE embedding IS NOT NULL")
    finally:
        await conn.close()

    print(f"\nDone: {len(docs)} docs → {len(all_chunks)} chunks written; table now has {total} rows ({embedded} embedded).")
    for section, n in sorted(sections.items()):
        print(f"  {section:22} {n}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="in_dir", type=Path, default=Path("data/raw"), help="Raw docs dir (default: data/raw)")
    parser.add_argument("--limit", type=int, default=None, help="Ingest only the first N docs (smoke test)")
    args = parser.parse_args()
    asyncio.run(ingest(args.in_dir, args.limit))


if __name__ == "__main__":
    main()
