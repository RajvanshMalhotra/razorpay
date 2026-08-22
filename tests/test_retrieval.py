import math

from exchange.retrieval import HybridIndex, rrf_fuse

DOCS = [
    ("ast_1", "corrugated kraft boxes 12x8 recyclable"),
    ("ast_2", "biodegradable mailers compostable poly"),
    ("ast_3", "bubble wrap rolls plastic protective"),
    ("ast_4", "vitamin c serum 20% skincare"),
]


def fake_embedder(texts):
    """Deterministic bag-of-words vectors over a fixed vocabulary.

    Keeps tests fast and offline while still exercising the dense path.
    """
    vocab = [
        "corrugated", "kraft", "boxes", "recyclable",
        "biodegradable", "mailers", "compostable", "poly",
        "bubble", "wrap", "plastic", "protective",
        "vitamin", "serum", "skincare", "eco",
    ]
    vectors = []
    for text in texts:
        tokens = set(text.lower().split())
        vec = [1.0 if word in tokens else 0.0 for word in vocab]
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        vectors.append([v / norm for v in vec])
    return vectors


def test_rrf_fuse_ranks_a_doc_appearing_high_in_both_lists_first():
    fused = rrf_fuse([["a", "b", "c"], ["a", "c", "b"]])

    assert fused[0][0] == "a"


def test_rrf_fuse_includes_docs_present_in_only_one_ranking():
    fused = rrf_fuse([["a", "b"], ["c"]])
    ids = [doc_id for doc_id, _ in fused]

    assert set(ids) == {"a", "b", "c"}


def test_rrf_fuse_scores_descend():
    fused = rrf_fuse([["a", "b", "c"], ["a", "b", "c"]])
    scores = [score for _, score in fused]

    assert scores == sorted(scores, reverse=True)


def test_rrf_fuse_of_nothing_is_empty():
    assert rrf_fuse([]) == []


def test_exact_term_match_is_retrieved():
    index = HybridIndex(embed_fn=fake_embedder)
    index.index(DOCS)

    results = index.search("corrugated boxes", top_k=2)

    assert results[0][0] == "ast_1"


def test_paraphrase_is_retrieved_via_the_dense_path():
    """'eco ... packaging' contributes no BM25 signal; 'biodegradable' carries it.

    Both retrievers should agree on ast_2 here — the point is that the fused
    ranking surfaces it, not that either path finds it alone.
    """
    index = HybridIndex(embed_fn=fake_embedder)
    index.index(DOCS)

    results = index.search("eco biodegradable packaging", top_k=2)
    ids = [doc_id for doc_id, _ in results]

    assert "ast_2" in ids


def test_unrelated_document_ranks_below_relevant_ones():
    index = HybridIndex(embed_fn=fake_embedder)
    index.index(DOCS)

    results = index.search("corrugated kraft boxes", top_k=4)
    ids = [doc_id for doc_id, _ in results]

    assert ids.index("ast_1") < ids.index("ast_4")


def test_top_k_bounds_the_result_count():
    index = HybridIndex(embed_fn=fake_embedder)
    index.index(DOCS)

    assert len(index.search("packaging", top_k=2)) == 2


def test_searching_an_empty_index_returns_nothing():
    index = HybridIndex(embed_fn=fake_embedder)
    index.index([])

    assert index.search("anything") == []


def test_search_is_invariant_to_document_insertion_order():
    """Ranking must depend on relevance, not on the order documents were added."""

    def flat_embedder(texts):
        return [[1.0, 0.0] for _ in texts]

    forward = HybridIndex(embed_fn=flat_embedder)
    forward.index(DOCS)

    backward = HybridIndex(embed_fn=flat_embedder)
    backward.index(list(reversed(DOCS)))

    assert [d for d, _ in forward.search("kraft", top_k=4)] == [
        d for d, _ in backward.search("kraft", top_k=4)
    ]


def test_zero_bm25_query_does_not_let_index_order_pollute_the_ranking():
    """No document shares a term with 'skin', so every BM25 score is 0 and its
    ranking collapses to insertion order. That must not outvote the dense path."""

    def skin_embedder(texts):
        vectors = []
        for text in texts:
            lowered = text.lower()
            is_skin = any(w in lowered for w in ("skin", "serum", "skincare"))
            vectors.append([1.0, 0.0] if is_skin else [0.0, 1.0])
        return vectors

    index = HybridIndex(embed_fn=skin_embedder)
    index.index(DOCS)

    results = index.search("skin", top_k=1)

    assert results[0][0] == "ast_4"
