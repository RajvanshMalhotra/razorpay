"""What still needs paying, read out of the log.

WHY THIS EXISTS. Probed against live test mode, not assumed: a payment cannot
be created server-side — the S2S endpoint returns 403 on this account — so a
settlement completes only when someone actually pays its link. Nothing in the
system can make that happen on its own.

That is not a small inconvenience. Everything downstream is gated on a
COMPLETED settlement: orders are not filled, so the same ask matches forever;
no points are minted, so the earning half of the economy never runs;
counterparty confidence stays at zero, so the trial cap never lifts; and
`HouseAgent.observe` mines only completed settlements, so the privacy floor
sits at k=0 and no intelligence can be minted. Without payment the run
produces a log of PENDING settlements and nothing else.

THIS MODULE IS THE READ SIDE ONLY. It answers "what still needs paying?" and
nothing more. The browser automation that pays them is driven separately by the
operator, because it needs a live Chrome session — and because a module that
both decides what to pay and pays it is one bug away from paying something
twice, which is real money moving twice.

Two things established by paying a real link in test mode:

  - CARDS ARE REJECTED on this account: "International cards are not
    supported", for the standard test card. Netbanking succeeds.
  - Netbanking is the better automation target anyway — fewer fields, no card
    form, and a deterministic result page.
"""
from __future__ import annotations

from dataclasses import dataclass

from exchange import events as ev


@dataclass(frozen=True)
class PayableSettlement:
    """A settlement that money could still be sent for."""
    settlement_id: str
    actor_id: str
    amount: int              # paise
    payment_link_id: str
    payment_link_url: str
    correlation_id: str


@dataclass(frozen=True)
class UnpayableSettlement:
    """Initiated, unpaid, and with no link to pay it through.

    Reported rather than dropped. A settlement whose payment link failed to
    create is stuck in a way no amount of clicking will fix, and the run's
    post-mortem needs to tell that apart from "nobody got round to it".
    """
    settlement_id: str
    actor_id: str
    amount: int
    reason: str


@dataclass(frozen=True)
class ClerkReport:
    payable: tuple[PayableSettlement, ...] = ()
    unpayable: tuple[UnpayableSettlement, ...] = ()

    @property
    def outstanding(self) -> int:
        return sum(p.amount for p in self.payable)

    def __str__(self) -> str:
        return (
            f"{len(self.payable)} payable "
            f"({self.outstanding / 100:,.2f} rupees outstanding), "
            f"{len(self.unpayable)} unpayable"
        )


def pending_payments(log) -> ClerkReport:
    """Settlements that are initiated, not completed, and not failed.

    COMPLETED IS EXCLUDED FIRST AND DELIBERATELY. Offering an already-paid
    settlement again is how a run pays twice, and in test mode a second
    payment is as real as the first — it captures, it appears in the books,
    and it makes the accountant's totals wrong in a way that looks like a
    conservation bug rather than an operator error.
    """
    events = log.read_all()

    settled = {
        e.payload["settlement_id"]
        for e in events
        if e.type in (ev.SETTLEMENT_COMPLETED, ev.SETTLEMENT_FAILED)
    }

    payable: list[PayableSettlement] = []
    unpayable: list[UnpayableSettlement] = []
    seen: set[str] = set()

    for event in events:
        if event.type != ev.SETTLEMENT_INITIATED:
            continue
        sid = event.payload["settlement_id"]
        if sid in settled or sid in seen:
            continue
        seen.add(sid)

        # Points move by ledger transfer and have no link to pay.
        if event.payload.get("currency") not in (None, "INR"):
            continue

        link_id = event.payload.get("payment_link_id")
        link_url = event.payload.get("payment_link_url")
        if not link_id or not link_url:
            unpayable.append(UnpayableSettlement(
                settlement_id=sid,
                actor_id=event.actor_id,
                amount=event.payload["amount"],
                reason=event.payload.get("payment_link_error")
                or "no payment link was recorded for this settlement",
            ))
            continue

        payable.append(PayableSettlement(
            settlement_id=sid,
            actor_id=event.actor_id,
            amount=event.payload["amount"],
            payment_link_id=link_id,
            payment_link_url=link_url,
            correlation_id=event.correlation_id,
        ))

    return ClerkReport(payable=tuple(payable), unpayable=tuple(unpayable))
