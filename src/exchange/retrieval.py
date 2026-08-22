"""Hybrid retrieval: BM25 for exact terms, embeddings for intent, fused by RRF.

Sparse retrieval catches SKUs, materials, and brand names. Dense retrieval
catches paraphrase — 'eco packaging' finding 'biodegradable mailers'. Neither
alone is sufficient for a descriptive bid, so both run and their rankings are
combined by reciprocal rank fusion.
"""
from __future__ import annotations

from typing import Callable

from rank_bm25 import BM25Okapi

Embedder = Callable[[list[str]], list[list[float]]]


def rrf_fuse(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal rank fusion.

    Each ranking contributes 1/(k + rank) to a document's score. Rank position
    matters; the underlying scores do not, which is what lets two retrievers
    with incomparable score scales be combined at all.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda pair: pair[1], reverse=True)


def default_embedder() -> Embedder:
    """Load model2vec lazily — importing it and fetching weights is slow.

    model2vec gives distilled static embeddings without a torch dependency,
    which matters because sentence-transformers cannot be installed here.
    Vectors are L2-normalised on the way out because `_cosine` below is a
    plain dot product and assumes unit vectors.
    """
    import numpy as np
    from model2vec import StaticModel

    model = StaticModel.from_pretrained("minishlab/potion-base-8M")

    def embed(texts: list[str]) -> list[list[float]]:
        vectors = np.asarray(model.encode(texts), dtype=float)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return (vectors / norms).tolist()

    return embed


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class HybridIndex:
    def __init__(self, embed_fn: Embedder | None = None) -> None:
        self._embed = embed_fn or default_embedder()
        self._doc_ids: list[str] = []
        self._texts: list[str] = []
        self._bm25: BM25Okapi | None = None
        self._vectors: list[list[float]] = []

    def index(self, docs: list[tuple[str, str]]) -> None:
        self._doc_ids = [doc_id for doc_id, _ in docs]
        self._texts = [text for _, text in docs]
        if not docs:
            self._bm25 = None
            self._vectors = []
            return
        self._bm25 = BM25Okapi([t.lower().split() for t in self._texts])
        self._vectors = self._embed(self._texts)

    def search(self, query: str, top_k: int = 5) -> list[tuple[str, float]]:
        if not self._doc_ids:
            return []

        sparse_scores = self._bm25.get_scores(query.lower().split())
        sparse_ranking = [
            self._doc_ids[i]
            for i in sorted(
                range(len(self._doc_ids)), key=lambda i: sparse_scores[i], reverse=True
            )
            if sparse_scores[i] > 0
        ]

        query_vec = self._embed([query])[0]
        dense_scores = [_cosine(query_vec, v) for v in self._vectors]
        dense_ranking = [
            self._doc_ids[i]
            for i in sorted(
                range(len(self._doc_ids)), key=lambda i: dense_scores[i], reverse=True
            )
        ]

        return rrf_fuse([sparse_ranking, dense_ranking])[:top_k]
