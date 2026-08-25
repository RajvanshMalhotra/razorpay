"""The CREDITS rail — an atomic ledger transfer.

Points never leave the system, so settlement is a single balance check
followed by a transfer event. Conservation is the invariant that matters.
"""
from __future__ import annotations

from typing import Callable

from exchange import events as ev
from exchange.eventlog import EventLog
from exchange.ids import new_id
from exchange.models import Currency, Settlement, SettlementStatus
from exchange.projections import fold
from exchange.rails.base import InsufficientCredits

# actor_id -> that actor's points balance. A FUNCTION OF THE ACTOR, never a
# number, and that is the whole point of the type. See `CreditRail.__init__`.
BalanceLookup = Callable[[str], int]


class CreditRail:
    def __init__(self, log: EventLog, balance_of: BalanceLookup | None = None) -> None:
        """`balance_of` is how the rail learns what the payer holds.

        WHY A CALLABLE AND NOT A BALANCE. The rail is the lock on the points
        ledger: it refuses a transfer the payer cannot fund. A checker that
        accepts the figure it checks is not a checker, so `settle()` takes no
        balance argument and never will — the rail asks for the balance of the
        actor IT has identified as the payer, and the answer comes from a
        derivation over the log. A caller can name itself; it cannot name its
        own number.

        WHY IT IS INJECTABLE AT ALL. Folding the whole log for one balance is
        linear in the log, and the auction pays at least 25 contributors per
        lot, so a run of any length spent most of its time re-deriving balances
        it had already derived. `Exchange` binds this to its own cached
        projection — the same values, folded incrementally instead of from
        scratch — via `bind_balance_source`.

        DEFAULT IS TODAY'S BEHAVIOUR: a full fold of the log. The rail stays
        usable, and correct, with nothing but a log; every existing caller and
        test that constructs `CreditRail(log)` gets exactly what it got before.

        NOT A DEPENDENCY ON `Exchange`. The rail must not import the service it
        is called by — that inverts the layering and makes the rail untestable
        on its own. It knows only a function from actor to integer.
        """
        self._log = log
        self._balance_of: BalanceLookup = balance_of or self._folded_balance

    def _folded_balance(self, actor_id: str) -> int:
        """The default: fold the whole log. Authoritative, and linear."""
        return fold(self._log.read_all()).credit_balances.get(actor_id, 0)

    def bind_balance_source(self, balance_of: BalanceLookup) -> None:
        """Point the rail at a faster derivation of the same figure.

        Wiring, done once by whoever assembles the exchange — not a per-trade
        argument, and deliberately not reachable from `settle()`. What is
        passed must be a derivation from the log (`Exchange` passes a lookup
        into its cached projection, which the accountant's `projection_drift`
        check proves still agrees with a full fold). Anything that is not
        derived from the log would put a second source of truth behind the one
        check standing between the payer and the ledger.
        """
        self._balance_of = balance_of

    def settle(
        self,
        match_id: str,
        from_actor_id: str,
        to_actor_id: str,
        amount: int,
        correlation_id: str,
        causation_id: str | None = None,
    ) -> Settlement:
        settlement_id = new_id("stl")

        # The balance check stays HERE, whatever the gate decided upstream.
        # Defence in depth: the gate bounds an exposure against a policy, the
        # rail refuses a transfer the ledger cannot fund, and the two are not
        # the same question. The payer is `from_actor_id` — the rail's own
        # argument for who is paying — so the figure is always about the actor
        # being constrained, and never one it supplied.
        balance = self._balance_of(from_actor_id)
        if balance < amount:
            reason = f"{from_actor_id} holds {balance} points, needs {amount}"
            # Log before raising. The gate has already written ALLOW by the time
            # we get here, and an ALLOW that resolves to nothing is exactly the
            # hole a reconciler cannot see through. Raising is still correct —
            # the caller must not continue — but the outcome is recorded first.
            self._log.append(
                from_actor_id,
                ev.SETTLEMENT_FAILED,
                {
                    "settlement_id": settlement_id,
                    "match_id": match_id,
                    "currency": str(Currency.CREDITS),
                    "amount": amount,
                    "reason": reason,
                },
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
            raise InsufficientCredits(reason)

        initiated = self._log.append(
            from_actor_id,
            ev.SETTLEMENT_INITIATED,
            {
                "settlement_id": settlement_id,
                "match_id": match_id,
                "currency": str(Currency.CREDITS),
                "amount": amount,
            },
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

        transferred = self._log.append(
            from_actor_id,
            ev.CREDITS_TRANSFERRED,
            {
                "from_actor_id": from_actor_id,
                "to_actor_id": to_actor_id,
                "amount": amount,
                "settlement_id": settlement_id,
            },
            correlation_id=correlation_id,
            causation_id=initiated.event_id,
        )

        self._log.append(
            from_actor_id,
            ev.SETTLEMENT_COMPLETED,
            {"settlement_id": settlement_id},
            correlation_id=correlation_id,
            causation_id=transferred.event_id,
        )

        return Settlement(
            settlement_id=settlement_id,
            match_id=match_id,
            currency=Currency.CREDITS,
            amount=amount,
            status=SettlementStatus.COMPLETED,
        )
