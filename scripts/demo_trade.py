"""Run one real trade against Razorpay test mode and print the audit trail.

This is the Plan 1 acceptance check. It produces real order and payment ids.
"""
from __future__ import annotations

import sys

import razorpay
from dotenv import load_dotenv

from exchange.config import Config
from exchange.eventlog import EventLog
from exchange.events import SETTLEMENT_INITIATED
from exchange.ids import new_id
from exchange.matching import find_candidates
from exchange.models import (
    Actor, ActorKind, ActorStatus, Asset, AssetKind, Currency, Order, Side,
)
from exchange.policy import PolicyContext
from exchange.rails.credits import CreditRail
from exchange.rails.inr import RazorpayRail
from exchange.retrieval import HybridIndex, default_embedder
from exchange.service import Exchange


def main() -> int:
    load_dotenv()
    correlation_id = new_id("corr")
    print(f"Correlation id for this run: {correlation_id}\n")
    cfg = Config.from_env()
    client = razorpay.Client(auth=(cfg.razorpay_key_id, cfg.razorpay_key_secret))

    log = EventLog(cfg.db_path)
    exchange = Exchange(
        log,
        HybridIndex(embed_fn=default_embedder()),
        RazorpayRail(log, client, poll_attempts=3, poll_interval=2.0),
        CreditRail(log),
    )

    for actor_id in ("m_buyer", "m_seller"):
        exchange.register_actor(Actor(actor_id=actor_id, kind=ActorKind.MERCHANT))

    exchange.list_asset(Asset(
        asset_id="ast_mailers", kind=AssetKind.GOODS,
        title="biodegradable mailers compostable poly 10x13",
        spec={"material": "compostable poly", "size": "10x13"},
        currency=Currency.INR, origin_actor_id="m_seller",
    ))

    ask = Order(
        order_id="ord_ask", actor_id="m_seller", side=Side.ASK,
        asset_ref="ast_mailers", asset_query=None, qty=1000, limit_price=1940,
        currency=Currency.INR, expires_at="2026-09-30T00:00:00+00:00",
        policy_snapshot={},
    )
    exchange.post_order(ask, correlation_id=correlation_id)

    bid = Order(
        order_id="ord_bid", actor_id="m_buyer", side=Side.BID, asset_ref=None,
        asset_query={"text": "eco friendly biodegradable mailers under 22 a unit"},
        qty=500, limit_price=2200, currency=Currency.INR,
        expires_at="2026-08-29T00:00:00+00:00", policy_snapshot={},
    )
    exchange.post_order(bid, correlation_id=correlation_id)

    matches = find_candidates(bid, [ask], exchange.state().assets, exchange.index)
    if not matches:
        print("No match found.")
        return 1
    print(f"Matched: {matches[0].rationale}\n")

    decision, settlement = exchange.execute_match(
        matches[0], "m_buyer", "m_seller",
        PolicyContext(ActorStatus.ACTIVE, rolling_spend=0, counterparty_confidence=0.9),
        correlation_id=correlation_id,
    )
    print(f"Policy: {decision.verdict} — {decision.reason}\n")

    if settlement:
        print(f"Settlement: {settlement.status}")
        print(f"  razorpay_order_id:   {settlement.razorpay_order_id}")
        print(f"  razorpay_payment_id: {settlement.razorpay_payment_id}\n")

    initiated = [
        e for e in log.read_by_correlation(correlation_id)
        if e.type == SETTLEMENT_INITIATED
    ]
    if initiated:
        link = initiated[0].payload.get("payment_link_url")
        error = initiated[0].payload.get("payment_link_error")
        if link:
            print("To complete this payment:")
            print(f"  {link}")
            print("  Test card 4111 1111 1111 1111, any future expiry, any CVV.\n")
        else:
            print(f"No payment link was created: {error}\n")

    print("=== AUDIT TRAIL ===")
    for event in log.read_by_correlation(correlation_id):
        print(f"  [{event.seq:>3}] {event.ts}  {event.actor_id:<10} {event.type}")

    log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
