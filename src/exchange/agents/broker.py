"""One merchant's representative: an orchestrator over three isolated roles.

The broker never touches a settlement rail. Every trade goes through
`Exchange.execute_match`, which records its policy decision before any money
moves — that ordering is the audit trail's guarantee, and routing around it
would silently break the thing this project is judged on.
"""
from __future__ import annotations

from exchange.agents.context import ContextDelta
from exchange.agents.relationships import RelationshipGraph
from exchange.agents.subagents import make_diplomat, make_scout, make_trader
from exchange.agents.subconscious import Subconscious
from exchange.agents.tree import ContextTree
from exchange.ids import new_id
from exchange.matching import find_candidates
from exchange.models import ActorStatus, Currency, Match, Order, Side
from exchange.policy import PolicyContext


class Broker:
    def __init__(
        self,
        actor_id: str,
        exchange,
        provider,
        subconscious: Subconscious | None = None,
        graph: RelationshipGraph | None = None,
    ) -> None:
        self.actor_id = actor_id
        self._exchange = exchange
        self._provider = provider
        self.subconscious = subconscious or Subconscious(provider)
        self.graph = graph or RelationshipGraph()

        self.tree = ContextTree()
        self.root_id = self.tree.add(
            None,
            ContextDelta(objective=f"trade profitably on behalf of {actor_id}"),
            state_version=0,
        )

        version = len(self._exchange.log.read_all())
        self._trader = make_trader(provider, self.tree, self.root_id, version)
        self._scout = make_scout(provider, self.tree, self.root_id, version)
        self._diplomat = make_diplomat(provider, self.tree, self.root_id, version)

    def find_supply(
        self,
        need_text: str,
        qty: int,
        limit_price: int,
        correlation_id: str,
    ) -> list[Match]:
        """Post a descriptive bid and return the candidates worth pursuing."""
        bid = Order(
            order_id=new_id("ord"),
            actor_id=self.actor_id,
            side=Side.BID,
            asset_ref=None,
            asset_query={"text": need_text},
            qty=qty,
            limit_price=limit_price,
            currency=Currency.INR,
            expires_at="2026-12-31T00:00:00+00:00",
            policy_snapshot={},
        )
        self._exchange.post_order(bid, correlation_id=correlation_id)

        state = self._exchange.state()
        asks = [o for o in state.open_orders.values() if o.side == Side.ASK]
        matches = find_candidates(
            bid, asks, state.assets, self._exchange.index,
            counterparty_scores=self.graph.scores(),
        )
        self._trader.act(
            f"We need {qty} of: {need_text}, at no more than {limit_price} each. "
            f"{len(matches)} candidate(s) found."
        )
        return matches

    def assess(self, counterparty_id: str) -> str:
        """Ask the Diplomat about a counterparty, with recall injected first."""
        recalled = self.subconscious.recall(counterparty_id)
        return self._diplomat.act(
            f"What should we know about {counterparty_id} before dealing with them?",
            facts=recalled,
        )

    def close(self, match: Match, seller_id: str, correlation_id: str):
        """Settle through the exchange's gate, then record the relationship."""
        ctx = PolicyContext(
            actor_status=ActorStatus.ACTIVE,
            rolling_spend=0,  # derived from the log inside execute_match
            counterparty_confidence=self.graph.confidence(seller_id),
        )
        decision, settlement = self._exchange.execute_match(
            match, self.actor_id, seller_id, ctx, correlation_id=correlation_id,
        )
        if settlement is not None:
            self.graph.record_deal(
                seller_id,
                value=match.clearing_price * match.qty,
                delivered=True,
            )
            for agent in (self._trader, self._scout, self._diplomat):
                self.tree.checkpoint(agent.node_id)
        return decision, settlement
