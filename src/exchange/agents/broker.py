"""One merchant's representative: an orchestrator over three isolated roles.

The broker never touches a settlement rail. Every trade goes through
`Exchange.execute_match`, which records its policy decision before any money
moves — that ordering is the audit trail's guarantee, and routing around it
would silently break the thing this project is judged on.
"""
from __future__ import annotations

import logging
from dataclasses import replace

from exchange.agents.context import ContextDelta
from exchange.agents.journal import AgentJournal
from exchange.agents.relationships import RelationshipGraph
from exchange.agents.subagents import make_diplomat, make_scout, make_trader
from exchange.agents.subconscious import Subconscious
from exchange.agents.tree import ContextTree
from exchange.ids import new_id
from exchange.matching import find_candidates
from exchange.models import (
    ActorStatus,
    Currency,
    Match,
    Order,
    PolicyDecision,
    Settlement,
    SettlementStatus,
    Side,
)
from exchange.policy import PolicyContext

_log = logging.getLogger(__name__)


class Broker:
    def __init__(
        self,
        actor_id: str,
        exchange,
        provider,
        fast_provider=None,
        subconscious: Subconscious | None = None,
        graph: RelationshipGraph | None = None,
    ) -> None:
        self.actor_id = actor_id
        self._exchange = exchange
        self._provider = provider
        fast = fast_provider or provider
        self.subconscious = subconscious or Subconscious(provider)
        self.graph = graph or RelationshipGraph()

        self.tree = ContextTree()
        self.root_id = self.tree.add(
            None,
            ContextDelta(objective=f"trade profitably on behalf of {actor_id}"),
            state_version=0,
        )

        version = len(self._exchange.log.read_all())
        self._trader = make_trader(fast, self.tree, self.root_id, version)
        self._scout = make_scout(fast, self.tree, self.root_id, version)
        self._diplomat = make_diplomat(fast, self.tree, self.root_id, version)

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
        # standing() returns the optimistic UNKNOWN_STANDING for a counterparty we
        # have never dealt with; scores() would omit them entirely and let the
        # matcher fall back to its own neutral default, discarding the optimism.
        counterparty_scores = {
            ask.actor_id: self.graph.standing(ask.actor_id) for ask in asks
        }
        matches = find_candidates(
            bid, asks, state.assets, self._exchange.index,
            counterparty_scores=counterparty_scores,
        )
        reply = self._trader.act(
            f"We need {qty} of: {need_text}, at no more than {limit_price} each. "
            f"{len(matches)} candidate(s) found."
        )
        # Spec 4.2: a sub-agent's summary becomes a fact in the orchestrator's
        # delta. Discarding the reply left the root context permanently empty,
        # so the one-way narrowing the design rests on never actually happened.
        self._promote(reply)
        return matches

    def _promote(self, summary: str) -> None:
        """Narrow a sub-agent's reply into the broker's own context.

        Sub-agents branch from `root_id` and never merge; each returns a
        structured summary that becomes a fact here. Re-parenting the three
        agents onto the new node is what keeps the promoted facts visible to
        them — otherwise the orchestrator accumulates a chain its own workers
        cannot read.
        """
        self.root_id = self.tree.add(
            self.root_id,
            ContextDelta(facts_added=(summary,)),
            len(self._exchange.log.read_all()),
        )
        for agent in (self._trader, self._scout, self._diplomat):
            agent.reparent(self.root_id)

    def assess(self, counterparty_id: str, correlation_id: str) -> str:
        """Ask the Diplomat about a counterparty, with recall injected first.

        `correlation_id` is required, not optional. A recall that steered a
        decision has to leave a trace on the trade it steered — an opt-in
        audit trail is not one, and the caller who forgets is exactly the
        caller whose reasoning goes unrecorded.
        """
        recalled = self.subconscious.recall(counterparty_id)
        if recalled:
            AgentJournal(self._exchange.log, self.actor_id, correlation_id) \
                .recall_injected(counterparty_id, recalled)
        reply = self._diplomat.act(
            f"What should we know about {counterparty_id} before dealing with them?",
            facts=recalled,
        )
        self._promote(reply)
        return reply

    def close(
        self,
        match: Match,
        seller_id: str,
        correlation_id: str,
        agreed_price: int,
    ) -> tuple[PolicyDecision, Settlement | None]:
        """Settle through the exchange's gate, then record the relationship.

        `agreed_price` is what the negotiation actually landed on, per unit, and
        is required. The match's own `clearing_price` is the ask's asking price —
        settling at that after agreeing on something else would make the log
        tell two stories on one correlation_id, and an optional parameter meant
        a forgetful caller could do exactly that silently. The agreed figure
        always replaces it before anything downstream sees the match.
        """
        match = replace(match, clearing_price=agreed_price)

        ctx = PolicyContext(
            actor_status=ActorStatus.ACTIVE,
            rolling_spend=0,  # derived from the log inside execute_match
            counterparty_confidence=self.graph.confidence(seller_id),
        )
        decision, settlement = self._exchange.execute_match(
            match, self.actor_id, seller_id, ctx, correlation_id=correlation_id,
        )
        # COMPLETED, not merely non-None: the rails never return None. A capture
        # that never lands leaves the settlement PENDING and an SDK failure leaves
        # it FAILED, and recording either as a delivered deal would raise standing
        # and confidence — clearing a higher cap next time on the strength of a
        # payment that never happened.
        if settlement is not None and settlement.status == SettlementStatus.COMPLETED:
            self.graph.record_deal(
                seller_id,
                value=match.clearing_price * match.qty,
                delivered=True,
            )
            for agent in (self._trader, self._scout, self._diplomat):
                self.tree.checkpoint(agent.node_id)

            # Consolidation and checkpointing are the same moment: the checkpoint
            # is everything the broker knew when the deal closed, which is exactly
            # what there is to distil.
            #
            # What is knowable now is BEHAVIOURAL — how they negotiated, how fast
            # they moved, what they conceded. Whether they actually delivered is
            # not known at settlement, so reliability lessons wait for a delivery
            # signal that does not exist yet. apply_lesson ignores anything that
            # is not a reliability lesson, so feeding it every lesson is safe.
            #
            # This is a model round-trip, and it runs AFTER the money has moved. A
            # provider timeout must not propagate out of close(): the caller would
            # never learn the outcome of a trade that has already been paid for —
            # the same "settled but not recorded" shape the COMPLETED guard above
            # exists to prevent, arriving through a different door. The settlement
            # is already durable in the log; a lost lesson is recoverable, a lost
            # settlement result is not.
            try:
                episode = self.tree.materialise(self._trader.node_id)
                lesson = self.subconscious.consolidate(
                    episode, seller_id, category="trade"
                )
                AgentJournal(self._exchange.log, self.actor_id, correlation_id) \
                    .lesson_consolidated(lesson)
                self.graph.apply_lesson(lesson)
            except Exception:  # noqa: BLE001 - a lost lesson must not lose a paid trade
                _log.exception(
                    "consolidation failed after settlement with %s; the trade stands",
                    seller_id,
                )
        return decision, settlement
