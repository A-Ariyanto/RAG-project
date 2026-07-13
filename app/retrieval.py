"""Hybrid retrieval over the `chunks` table — the Phase 3 centerpiece.

Three retrievers against the *same* table, sharing one query embedding:

* `vector_search` — pgvector KNN (`embedding <=> query`), semantic similarity.
* `fts_search`    — Postgres full-text `ts_rank` over the generated `tsvector`,
                    exact lexical match (course codes, term names, "UOC").
* `hybrid_search` — a single SQL query that fuses the two with **Reciprocal
                    Rank Fusion**: each doc scores `sum(1 / (k + rank_i))` over
                    the retrievers it appears in. RRF fuses on *rank*, not raw
                    scores, so it needs no score normalisation between two
                    incomparable scales (cosine distance vs `ts_rank`).

Why fusion wins here: semantic search nails paraphrased intent ("what do I need
before I can take…") but drifts on bare identifiers; lexical search nails exact
tokens ("COMP3311", "T2", "UOC") but is blind to synonyms. Enrolment questions
mix both, so neither single method is reliably top-3 — the fused ranking is.

The three functions return a common `Result` so the Phase 3 probe script can
line them up side by side; Phase 4's `/ask` calls `hybrid_search` directly.
"""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg

from ingestion.embed import embed_query

# RRF smoothing constant, k=60 from Cormack et al. (2009) and the de-facto
# default: large enough that no single top rank dominates the fusion, small
# enough that the head of each list still carries most of the weight.
RRF_K = 60

# How deep to take each retriever's candidate list before fusing. At ~1k rows an
# exact scan of the whole table is cheap, so we can afford a generous pool and
# let RRF do the sorting; only the fused top-k is returned to the caller.
DEFAULT_POOL = 50
DEFAULT_TOP_K = 10

# The lexical half's tsquery. `plainto_tsquery` ANDs every content word, so a
# single extra word in a natural-language question ("Can I enrol in COMP2521 if
# I've already done COMP1927?") drops every row missing any one of them and FTS
# returns nothing. Rewriting the '&' operators to '|' turns it into a recall-
# oriented OR query: any term may match, `ts_rank` still orders by how many and
# how strongly terms hit, and RRF supplies the precision. Crucially this lets a
# bare course code (comp9201, comp1927) pull its exact chunk into the pool — the
# lexical strength vector search lacks. plainto emits only single-lexeme ANDs
# (no phrase `<->`), so the text-level '&'→'|' swap is safe. `$1` is rebound per
# query via .replace() where the query-text parameter sits at a different index.
_OR_TSQUERY = "replace(plainto_tsquery('english', $1)::text, '&', '|')::tsquery"

# ts_rank normalization flag. Default (0) applies NO length normalization, so a
# long chunk that lists many courses outranks a short chunk that matches exactly
# — "which courses is COMP3231 equivalent to" then surfaces big structure lists
# over COMP3231's own terse equivalent line. Flag 1 divides by 1 + log(length),
# rewarding concise exact matches, which suits our short metadata chunks.
_TS_NORM = 1


@dataclass
class Result:
    """One retrieved chunk. Ranking fields are populated by whichever search ran."""

    id: int
    doc_code: str
    section_type: str
    title: str
    text: str
    source_url: str
    rrf_score: float | None = None  # hybrid_search: fused score
    vec_rank: int | None = None     # hybrid_search: 1-based rank in the vector list
    fts_rank: int | None = None     # hybrid_search: 1-based rank in the FTS list
    score: float | None = None      # single-method: cosine similarity / ts_rank


def _vector_literal(vec) -> str:
    """Format a query embedding as a pgvector text literal: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{x:.7g}" for x in vec) + "]"


def _result(record: asyncpg.Record, **extra) -> Result:
    return Result(
        id=record["id"],
        doc_code=record["doc_code"],
        section_type=record["section_type"],
        title=record["title"],
        text=record["text"],
        source_url=record["source_url"],
        **extra,
    )


async def vector_search(
    conn: asyncpg.Connection, query: str, *, top_k: int = DEFAULT_TOP_K
) -> list[Result]:
    """Vector-only KNN baseline. `score` is cosine similarity (1 − distance)."""
    qvec = _vector_literal(embed_query(query).tolist())
    rows = await conn.fetch(
        """
        SELECT id, doc_code, section_type, title, text, source_url,
               1 - (embedding <=> $1::vector) AS score
        FROM chunks
        WHERE embedding IS NOT NULL
        ORDER BY embedding <=> $1::vector
        LIMIT $2
        """,
        qvec,
        top_k,
    )
    return [_result(r, score=float(r["score"])) for r in rows]


async def fts_search(
    conn: asyncpg.Connection, query: str, *, top_k: int = DEFAULT_TOP_K
) -> list[Result]:
    """Full-text-only baseline. `score` is `ts_rank`; empty if nothing matches."""
    rows = await conn.fetch(
        f"""
        WITH q AS (SELECT {_OR_TSQUERY} AS tsq)
        SELECT id, doc_code, section_type, title, text, source_url,
               ts_rank(tsv, q.tsq, {_TS_NORM}) AS score
        FROM chunks, q
        WHERE tsv @@ q.tsq
        ORDER BY score DESC, id
        LIMIT $2
        """,
        query,
        top_k,
    )
    return [_result(r, score=float(r["score"])) for r in rows]


async def hybrid_search(
    conn: asyncpg.Connection,
    query: str,
    *,
    top_k: int = DEFAULT_TOP_K,
    pool: int = DEFAULT_POOL,
    rrf_k: int = RRF_K,
) -> list[Result]:
    """Fuse vector KNN + FTS with Reciprocal Rank Fusion in one SQL round-trip.

    Both CTEs rank their own candidate pool; the outer query FULL-joins them on
    chunk id and scores each surviving row `1/(k+vec_rank) + 1/(k+fts_rank)`,
    with a missing side contributing 0. A single query embedding is reused for
    the vector side and the raw text for the lexical side.
    """
    qvec = _vector_literal(embed_query(query).tolist())
    rows = await conn.fetch(
        f"""
        WITH q AS (SELECT {_OR_TSQUERY.replace('$1', '$2')} AS tsq),
        vec AS (
            SELECT id, RANK() OVER (ORDER BY embedding <=> $1::vector) AS rank
            FROM chunks
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> $1::vector
            LIMIT $3
        ),
        fts AS (
            SELECT c.id, RANK() OVER (ORDER BY ts_rank(c.tsv, q.tsq) DESC) AS rank
            FROM chunks c, q
            WHERE c.tsv @@ q.tsq
            ORDER BY ts_rank(c.tsv, q.tsq) DESC
            LIMIT $3
        )
        SELECT c.id, c.doc_code, c.section_type, c.title, c.text, c.source_url,
               COALESCE(1.0 / ($4 + vec.rank), 0.0)
                 + COALESCE(1.0 / ($4 + fts.rank), 0.0) AS rrf_score,
               vec.rank AS vec_rank,
               fts.rank AS fts_rank
        FROM chunks c
        LEFT JOIN vec ON vec.id = c.id
        LEFT JOIN fts ON fts.id = c.id
        WHERE vec.id IS NOT NULL OR fts.id IS NOT NULL
        ORDER BY rrf_score DESC, c.id
        LIMIT $5
        """,
        qvec,
        query,
        pool,
        rrf_k,
        top_k,
    )
    return [
        _result(
            r,
            rrf_score=float(r["rrf_score"]),
            vec_rank=r["vec_rank"],
            fts_rank=r["fts_rank"],
        )
        for r in rows
    ]
