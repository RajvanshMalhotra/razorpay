"""Match a bid against the open asks.

Feasibility is a hard filter. Retrieval decides relevance. Counterparty
standing is a soft nudge that can reorder near-ties but can never exclude an
ask — exclusion by reputation is what ossifies a market into cliques. Risk on
unfamiliar counterparties is bounded by the policy gate instead.
"""
from __future__ import annotations

from exchange.ids import new_id
from exchange.models import Asset, Match, Order, Side
from exchange.retrieval import HybridIndex

# The counterparty nudge is capped at this fraction of the retrieval score.
COUNTERPARTY_WEIGHT = 0.2


def find_candidates(
    bid: Order,
    asks: list[Order],
    assets: dict[str, Asset],
    index: HybridIndex,
    counterparty_scores: dict[str, float] | None = None,
    top_k: int = 3,
    min_score: float = 0.0,
) -> list[Match]:
    """Rank the feasible asks against a bid.

    `min_score` is a relevance floor: a candidate is dropped unless its final
    score is strictly greater than it. The default of `0.0` is a no-op — every
    feasible ask is offered, which is deliberate. RRF scores are rank-derived,
    so they are not comparable across corpora and no defensible non-zero value
    exists without real listing data behind it; a floor set too high silently
    rejects valid trades. The hook exists so a later plan can tune it against
    the real book, and the production value must be chosen that way.
    """
    counterparty_scores = counterparty_scores or {}

    feasible: dict[str, list[Order]] = {}
    for ask in asks:
        if (
            ask.side == Side.ASK
            and ask.actor_id != bid.actor_id
            and ask.asset_ref in assets
            and ask.limit_price <= bid.limit_price
            and ask.qty >= bid.qty
        ):
            feasible.setdefault(ask.asset_ref, []).append(ask)
    if not feasible:
        return []

    query = bid.asset_query.get("text", "") if bid.asset_query else ""
    if not query and bid.asset_ref:
        query = assets[bid.asset_ref].title

    # Ask the index how much it holds. Sizing this by len(assets) truncates the
    # ranking whenever a caller passes a filtered subset while the index still
    # holds every listing — silently dropping feasible asks off the bottom.
    ranked = index.search(query, top_k=index.size)

    scored: list[tuple[float, Match]] = []
    for asset_id, retrieval_score in ranked:
        for ask in feasible.get(asset_id, []):
            standing = counterparty_scores.get(ask.actor_id, 0.5)
            final_score = retrieval_score * (1.0 + COUNTERPARTY_WEIGHT * (standing - 0.5) * 2)

            if not final_score > min_score:
                continue

            scored.append((
                final_score,
                Match(
                    match_id=new_id("mch"),
                    bid_order_id=bid.order_id,
                    ask_order_id=ask.order_id,
                    clearing_price=ask.limit_price,
                    score=final_score,
                    # Reports the score rather than asserting a match: retrieval
                    # ranks, it does not certify relevance, and this string is
                    # what the audit trail prints.
                    rationale=(
                        f"{asset_id} ranked {final_score:.4f} for '{query}'; "
                        f"priced {ask.limit_price} (<= bid limit {bid.limit_price}), "
                        f"qty {ask.qty} >= {bid.qty}; "
                        f"counterparty {ask.actor_id} standing {standing:.2f}"
                    ),
                ),
            ))

    scored.sort(key=lambda pair: (-pair[0], pair[1].clearing_price))
    return [match for _, match in scored[:top_k]]
