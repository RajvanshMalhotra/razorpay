"""One input box. A person types what they need; the exchange does the rest.

    .venv/bin/python -m scripts.market.storefront \\
        "biodegradable mailers under 22 rupees a unit" --qty 400

NOT A SECOND PRODUCT. This writes a descriptive bid to the same order book
the brokers use, runs the same hybrid retrieval, and settles through the same
gate. Nothing here knows it is being driven by a human — which is the point,
and the reason it is fifty lines rather than a subsystem.

WHY IT EARNS ITS PLACE IN THE DEMO. Everything else on screen is agents
trading with agents, and the fair question is whether any of it is reachable
by a person. This answers it in one line of typing: the same discovery, the
same negotiation, the same policy decision recorded before any money moves.

THE HUMAN APPROVES, AND THE GATE STILL DECIDES. Those are different things
and both have to happen. A person confirming a purchase is consent; it is not
permission, and it does not raise a cap or unfreeze an actor. If the gate
refuses what the human asked for, the refusal stands and is recorded — the
same DENY, in the same words, on the same thread. A storefront that could
talk the gate round would be the tenth instance of this project's oldest bug:
a value the checker must be authoritative about, supplied by the party it
constrains.
"""
from __future__ import annotations

import argparse
import sys

from exchange.agents.negotiation import negotiate
from exchange.ids import new_id
from exchange.matching import resize


def shop(exchange, broker, need_text: str, qty: int, limit_price: int,
         confirm, correlation_id: str | None = None):
    """Search, choose, negotiate, ask the human, then settle through the gate."""
    correlation_id = correlation_id or new_id("shop")

    matches = broker.find_supply(need_text=need_text, qty=qty,
                                 limit_price=limit_price,
                                 correlation_id=correlation_id)
    if not matches:
        return correlation_id, "nothing on the book matches that", None

    match = broker.choose(matches, correlation_id=correlation_id)
    posted = exchange.state().posted_orders.get(match.ask_order_id)
    seller_id = posted.actor_id if posted else "unknown"

    # A PERSON DOES NOT HAGGLE WITH A SHOP. If the asking price is inside
    # what they said they would pay, that is the price — the same way any
    # storefront works. Sending a human's request through an agent-to-agent
    # negotiation produced exactly what you would expect: two walk-aways and
    # a stall, because the buyer's side was arguing on their behalf against a
    # seller with a floor.
    #
    # Haggling is what BROKERS do for merchants who trade at volume. The
    # storefront's job is to find the thing and show the price.
    if match.clearing_price <= limit_price:
        agreed_price = match.clearing_price
    else:
        outcome = negotiate(
            buyer_id=broker.actor_id, seller_id=seller_id,
            buyer_provider=broker.fast_tier, seller_provider=broker.fast_tier,
            opening_price=match.clearing_price, buyer_limit=limit_price,
            seller_floor=int(match.clearing_price * 0.88),
        )
        if not outcome.agreed or outcome.final_price is None:
            return correlation_id, f"no deal: {outcome.ended_reason}", None
        agreed_price = outcome.final_price

    # THE HUMAN DECIDES WHETHER TO PROCEED. Asked before anything is
    # committed, and a refusal ends it here — nothing is written, because
    # nothing happened.
    if not confirm(seller_id, agreed_price, qty):
        return correlation_id, "you declined", None

    decision, settlement = broker.close(
        match=match, seller_id=seller_id,
        correlation_id=correlation_id, agreed_price=agreed_price,
    )
    # The same trial-size retry a broker gets. A person buying from a stranger
    # is as unproven as an agent buying from one.
    if settlement is None and "cap" in (decision.reason or "").lower():
        affordable = _affordable(decision, agreed_price)
        if affordable and affordable < match.qty:
            decision, settlement = broker.close(
                match=resize(match, affordable), seller_id=seller_id,
                correlation_id=correlation_id, agreed_price=agreed_price,
            )
    if settlement is None:
        return correlation_id, f"the gate refused: {decision.reason}", None
    return correlation_id, "settled", settlement


def _affordable(decision, unit_price: int) -> int | None:
    limits = getattr(decision, "limits_evaluated", None) or {}
    caps = [int(v) for k, v in limits.items()
            if isinstance(v, (int, float)) and v > 0 and "cap" in k]
    return min(caps) // unit_price if caps and unit_price > 0 else None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Buy something, in words.")
    parser.add_argument("need", help="what you want, in plain language")
    parser.add_argument("--qty", type=int, default=400)
    parser.add_argument("--limit", type=int, default=2200,
                        help="most you will pay per unit, in paise")
    parser.add_argument("--db", default="runs/market.db")
    parser.add_argument("--as-merchant", default="m_bl_thirdwave")
    parser.add_argument("--yes", action="store_true",
                        help="approve without prompting")
    args = parser.parse_args(argv)

    import razorpay
    from dotenv import load_dotenv

    from exchange.agents.broker import Broker
    from exchange.config import Config
    from exchange.eventlog import EventLog
    from exchange.llm.openai_compat import providers_from_env
    from exchange.rails.credits import CreditRail
    from exchange.rails.inr import RazorpayRail
    from exchange.retrieval import HybridIndex, default_embedder
    from exchange.service import Exchange

    load_dotenv()
    cfg = Config.from_env()
    client = razorpay.Client(auth=(cfg.razorpay_key_id, cfg.razorpay_key_secret))
    strong, fast = providers_from_env()

    log = EventLog(args.db)
    try:
        exchange = Exchange(log, HybridIndex(embed_fn=default_embedder()),
                            RazorpayRail(log, client), CreditRail(log))
        broker = Broker(args.as_merchant, exchange, strong, fast_provider=fast)

        def confirm(seller_id, price, qty):
            print(f"\n  {seller_id} will sell {qty} at {price} per unit "
                  f"({price * qty / 100:,.2f} rupees).")
            if args.yes:
                print("  approved.")
                return True
            return input("  buy it? [y/N] ").strip().lower().startswith("y")

        print(f'\n  you asked for: "{args.need}"')
        correlation_id, result, settlement = shop(
            exchange, broker, args.need, args.qty, args.limit, confirm,
        )
        print(f"\n  {result}")
        if settlement is not None:
            print(f"  {settlement.amount / 100:,.2f} rupees, "
                  f"order {settlement.razorpay_order_id}")
        print(f"\n  the whole story, on one id ({correlation_id}):")
        for event in log.read_by_correlation(correlation_id):
            print(f"    {event.actor_id:<18} {event.type}")
        return 0
    finally:
        log.close()


if __name__ == "__main__":
    sys.exit(main())
