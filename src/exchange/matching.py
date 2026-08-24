"""Match a bid against the open asks.

Feasibility is a hard filter. Retrieval decides relevance. Counterparty
standing plays no part here — it never excludes an ask, so giving it a say in
ranking would only risk what exclusion does: ossifying a market into
cliques. Standing instead reaches a choosing agent as a fact, and risk on
unfamiliar counterparties is bounded by the policy gate.
"""
from __future__ import annotations

from dataclasses import replace

from exchange.ids import new_id
from exchange.models import Asset, Match, Order, Side
from exchange.retrieval import HybridIndex


def resize(match: Match, qty: int) -> Match:
    """The same counterparty at a different size — as a NEW match.

    A trade the gate refused and the merchant retries smaller is not the same
    action asked twice; it is a second action on different terms, and it must
    reach the gate with its own id. `dataclasses.replace` alone preserves
    `match_id`, which would file a DENY and a later ALLOW under one
    `action_ref` — and the accountant joins settlements to decisions on exactly
    that id, precisely so a denied match and an allowed one on the same
    correlation cannot be mistaken for each other. Reusing the id reopens that
    hole from the inside: a settlement of the refused exposure would find an
    ALLOW under its own id and pass. `fold` overwrites on match_id too, so the
    refused proposal would vanish from the projection as well.

    `Exchange.execute_match` now refuses a match_id that has already been
    decided, so this is the supported way to retry.
    """
    return replace(match, match_id=new_id("mch"), qty=qty)


def find_candidates(
    bid: Order,
    asks: list[Order],
    assets: dict[str, Asset],
    index: HybridIndex,
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
            if not retrieval_score > min_score:
                continue

            scored.append((
                retrieval_score,
                Match(
                    match_id=new_id("mch"),
                    bid_order_id=bid.order_id,
                    ask_order_id=ask.order_id,
                    clearing_price=ask.limit_price,
                    qty=bid.qty,
                    score=retrieval_score,
                    # Reports the score rather than asserting a match: retrieval
                    # ranks, it does not certify relevance, and this string is
                    # what the audit trail prints.
                    rationale=(
                        f"{asset_id} ranked {retrieval_score:.4f} for '{query}'; "
                        f"priced {ask.limit_price} (<= bid limit {bid.limit_price}), "
                        f"qty {ask.qty} >= {bid.qty}"
                    ),
                ),
            ))

    scored.sort(key=lambda pair: (-pair[0], pair[1].clearing_price))
    return [match for _, match in scored[:top_k]]
