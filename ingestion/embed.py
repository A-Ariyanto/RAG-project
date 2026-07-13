"""Local embeddings for chunk texts, via sentence-transformers `bge-small-en-v1.5`.

384-dimensional, CPU-friendly, and good enough that retrieval quality — not the
embedder — is the interesting problem. Runs locally so ingestion needs no API key.

`sentence-transformers` (and its torch dependency) is imported lazily inside the
functions so the pure chunker/rule modules and their unit tests never pull it in.

Passages are embedded as-is. bge models recommend a *query-side* instruction
prefix ("Represent this sentence for searching relevant passages:") applied only
to the query, not the stored passages — that belongs to Phase 3/4 retrieval, not
here. The model is cached under `models/` (gitignored) after first download.
"""

from __future__ import annotations

import os
from functools import lru_cache

MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBED_DIM = 384

# bge models are trained with an asymmetric instruction: the *query* is prefixed
# with this sentence, stored passages are not. Applying it lifts retrieval
# quality noticeably, so the query path (Phase 3/4) must use `embed_query`.
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

# Keep the HuggingFace cache inside the repo (mounted volume) so the ~130MB
# model download survives container restarts and isn't re-fetched every run.
_CACHE_DIR = os.environ.get("MODELS_DIR", "models")


@lru_cache(maxsize=1)
def _model():
    """Load (and cache) the SentenceTransformer once per process."""
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(MODEL_NAME, cache_folder=_CACHE_DIR)


def embed_texts(texts: list[str], *, batch_size: int = 64):
    """Embed passages → an (N, 384) float32 numpy array, L2-normalised.

    Normalised embeddings mean cosine similarity == inner product, which keeps
    the pgvector distance operator choice in Phase 3 simple.
    """
    if not texts:
        import numpy as np

        return np.empty((0, EMBED_DIM), dtype="float32")

    return _model().encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=True,
    ).astype("float32")


def embed_query(text: str):
    """Embed a single search query → a (384,) float32 numpy vector, L2-normalised.

    Applies the bge query instruction prefix (see `QUERY_INSTRUCTION`) that the
    stored passages deliberately omit. Progress bar suppressed — this is the
    single-vector, latency-sensitive query path, not a batch ingest.
    """
    return _model().encode(
        QUERY_INSTRUCTION + text,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    ).astype("float32")
