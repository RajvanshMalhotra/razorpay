"""One merchant's representative: an orchestrator over three isolated roles.

The broker never touches a settlement rail. Every trade goes through
`Exchange.execute_match`, which records its policy decision before any money
moves — that ordering is the audit trail's guarantee, and routing around it
would silently break the thing this project is judged on.
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, replace
from datetime import datetime, timezone

from exchange import events as ev
from exchange.agents.context import ContextDelta
from exchange.agents.journal import AgentJournal
from exchange.agents.relationships import RelationshipGraph
from exchange.agents.subagents import make_diplomat, make_scout, make_trader
from exchange.agents.subconscious import Subconscious
from exchange.agents.tree import ContextTree
from exchange.house.auction import Bid
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
    Verdict,
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
        matches = find_candidates(bid, asks, state.assets, self._exchange.index)
        reply = self._trader.act(
            f"We need {qty} of: {need_text}, at no more than {limit_price} each. "
            f"{len(matches)} candidate(s) found."
        )
        # Spec 4.2: a sub-agent's summary becomes a fact in the orchestrator's
        # delta. Discarding the reply left the root context permanently empty,
        # so the one-way narrowing the design rests on never actually happened.
        self._promote(reply)
        return matches

    def choose(self, matches: list[Match], correlation_id: str) -> Match:
        """Pick a counterparty from the shortlist, and record why.

        The shortlist is ranked by relevance alone. Which of them to actually
        trade with is a judgment — history, reliability, whether a stranger is
        worth a first try — and it belongs to an agent, in prose, in the log.
        A weight here would decide it silently and need a number nobody could
        justify.
        """
        if len(matches) <= 1:
            return matches[0]

        lines = []
        for i, m in enumerate(matches, start=1):
            seller = self._exchange.state().open_orders[m.ask_order_id].actor_id
            recalled = self.subconscious.recall(seller)
            lines.append(
                f"{i}. seller {seller} at {m.clearing_price} per unit, "
                f"{m.qty} units. History: "
                + ("; ".join(recalled) if recalled else "never dealt with them.")
            )

        reply = self._diplomat.act(
            "Choose which of these to trade with. Answer with the number, then one "
            "sentence of reasoning.\n" + "\n".join(lines)
        )
        self._promote(reply)

        index = _first_index(reply, len(matches))
        chosen = matches[index]
        AgentJournal(self._exchange.log, self.actor_id, correlation_id)._append(
            ev.COUNTERPARTY_CHOSEN,
            {"ask_order_id": chosen.ask_order_id, "reason": reply,
             "shortlist": [m.ask_order_id for m in matches]},
        )
        return chosen

    def value_insight(self, headline: str, category: str, cap: int) -> Bid:
        """Ask the Scout what this lot is worth to us, and bound the answer.

        No formula prices an insight — what a headline is worth depends on
        this merchant's position, which is a judgment. Under second-price
        the honest number is also the optimal one, so the reasoning that
        lands in the log is about worth rather than about rivals.

        The cap is not a suggestion. An agent that wants to spend more than
        it may still bids, at the limit — judgment picks the number, the
        bound decides what is allowed.
        """
        recalled = self.subconscious.recall("house", category=category)
        reply = self._scout.act(
            f"A market intelligence lot is up for auction:\n\n  {headline}\n\n"
            f"Category: {category}. You may bid at most {cap} points.\n"
            "Answer with 'BID: <integer>' then one sentence on why it is worth "
            "that to us. Bid what it is actually worth — you pay the runner-up's "
            "price, not your own.",
            facts=recalled,
        )
        self._promote(reply)

        match = re.search(r"BID:\s*(\d+)", reply, re.IGNORECASE)
        if match is None:
            # Not a bid of zero — no bid. The reply is still returned and still
            # logged, because an agent that answered unusably is worth seeing
            # in the trail; but `parsed=False` keeps it out of the ranking,
            # where a fabricated zero could have set a clearing price nobody
            # named.
            return Bid(actor_id=self.actor_id, amount=0, reason=reply, parsed=False)
        return Bid(
            actor_id=self.actor_id,
            amount=min(int(match.group(1)), cap),
            reason=reply,
            parsed=True,
        )

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

    def _posted_bid_limit(self, bid_order_id: str) -> int | None:
        """The per-unit ceiling this merchant advertised, read from the log.

        Not from `state().open_orders`: a bid leaves the book once it is
        filled, and the limit it was posted under still binds the trade it was
        posted for. The log keeps it either way — and, as everywhere else here,
        the authoritative figure comes from the log rather than from whoever is
        asking to spend against it.
        """
        for event in self._exchange.log.read_all():
            if (
                event.type == ev.ORDER_POSTED
                and event.payload.get("order_id") == bid_order_id
            ):
                return event.payload["limit_price"]
        return None

    def _refuse_above_posted_limit(
        self, match: Match, agreed_price: int, correlation_id: str,
    ) -> PolicyDecision | None:
        """Refuse a negotiated price that breaches the bid's own ceiling.

        `negotiate()` puts the buyer's limit in front of the model as prompt
        text and nothing more, so a long haggle can end above the number this
        merchant published on the book. The matcher checked
        `ask.limit_price <= bid.limit_price` when it built the match; the
        agreed price then replaces that vetted figure with an unvetted one, and
        the policy caps only bound the absolute exposure — not this merchant's
        own stated maximum.

        REFUSED, NOT CLAMPED, deliberately. Clamping would settle at a price
        neither side agreed to and leave a SETTLEMENT_INITIATED whose amount
        matches neither the negotiation nor the ask, with no event explaining
        the difference — a silent correction is the same defect wearing a
        different hat. A refusal is a logged DENY that names the agreed price,
        the posted limit and the order they belong to, and moves no money. The
        deal is not lost: re-negotiating or re-sizing produces a new match
        (`matching.resize`), which the gate treats on its own terms.

        A bid with no ORDER_POSTED in the log is refused too. An unfindable
        ceiling is not an absent one, and defaulting to "unbounded" would make
        this check advisory for exactly the caller that skipped the book.
        """
        limit = self._posted_bid_limit(match.bid_order_id)
        if limit is None:
            return self._log_refusal(
                match, correlation_id,
                reason=(
                    f"Bid order {match.bid_order_id} is not in the log; there is "
                    f"no posted limit to check {agreed_price} against"
                ),
                evaluated={"agreed_price": agreed_price, "bid_limit_price": None},
            )
        if agreed_price > limit:
            return self._log_refusal(
                match, correlation_id,
                reason=(
                    f"Agreed price {agreed_price} exceeds the limit {limit} posted "
                    f"on bid {match.bid_order_id}"
                ),
                evaluated={"agreed_price": agreed_price, "bid_limit_price": limit},
            )
        return None

    def _log_refusal(
        self, match: Match, correlation_id: str, reason: str, evaluated: dict,
    ) -> PolicyDecision:
        """Record a DENY on the trade's own thread, in the gate's vocabulary.

        Written as POLICY_DECIDED so a replay of this correlation shows one
        kind of record for "a money action was refused and here is why",
        whether the bound that bound it was the exchange's cap or the
        merchant's own posted ceiling. `execute_match` is never reached, so no
        MATCH_PROPOSED and no settlement exist for this action_ref — and the
        match_id is now spent: a corrected retry is a new match.
        """
        decision = PolicyDecision(
            decision_id=new_id("dec"),
            action_ref=match.match_id,
            actor_id=self.actor_id,
            verdict=Verdict.DENY,
            reason=reason,
            limits_evaluated={
                **evaluated,
                "bid_order_id": match.bid_order_id,
                "qty": match.qty,
            },
            ts=datetime.now(timezone.utc).isoformat(),
        )
        self._exchange.log.append(
            self.actor_id,
            ev.POLICY_DECIDED,
            {**asdict(decision), "verdict": str(decision.verdict)},
            correlation_id=correlation_id,
        )
        return decision

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

        Before it does, the agreed figure is checked against the ceiling this
        merchant actually posted on its bid — see `_refuse_above_posted_limit`.
        """
        refusal = self._refuse_above_posted_limit(match, agreed_price, correlation_id)
        if refusal is not None:
            return refusal, None

        match = replace(match, clearing_price=agreed_price)

        ctx = PolicyContext(
            # Both of these are discarded inside execute_match and re-derived
            # from the log. They are here because the dataclass requires them,
            # not because this caller is trusted for them: a cap the actor
            # supplies its own usage figure for is not a cap, and a status the
            # actor asserts about itself is not a status. A frozen broker
            # reaching this line still gets a DENY.
            actor_status=ActorStatus.ACTIVE,
            rolling_spend=0,
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


def _first_index(text: str, count: int) -> int:
    """The first 1-based number in `text` that names a shortlist entry.

    A model that will not answer must not stop the market, so an unparseable
    reply falls back to the most relevant candidate — and the reply is still
    journalled, so the audit trail shows what it said.
    """
    for token in re.findall(r"\d+", text):
        value = int(token)
        if 1 <= value <= count:
            return value - 1
    return 0
