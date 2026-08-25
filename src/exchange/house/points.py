"""What a merchant earns, and why it is not volume.

Volume-weighting makes the largest merchant win by round three: it earns
most, buys the best intelligence, trades better, earns more. The market
ossifies and there is nothing left to watch. So the rule pays for MARGIN
CAPTURED — the gap between the ask and what was actually paid — which a
small merchant can win by negotiating well and a large one can lose by
overpaying.

Minted only by the accountant, and only two ways: against a settled trade
(once per settlement, by the rule below) or as a capped opening grant. Both
run through `Accountant.mint`, and `assert_invariants` checks that no other
actor wrote a POINTS_MINTED — the claim is enforced rather than asserted.
"""
from __future__ import annotations

EARNING_RATE_BPS = 500      # 5% of margin captured, in basis points
BASE_POINTS = 10            # a completed trade is worth something on its own
ROYALTY_SHARE_BPS = 3000    # 30% of a clearing price goes back to contributors

# The one mint that is not derived from a settled trade: an opening balance for
# a merchant whose earning history predates this log. It is capped rather than
# free because "where do points come from?" must have a bounded answer, and an
# uncapped grant is the same unbounded source the raw `log.append` was. Every
# grant is logged with its reason and carries no source settlement, so the two
# kinds of mint are distinguishable in the trail forever.
OPENING_GRANT_CAP = 2_000


def points_for_settlement(
    amount: int,
    ask_price: int,
    qty: int,
    delivered: bool,
) -> int:
    """Points for one settled trade.

    `amount` is what was actually paid for the whole lot; `ask_price * qty` is
    what the seller opened at. The difference is the margin the broker captured
    by negotiating, and that is what is rewarded.

    THE CREDITED MARGIN IS BOUNDED BY THE MONEY THAT ACTUALLY MOVED. Of the
    four inputs here, exactly one is backed by an authority outside the two
    parties: `amount` is what Razorpay moved and confirmed captured. `ask_price`
    and `qty` come from orders the parties wrote themselves, and two merchants
    who agree to lie can put any number on an ask. `min(margin, amount)` is what
    stops that being worth anything: a merchant may be credited with capturing
    at most as much margin as it actually paid out, which is to say the ask can
    be treated as at most twice what was paid.

    What the bound means economically: a claimed margin far larger than the
    money that changed hands is not evidence of a hard negotiation, it is
    evidence that the ask was fantasy. Nobody lists at a million rupees and
    sells for one paisa; the price a market will actually bear is bounded by
    what somebody actually paid. So a one-paisa trade earns like a one-paisa
    trade (BASE_POINTS and nothing more) no matter what ask it names, while a
    genuine deal — anything settled above half the ask, which is every real
    negotiation — is unaffected and earns its full margin.
    """
    if not delivered:
        return 0

    # BASE_POINTS used to sit OUTSIDE the bound below, which made the bound
    # leak: a settlement of zero was still "a completed trade", so it paid
    # BASE_POINTS while consuming none of the spend cap. Ten of them minted a
    # hundred points for nothing, and the auditor had nothing to report.
    #
    # The gate now refuses a non-positive amount outright, so this is the
    # second lock on the same door. It stays because the rule belongs here
    # too: BASE_POINTS is what a *trade* is worth on its own, and a transfer
    # of nothing is not a trade.
    if amount <= 0:
        return 0

    asked = ask_price * qty
    margin = asked - amount
    if margin < 0:
        return 0  # you cannot be paid for overpaying

    credited = min(margin, amount)
    return BASE_POINTS + (credited * EARNING_RATE_BPS) // 10_000


def royalty_for(clearing_price: int, contributor_count: int) -> int:
    """What each contributing merchant earns when a lot derived from their
    activity is bought.

    Priced off the clearing price so a valuable win earns its contributors
    more. This is what turns the intelligence product from extraction into a
    deal — and it is the answer to "you're selling my campaign to my
    competitor".
    """
    if contributor_count <= 0:
        return 0
    pool = (clearing_price * ROYALTY_SHARE_BPS) // 10_000
    return pool // contributor_count
