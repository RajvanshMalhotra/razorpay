"""Hybrid retrieval: BM25 for exact terms, embeddings for intent, fused by RRF.

Sparse retrieval catches SKUs, materials, and brand names. Dense retrieval
catches paraphrase — 'eco packaging' finding 'biodegradable mailers'. Neither
alone is sufficient for a descriptive bid, so both run and their rankings are
combined by reciprocal rank fusion.
"""
from __future__ import annotations

import threading

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
    import os

    import numpy as np
    from model2vec import StaticModel

    name = "minishlab/potion-base-8M"

    # THE CACHE FIRST, THE NETWORK ONLY IF IT HAS TO. The weights are a few
    # megabytes and they are on the machine after the first run, but
    # `from_pretrained` still asks the hub whether they are current — and when
    # that call hangs, this returns in four HUNDRED seconds instead of one.
    # Measured: 0.8s offline against 400s+ waiting on the hub, which on a demo
    # machine is the difference between a server and a dead terminal.
    if os.environ.get("HF_HUB_OFFLINE") is None:
        os.environ["HF_HUB_OFFLINE"] = "1"
        try:
            model = StaticModel.from_pretrained(name)
        except Exception:                              # noqa: BLE001
            # Nothing cached yet — a first run on a new machine. Let it fetch.
            del os.environ["HF_HUB_OFFLINE"]
            model = StaticModel.from_pretrained(name)
    else:
        model = StaticModel.from_pretrained(name)

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
        # Every broker shares one index and brokers run concurrently, so the
        # four fields above are written as a group and must be read as one.
        self._lock = threading.Lock()

    def index(self, docs: list[tuple[str, str]]) -> None:
        """REPLACE the corpus. Every text is re-embedded."""
        with self._lock:
            self._index_locked(docs)

    def _index_locked(self, docs: list[tuple[str, str]]) -> None:
        self._doc_ids = [doc_id for doc_id, _ in docs]
        self._texts = [text for _, text in docs]
        if not docs:
            self._bm25 = None
            self._vectors = []
            return
        self._bm25 = BM25Okapi([t.lower().split() for t in self._texts])
        self._vectors = self._embed(self._texts)

    def add(self, docs: list[tuple[str, str]]) -> None:
        """APPEND to the corpus. Only the new texts are embedded.

        `index()` replaces everything, so a caller that grew a list and handed
        the whole of it over on each listing paid n(n+1)/2 embeddings: 90
        listings produced 4,095 embedded texts where linear is 90. With
        `default_embedder()` (model2vec, real weights) rather than a stub, that
        is what 30 merchants seeding their inventories costs at startup, and it
        is genuinely quadratic if the runner lists during rounds.

        BM25 is still re-fit over the whole corpus, and has to be: its idf is a
        property of the corpus, so an appended document changes the scores of
        the ones already in it. Re-fitting is tokenisation and counting, which
        is not the expensive half — embedding is, and embedding is now paid
        once per document for the life of the index.

        Ranking is unaffected: `_rank` breaks ties on document id rather than
        on position, so a corpus built by appending ranks identically to the
        same corpus built in one call.
        """
        if not docs:
            return
        # Embed OUTSIDE the lock — it is the slow half, and holding the lock
        # through it would stall every concurrent search behind one listing.
        new_texts = [text for _, text in docs]
        new_vectors = self._embed(new_texts)
        new_ids = [doc_id for doc_id, _ in docs]

        # Then swap the four fields together, so no search can observe ids
        # that its scores do not cover.
        with self._lock:
            self._doc_ids.extend(new_ids)
            self._texts.extend(new_texts)
            self._vectors.extend(new_vectors)
            self._bm25 = BM25Okapi([t.lower().split() for t in self._texts])

    @property
    def size(self) -> int:
        """How many documents the index holds."""
        return len(self._doc_ids)

    def search(self, query: str, top_k: int = 5) -> list[tuple[str, float]]:
        """Search a CONSISTENT SNAPSHOT of the corpus.

        The index is shared by every broker, and brokers run concurrently. A
        search reads `_doc_ids`, `_bm25` and `_vectors` and assumes all three
        describe the same corpus — but another thread listing an asset calls
        `index()`, which REPLACES all three. A search that read the doc ids
        before the swap and the scores after it indexes a long list with a
        short one:

            self._doc_ids[i], scores[i]
            IndexError: list index out of range

        which is exactly what a live round produced. Taking the three under
        one lock and ranking against the snapshot means a search sees the
        corpus as it was at one instant. An asset listed a millisecond later
        is simply found by the next search, which is what "eventually" means
        in a market that is still being written to.
        """
        with self._lock:
            doc_ids = list(self._doc_ids)
            bm25 = self._bm25
            vectors = list(self._vectors)

        if not doc_ids:
            return []

        sparse_ranking = self._rank(doc_ids, bm25.get_scores(query.lower().split()),
                                    positive_only=True)

        query_vec = self._embed([query])[0]
        dense_scores = [_cosine(query_vec, v) for v in vectors]
        dense_ranking = self._rank(doc_ids, dense_scores)

        return rrf_fuse([sparse_ranking, dense_ranking])[:top_k]

    def _rank(self, doc_ids, scores, positive_only: bool = False) -> list[str]:
        """Order documents by score, breaking ties on document id.

        The tie-break is the point. A plain `sorted` is stable, so equal scores
        keep insertion order — and then the sequence assets happened to be
        listed in silently decides the ranking, which is not relevance. Ties
        must resolve on something intrinsic to the document instead.
        """
        pairs = [
            (doc_ids[i], scores[i])
            for i in range(len(doc_ids))
            if not positive_only or scores[i] > 0
        ]
        pairs.sort(key=lambda pair: (-pair[1], pair[0]))
        return [doc_id for doc_id, _ in pairs]
