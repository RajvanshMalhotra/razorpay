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
) -> list[Match]:
    counterparty_scores = counterparty_scores or {}

    feasible = {
        ask.asset_ref: ask
        for ask in asks
        if ask.side == Side.ASK
        and ask.actor_id != bid.actor_id
        and ask.asset_ref in assets
        and ask.limit_price <= bid.limit_price
        and ask.qty >= bid.qty
    }
    if not feasible:
        return []

    query = bid.asset_query.get("text", "") if bid.asset_query else ""
    if not query and bid.asset_ref:
        query = assets[bid.asset_ref].title

    ranked = index.search(query, top_k=len(assets))

    scored: list[tuple[float, Match]] = []
    for asset_id, retrieval_score in ranked:
        ask = feasible.get(asset_id)
        if ask is None:
            continue

        standing = counterparty_scores.get(ask.actor_id, 0.5)
        final_score = retrieval_score * (1.0 + COUNTERPARTY_WEIGHT * (standing - 0.5) * 2)

        scored.append((
            final_score,
            Match(
                match_id=new_id("mch"),
                bid_order_id=bid.order_id,
                ask_order_id=ask.order_id,
                clearing_price=ask.limit_price,
                score=final_score,
                rationale=(
                    f"{asset_id} matched '{query}' at {ask.limit_price} "
                    f"(<= bid limit {bid.limit_price}), qty {ask.qty} >= {bid.qty}, "
                    f"counterparty {ask.actor_id} standing {standing:.2f}"
                ),
            ),
        ))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [match for _, match in scored[:top_k]]
