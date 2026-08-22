from exchange.matching import find_candidates
from exchange.models import Asset, AssetKind, Currency, Order, Side
from exchange.retrieval import HybridIndex
from tests.test_retrieval import fake_embedder


def _ask(order_id, actor_id, asset_ref, unit_price, qty=1000):
    return Order(
        order_id=order_id,
        actor_id=actor_id,
        side=Side.ASK,
        asset_ref=asset_ref,
        asset_query=None,
        qty=qty,
        limit_price=unit_price,
        currency=Currency.INR,
        expires_at="2026-09-30T00:00:00+00:00",
        policy_snapshot={},
    )


def _asset(asset_id, title, actor_id):
    return Asset(
        asset_id=asset_id,
        kind=AssetKind.GOODS,
        title=title,
        spec={},
        currency=Currency.INR,
        origin_actor_id=actor_id,
    )


BID = Order(
    order_id="ord_bid",
    actor_id="m_buyer",
    side=Side.BID,
    asset_ref=None,
    asset_query={"text": "biodegradable mailers compostable"},
    qty=500,
    limit_price=2200,
    currency=Currency.INR,
    expires_at="2026-09-30T00:00:00+00:00",
    policy_snapshot={},
)

ASSETS = {
    "ast_1": _asset("ast_1", "corrugated kraft boxes recyclable", "m_a"),
    "ast_2": _asset("ast_2", "biodegradable mailers compostable poly", "m_b"),
    "ast_3": _asset("ast_3", "bubble wrap plastic protective", "m_c"),
}


def _index():
    index = HybridIndex(embed_fn=fake_embedder)
    index.index([(a.asset_id, a.title) for a in ASSETS.values()])
    return index


def test_returns_the_semantically_matching_ask_first():
    asks = [
        _ask("ord_1", "m_a", "ast_1", 1800),
        _ask("ord_2", "m_b", "ast_2", 1940),
        _ask("ord_3", "m_c", "ast_3", 1500),
    ]

    matches = find_candidates(BID, asks, ASSETS, _index())

    assert matches[0].ask_order_id == "ord_2"


def test_asks_above_the_bid_limit_are_excluded():
    asks = [_ask("ord_2", "m_b", "ast_2", 2500)]

    assert find_candidates(BID, asks, ASSETS, _index()) == []


def test_asks_with_insufficient_quantity_are_excluded():
    asks = [_ask("ord_2", "m_b", "ast_2", 1940, qty=100)]

    assert find_candidates(BID, asks, ASSETS, _index()) == []


def test_the_bidders_own_ask_is_excluded():
    asks = [_ask("ord_2", "m_buyer", "ast_2", 1940)]

    assert find_candidates(BID, asks, ASSETS, _index()) == []


def test_clearing_price_is_the_ask_price():
    asks = [_ask("ord_2", "m_b", "ast_2", 1940)]

    assert find_candidates(BID, asks, ASSETS, _index())[0].clearing_price == 1940


def test_match_carries_a_human_readable_rationale():
    asks = [_ask("ord_2", "m_b", "ast_2", 1940)]

    rationale = find_candidates(BID, asks, ASSETS, _index())[0].rationale

    assert "ast_2" in rationale
    assert "1940" in rationale


def test_the_rationale_reports_a_score_rather_than_asserting_a_match():
    """Retrieval ranks; it does not certify relevance. The wording must not lie."""
    asks = [_ask("ord_2", "m_b", "ast_2", 1940)]

    rationale = find_candidates(BID, asks, ASSETS, _index())[0].rationale

    assert "ranked" in rationale
    assert "matched" not in rationale


def test_a_relevance_floor_excludes_an_otherwise_feasible_ask():
    """Feasibility is not relevance: a high floor drops a priceable but weak ask."""
    asks = [_ask("ord_2", "m_b", "ast_2", 1940)]

    admitted = find_candidates(BID, asks, ASSETS, _index())
    assert len(admitted) == 1, "the default floor of 0.0 must be a no-op"

    above_any_rrf_score = admitted[0].score + 1.0
    excluded = find_candidates(BID, asks, ASSETS, _index(), min_score=above_any_rrf_score)

    assert excluded == []


def test_counterparty_score_can_reorder_near_ties():
    """Two identical listings; the better-regarded seller comes first."""
    assets = {
        "ast_2": _asset("ast_2", "biodegradable mailers compostable poly", "m_b"),
        "ast_4": _asset("ast_4", "biodegradable mailers compostable poly", "m_d"),
    }
    index = HybridIndex(embed_fn=fake_embedder)
    index.index([(a.asset_id, a.title) for a in assets.values()])
    asks = [_ask("ord_2", "m_b", "ast_2", 1940), _ask("ord_4", "m_d", "ast_4", 1940)]

    matches = find_candidates(
        BID, asks, assets, index, counterparty_scores={"m_b": 0.1, "m_d": 0.95}
    )

    assert matches[0].ask_order_id == "ord_4"


def test_counterparty_score_never_excludes_an_ask():
    """A distrusted seller is still offered — the gate bounds them, not the match."""
    asks = [_ask("ord_2", "m_b", "ast_2", 1940)]

    matches = find_candidates(
        BID, asks, ASSETS, _index(), counterparty_scores={"m_b": 0.0}
    )

    assert len(matches) == 1


def test_top_k_bounds_the_candidate_count():
    asks = [
        _ask("ord_1", "m_a", "ast_1", 1800),
        _ask("ord_2", "m_b", "ast_2", 1940),
        _ask("ord_3", "m_c", "ast_3", 1500),
    ]

    assert len(find_candidates(BID, asks, ASSETS, _index(), top_k=2)) == 2


def test_no_asks_yields_no_matches():
    assert find_candidates(BID, [], ASSETS, _index()) == []


def test_competing_asks_on_the_same_asset_are_all_considered():
    """A merchant posting volume tiers on its own listing must not lose offers."""
    asks = [
        _ask("ord_cheap", "m_b", "ast_2", 1800),
        _ask("ord_dear", "m_b", "ast_2", 1940),
    ]

    matches = find_candidates(BID, asks, ASSETS, _index(), top_k=5)

    assert {m.ask_order_id for m in matches} == {"ord_cheap", "ord_dear"}


def test_cheaper_of_two_equally_ranked_asks_comes_first():
    """Deliberately lists the dearer ask first, so insertion order would fail this."""
    asks = [
        _ask("ord_dear", "m_b", "ast_2", 1940),
        _ask("ord_cheap", "m_b", "ast_2", 1800),
    ]

    matches = find_candidates(BID, asks, ASSETS, _index(), top_k=5)

    assert matches[0].ask_order_id == "ord_cheap"
    assert matches[0].clearing_price == 1800
