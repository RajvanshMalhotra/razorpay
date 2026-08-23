"""Run two brokers against a real LLM provider and print what happened.

  LLM_PROVIDER=ollama  .venv/bin/python scripts/two_brokers.py
  LLM_PROVIDER=deepseek .venv/bin/python scripts/two_brokers.py

Ollama must be running locally for the first; DEEPSEEK_API_KEY must be set
for the second. No Razorpay call is made — this exercises the agents, not
settlement.
"""
from __future__ import annotations

import sys

from dotenv import load_dotenv

from exchange.agents.broker import Broker
from exchange.agents.journal import AgentJournal
from exchange.agents.negotiation import negotiate
from exchange.eventlog import EventLog
from exchange.llm.openai_compat import provider_from_env
from exchange.models import (
    Actor, ActorKind, Asset, AssetKind, Currency, Order, Side,
)
from exchange.rails.credits import CreditRail
from exchange.rails.inr import RazorpayRail
from exchange.retrieval import HybridIndex, default_embedder
from exchange.service import Exchange
from dataclasses import replace

from exchange.ids import new_id


TRIAL_QTY = 200


def main() -> int:
    load_dotenv()
    provider = provider_from_env()
    correlation_id = new_id("corr")
    print(f"Correlation id: {correlation_id}\n")

    log = EventLog("runs/brokers.db")
    exchange = Exchange(log, HybridIndex(embed_fn=default_embedder()),
                        RazorpayRail(log, None), CreditRail(log))

    for actor_id in ("m_buyer", "m_seller"):
        exchange.register_actor(Actor(actor_id=actor_id, kind=ActorKind.MERCHANT))
    exchange.list_asset(Asset(
        asset_id="ast_mailers", kind=AssetKind.GOODS,
        title="biodegradable mailers compostable poly 10x13",
        spec={"material": "compostable poly"}, currency=Currency.INR,
        origin_actor_id="m_seller",
    ))
    exchange.post_order(Order(
        order_id="ord_ask", actor_id="m_seller", side=Side.ASK,
        asset_ref="ast_mailers", asset_query=None, qty=1000, limit_price=1940,
        currency=Currency.INR, expires_at="2026-12-31T00:00:00+00:00",
        policy_snapshot={},
    ), correlation_id=correlation_id)

    buyer = Broker("m_buyer", exchange, provider)

    print("=== FINDING SUPPLY ===")
    matches = buyer.find_supply(
        "eco friendly biodegradable mailers under 22 rupees a unit",
        500, 2200, correlation_id,
    )
    if not matches:
        print("No candidates found.")
        return 1
    print(f"{matches[0].rationale}\n")

    print("=== DIPLOMAT ===")
    print(buyer.assess("m_seller", correlation_id) + "\n")

    print("=== NEGOTIATION ===")
    journal = AgentJournal(log, "m_buyer", correlation_id)
    outcome = negotiate("m_buyer", "m_seller", provider, provider,
                        opening_price=1940, buyer_limit=2200, seller_floor=1800,
                        journal=journal)
    for offer in outcome.offers:
        print(f"  {offer.actor_id:>10}: {offer.price:>6}  {offer.message.strip()[:70]}")
    print(f"\n  outcome: {outcome.ended_reason}"
          + (f" at {outcome.final_price}" if outcome.agreed else "") + "\n")

    agreed_price = outcome.final_price if outcome.agreed else matches[0].clearing_price

    # The gate first, deliberately, on the full lot. m_seller is a stranger — no
    # deals, confidence 0.0 — so the trial-size bound should refuse this and say
    # so. That refusal is the point: reputation never excludes a counterparty, it
    # only caps what you risk on one you do not know yet.
    print("=== THE GATE, ON THE FULL LOT ===")
    decision, settlement = buyer.close(
        matches[0], "m_seller", correlation_id, agreed_price=agreed_price,
    )
    print(f"  {decision.verdict}: {decision.reason}")
    print(f"  exposure evaluated: {decision.limits_evaluated['amount']}"
          f"  against trial cap {decision.limits_evaluated['unknown_counterparty_cap']}\n")

    # Now the same trade at a size a first dealing can carry.
    if settlement is None:
        small = replace(matches[0], qty=TRIAL_QTY)
        print(f"=== RETRY AT {TRIAL_QTY} UNITS ===")
        decision, settlement = buyer.close(
            small, "m_seller", correlation_id, agreed_price=agreed_price,
        )
        print(f"  {decision.verdict}: {decision.reason}")

    if settlement is not None:
        print(f"  settlement: {settlement.status}"
              f"  razorpay_order_id={settlement.razorpay_order_id}")
        print(f"  relationship: confidence now "
              f"{buyer.graph.confidence('m_seller'):.2f}")
        lessons = buyer.subconscious.recall("m_seller")
        if lessons:
            print(f"  lesson filed: {lessons[-1]}")
    print()

    print("=== AUDIT TRAIL ===")
    for event in log.read_by_correlation(correlation_id):
        print(f"  [{event.seq:>3}] {event.actor_id:<10} {event.type}")

    log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
