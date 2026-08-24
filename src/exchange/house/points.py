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
    """
    if not delivered:
        return 0

    asked = ask_price * qty
    margin = asked - amount
    if margin < 0:
        return 0  # you cannot be paid for overpaying

    return BASE_POINTS + (margin * EARNING_RATE_BPS) // 10_000


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
