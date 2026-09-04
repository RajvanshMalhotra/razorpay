"""The event record and the vocabulary of event types."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Event:
    event_id: str
    seq: int
    ts: str
    actor_id: str
    type: str
    payload: dict[str, Any]
    causation_id: str | None
    correlation_id: str


# Event type vocabulary. Kept as constants so typos fail at import, not at runtime.
ACTOR_REGISTERED = "ACTOR_REGISTERED"
ASSET_LISTED = "ASSET_LISTED"
ORDER_POSTED = "ORDER_POSTED"
ORDER_EXPIRED = "ORDER_EXPIRED"
ORDER_FILLED = "ORDER_FILLED"
MATCH_PROPOSED = "MATCH_PROPOSED"
POLICY_DECIDED = "POLICY_DECIDED"
SETTLEMENT_INITIATED = "SETTLEMENT_INITIATED"
SETTLEMENT_COMPLETED = "SETTLEMENT_COMPLETED"
# A payment link issued for a settlement whose original could not be
# created. Recorded as its own event rather than backdated into the
# settlement, because when a link was issued is part of the record.
PAYMENT_LINK_REISSUED = "PAYMENT_LINK_REISSUED"
SETTLEMENT_FAILED = "SETTLEMENT_FAILED"
# The rail could not ask Razorpay whether a payment landed. NOT a failed
# settlement — the order exists and is payable, and the settlement stays
# PENDING for the accountant to resolve. Recorded so that "PENDING because
# nobody paid" and "PENDING because we could not look" stay distinguishable.
CAPTURE_POLL_FAILED = "CAPTURE_POLL_FAILED"
CREDITS_TRANSFERRED = "CREDITS_TRANSFERRED"
RECALL_INJECTED = "RECALL_INJECTED"
LESSON_CONSOLIDATED = "LESSON_CONSOLIDATED"
NEGOTIATION_OPENED = "NEGOTIATION_OPENED"
NEGOTIATION_ROUND = "NEGOTIATION_ROUND"
NEGOTIATION_ENDED = "NEGOTIATION_ENDED"
# The market runner's own marker that a merchant's turn reached an
# outcome. Resumption needs to tell "finished, found nothing" from
# "started, then the process died", and ORDER_POSTED means both.
TURN_ENDED = "TURN_ENDED"
COUNTERPARTY_CHOSEN = "COUNTERPARTY_CHOSEN"
INSIGHT_MINTED = "INSIGHT_MINTED"
PRIVACY_REFUSED = "PRIVACY_REFUSED"
# One row of Razorpay's internal campaign board. Marked `razorpay_internal`
# in its own payload because the audience is the point: the ranking is the
# house's view across every client, and a merchant reaches it only by
# winning the auction for a lot minted from it.
CAMPAIGN_RANKED = "CAMPAIGN_RANKED"
# Did the campaign convert? Computed from settlements, never scraped.
CAMPAIGN_PERFORMANCE = "CAMPAIGN_PERFORMANCE"
# What a category actually clears at. Arithmetic over matches.
BENCHMARK_PUBLISHED = "BENCHMARK_PUBLISHED"
AUCTION_OPENED = "AUCTION_OPENED"
BID_PLACED = "BID_PLACED"
AUCTION_CLEARED = "AUCTION_CLEARED"
RECONCILED = "RECONCILED"
# One settlement's remote lookup was rejected during a reconciliation sweep.
# The sweep continues; this settlement is simply unresolved this pass and will
# be re-checked on the next one, since nothing was learned about it.
RECONCILE_CHECK_FAILED = "RECONCILE_CHECK_FAILED"
DRIFT_DETECTED = "DRIFT_DETECTED"
# Deliberately NOT a second flavour of DRIFT_DETECTED. The two directions the
# books can disagree in demand opposite responses — one is repaired, the other
# can only be contained — and a reader who has to inspect a payload field to
# tell them apart will eventually not bother.
UNBACKED_COMPLETION_DETECTED = "UNBACKED_COMPLETION_DETECTED"
INVARIANT_VIOLATED = "INVARIANT_VIOLATED"
POINTS_MINTED = "POINTS_MINTED"
ACTOR_FROZEN = "ACTOR_FROZEN"
ACTOR_RESUMED = "ACTOR_RESUMED"
# A merchant subscribing to, or dropping, a paid plan. Separate from
# ACTOR_REGISTERED because a plan changes over a business's life and the
# registration is the moment it joined — replaying a registration to change a
# plan would also replay the status, and the frozen-merchant rule exists
# precisely because a party must not be able to re-register its way out of a
# containment.
PLAN_CHANGED = "PLAN_CHANGED"
