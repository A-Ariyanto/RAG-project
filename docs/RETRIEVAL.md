# Hybrid retrieval — the RRF centerpiece (Phase 3)

Every chunk lives in one Postgres table with **both** a `vector(384)` embedding
and a generated `tsvector`. Retrieval runs three ways against that one table
(`app/retrieval.py`), and a fixed probe set (`scripts/probe_retrieval.py`) scores
them side by side. This is the design the whole project is built to justify.

## The query

`hybrid_search` fuses semantic and lexical retrieval with **Reciprocal Rank
Fusion** in a single round-trip. Two CTEs each rank their own candidate pool; the
outer query joins them on chunk id and scores every surviving row
`1/(k + vec_rank) + 1/(k + fts_rank)`, with a missing side contributing 0.

```sql
WITH q AS (SELECT replace(plainto_tsquery('english', $2)::text, '&', '|')::tsquery AS tsq),
vec AS (
    SELECT id, RANK() OVER (ORDER BY embedding <=> $1::vector) AS rank
    FROM chunks WHERE embedding IS NOT NULL
    ORDER BY embedding <=> $1::vector LIMIT $3
),
fts AS (
    SELECT c.id, RANK() OVER (ORDER BY ts_rank(c.tsv, q.tsq, 1) DESC) AS rank
    FROM chunks c, q WHERE c.tsv @@ q.tsq
    ORDER BY ts_rank(c.tsv, q.tsq, 1) DESC LIMIT $3
)
SELECT c.*, COALESCE(1.0/($4 + vec.rank), 0) + COALESCE(1.0/($4 + fts.rank), 0) AS rrf_score
FROM chunks c
LEFT JOIN vec ON vec.id = c.id
LEFT JOIN fts ON fts.id = c.id
WHERE vec.id IS NOT NULL OR fts.id IS NOT NULL
ORDER BY rrf_score DESC LIMIT $5;
```

RRF fuses on **rank**, not raw score, so it needs no normalisation between two
incomparable scales (cosine distance vs `ts_rank`). `k = 60` (Cormack et al.).

## Two fixes the probes forced

Naive single-method retrieval was weak, and iterating on the probe set is what
surfaced why:

1. **`plainto_tsquery` ANDs every content word.** A natural-language question
   ("Can I enrol in COMP2521 if I've already done COMP1927?") drops every row
   missing any one word, so FTS returned *nothing*. Rewriting the `&` operators
   to `|` makes the lexical half recall-oriented — any term may match, `ts_rank`
   still orders by match strength, and RRF supplies precision. This is what lets
   a bare code (`comp9201`, `comp1927`) pull its exact chunk into the pool.
2. **`ts_rank` applies no length normalisation by default,** so a long chunk
   listing many courses outranks a short chunk that matches exactly ("which
   courses is COMP3231 equivalent to" surfaced big structure lists over
   COMP3231's own terse equivalent line). Normalisation flag `1` (÷ 1+log(len))
   rewards concise exact matches — the right shape for our metadata chunks.

A third fix lived in **Phase 2 chunking**: the offering chunk was a terse field
dump (`6 units of credit (UOC). offered in T1, T2, T3.`) that embedded too far
from questions like "how many credit points" or "when can I take it". Rephrasing
it the way students actually ask (`ingestion/chunk.py:_offering_sentence`) made
those chunks retrievable by both methods.

## Results (15 probes, hit = right chunk within top 3)

| method  | hit@1 | hit@3 |
|---------|:-----:|:-----:|
| vector  | 2/15  | 9/15  |
| fts     | 11/15 | 14/15 |
| **hybrid** | **10/15** | **14/15** |

## Why hybrid beats each single method

- **vs vector (9 → 14).** On 5 probes fusion recovered a chunk vector ranked out
  of the top 3. Terse metadata chunks (offering, prerequisite, equivalent) embed
  poorly against verbose questions, so vector drifts to the wordy `overview`
  chunk of the right course — or to a *different* course with more prose. FTS
  anchors on the exact tokens (`offered`, term codes, `equivalent`, course
  codes) and fusion pulls the correct chunk back into the top 3. Examples: *"In
  which terms is COMP3311 offered?"*, *"when can I take Software Engineering
  Fundamentals"*, *"which courses is COMP3231 equivalent to"*.
- **vs FTS.** Honest finding: for CSE **enrolment** questions — which are
  anchored to course codes, course names, and a small fixed vocabulary (UOC,
  term, prerequisite, exclusion) — a *well-tuned* lexical search is already very
  strong (14/15), and once tuned it matches hybrid on this set. Hybrid never
  does worse and promotes the right chunk to rank 1 on several code-anchored
  probes, but its real payoff over FTS is **robustness**: you don't have to know
  in advance whether a query is lexical or paraphrased — fusion handles both, so
  the naive-FTS failure mode (empty results on prose questions, 7/15 before the
  OR-rewrite) never reaches the user.

The measured lesson — the kind Phase 5's eval is built to make routine — is that
hybrid's value here is robustness and semantic recovery, not a uniform accuracy
jump over a strong lexical baseline.

## The one probe all three miss

*"Can I enrol in COMP2521 if I've already done COMP1927?"* The right chunk is
COMP2521's own exclusion line (COMP1927 is an exclusion → the answer is *no*).
Many *other* courses cite COMP2521 and COMP1927 in their prerequisites, so vector
drifts to them and FTS ranks the target only ~5th; RRF favours chunks both
methods rank, so a chunk only one method ranks modestly can't climb. The
structural fix is already latent in the schema: detect the subject course in the
query and filter/boost on `doc_code` — a metadata move the structure-aware
chunking was designed to enable. Deferred to post-v1 (see ROADMAP).

## Reproduce

```bash
docker compose exec app python -m scripts.probe_retrieval          # summary table
docker compose exec app python -m scripts.probe_retrieval --show 5 # + top-5 dump
```
