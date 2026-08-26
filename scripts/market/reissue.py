"""Issue a payment link for a settlement whose original could not be created.

Razorpay caps test-mode payment links at 30 per account. A run that exhausts
the quota leaves real settlements — negotiated, gated, agreed and initiated —
with no way for their money to move. The trade happened; only the convenience
of a link is missing.

Reissuing is what a business does with an invoice whose link failed, and it is
honest for the same reason: the settlement, the amount and the counterparties
are unchanged and already on the record. What changes is that a payable
instrument now exists for it.

RECORDED AS ITS OWN EVENT, never backdated into the settlement. When a link
was issued is part of the record, and a reader comparing this log against
Razorpay's dashboard should find the later timestamp rather than be quietly
told the link existed all along. `PAYMENT_LINK_REISSUED` carries the reason
the first one failed, so the quota story survives in the log too.
"""
from __future__ import annotations

from dataclasses import dataclass

from exchange import events as ev
from scripts.market.clerk import pending_payments


@dataclass(frozen=True)
class ReissueReport:
    issued: int = 0
    failed: int = 0
    skipped: int = 0

    def __str__(self) -> str:
        return (f"issued {self.issued} link(s), "
                f"{self.failed} failed, {self.skipped} skipped")


def reissue(log, client, limit: int = 10, one_per_merchant: bool = True):
    """Create links for unpayable settlements, newest quota permitting.

    `one_per_merchant` by default: the privacy floor counts DISTINCT
    contributing merchants, so a second link for a merchant that already has
    one buys nothing and spends a capped resource that another merchant needs.
    """
    report = pending_payments(log)
    already = {p.actor_id for p in report.payable}

    issued = failed = skipped = 0
    for unpayable in report.unpayable:
        if issued >= limit:
            break
        if one_per_merchant and unpayable.actor_id in already:
            skipped += 1
            continue
        try:
            link = client.payment_link.create({
                "amount": unpayable.amount,
                "currency": "INR",
                "description": f"Exchange settlement {unpayable.settlement_id}",
                "notes": {"settlement_id": unpayable.settlement_id,
                          "reissued": "true"},
                "reference_id": f"{unpayable.settlement_id}_reissue",
            })
        except Exception as exc:  # noqa: BLE001 - a failed reissue is recorded
            log.append(
                unpayable.actor_id, ev.SETTLEMENT_FAILED,
                {"settlement_id": unpayable.settlement_id,
                 "match_id": "reissue", "currency": "INR",
                 "amount": unpayable.amount,
                 "reason": f"reissue failed: {type(exc).__name__}: {exc}"},
                correlation_id=f"reissue_{unpayable.settlement_id}",
            )
            failed += 1
            continue

        log.append(
            unpayable.actor_id, ev.PAYMENT_LINK_REISSUED,
            {"settlement_id": unpayable.settlement_id,
             "amount": unpayable.amount,
             "payment_link_id": link.get("id"),
             "payment_link_url": link.get("short_url"),
             "original_failure": unpayable.reason},
            correlation_id=f"reissue_{unpayable.settlement_id}",
        )
        already.add(unpayable.actor_id)
        issued += 1

    return ReissueReport(issued=issued, failed=failed, skipped=skipped)
