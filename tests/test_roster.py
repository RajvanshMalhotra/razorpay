"""The roster is data, and these are the properties that make it usable.

The house agent's whole value is finding a cross-merchant pattern no single
merchant can see. Thirty merchants with unrelated random needs contain no such
pattern, so the agent would either report nothing or invent one — a boring demo
or a dishonest one. So the roster plants a trend.

We plant the CAUSE, never the conclusion: the agent gets no privileged access
and must find it in the same aggregate activity everyone else sees. These tests
exist to keep that claim true — particularly the second one, which asserts the
trend genuinely rises in the data rather than merely being asserted about.
"""
from collections import Counter

from scripts.market.roster import MERCHANTS, demand_by_round

CLUSTER_INPUT = "cold brew concentrate"

# Generic on their own — "grade", "bulk", "industrial" appear across unrelated
# goods, so single-word overlap matches things no buyer would confuse. Two
# consecutive words is a far better proxy for "these are the same product",
# and it is only a proxy: the real system uses hybrid retrieval.
def _bigrams(text: str) -> set[tuple[str, str]]:
    words = [w for w in text.lower().replace(",", " ").split() if len(w) > 3]
    return set(zip(words, words[1:]))


def _sellers_of(need_text: str):
    wanted = _bigrams(need_text)
    return [
        (m, listing)
        for m in MERCHANTS for listing in m.sells
        if wanted & _bigrams(listing.title)
    ]


def test_there_are_enough_merchants_to_clear_the_privacy_floor():
    """K_MIN counts DISTINCT contributors. Below it nothing can be minted,
    so nothing downstream exists at all."""
    from exchange.house.insights import K_MIN

    assert len(MERCHANTS) >= 30
    assert len({m.actor_id for m in MERCHANTS}) == len(MERCHANTS)
    assert len(MERCHANTS) > K_MIN, "leave room for merchants that never trade"


def test_the_planted_trend_actually_rises():
    """The trend has to be IN the data, not claimed about it.

    If demand for the cluster's input does not really grow, the house agent
    has nothing true to find and would have to make something up.
    """
    demand = demand_by_round(CLUSTER_INPUT)
    rounds = sorted(demand)

    assert len(rounds) >= 3, "a trend needs more than two points"
    assert demand[rounds[0]] < demand[rounds[-1]]
    assert all(demand[a] <= demand[b] for a, b in zip(rounds, rounds[1:])), demand


def test_the_trend_is_not_the_only_thing_happening():
    """A market where every merchant wants the same thing is not a market,
    and a signal with no noise around it is not a discovery."""
    categories = Counter(m.category for m in MERCHANTS)

    assert len(categories) >= 4
    assert max(categories.values()) < len(MERCHANTS) // 2, categories


def test_the_cluster_has_more_than_one_supplier():
    """`choose()` needs a real decision to make, and the Diplomat's advice
    only matters when there is someone else to pick."""
    sellers = [
        m for m in MERCHANTS
        if any(CLUSTER_INPUT in listing.title.lower() for listing in m.sells)
    ]

    assert len(sellers) >= 3
    prices = {listing.ask_price
              for m in sellers for listing in m.sells
              if CLUSTER_INPUT in listing.title.lower()}
    assert len(prices) > 1, "identical prices make the choice arbitrary"


def test_every_need_can_in_principle_be_met():
    """A need nobody sells is dead weight: it burns model calls on a search
    that cannot succeed and teaches the Subconscious nothing."""
    for merchant in MERCHANTS:
        for need in merchant.needs:
            assert _sellers_of(need.text), (
                f"{merchant.actor_id} needs {need.text!r}, which nobody sells"
            )


def test_personas_differ():
    """Identical personas produce identical transcripts, which is a boring
    video and a market with nothing to watch."""
    personas = {m.persona for m in MERCHANTS}

    assert len(personas) >= len(MERCHANTS) // 2


def test_amounts_are_integers_in_paise():
    """Never floats. A rupee amount that arrives as 19.4 is a rounding bug
    waiting to reach the gate."""
    for merchant in MERCHANTS:
        for listing in merchant.sells:
            assert isinstance(listing.ask_price, int), listing
            assert isinstance(listing.qty, int)
            assert listing.ask_price > 0
        for need in merchant.needs:
            assert isinstance(need.limit_price, int), need
            assert isinstance(need.qty, int)
            assert need.limit_price > 0


def test_a_buyer_can_actually_afford_what_it_asks_for():
    """A need priced below every ask never trades, and a roster full of them
    produces a run with no settlements and no explanation."""
    for merchant in MERCHANTS:
        for need in merchant.needs:
            reachable = [listing.ask_price
                         for _, listing in _sellers_of(need.text)]
            assert any(price <= need.limit_price for price in reachable), (
                f"{merchant.actor_id} caps {need.text!r} at {need.limit_price}, "
                f"below every ask ({sorted(set(reachable))})"
            )


def test_asset_ids_are_unique_across_the_whole_roster():
    """Two merchants sharing an asset id would collide in the book, and the
    order lookup resolves by id."""
    ids = [listing.asset_id for m in MERCHANTS for listing in m.sells]

    assert len(ids) == len(set(ids))


def test_nobody_sells_what_they_are_asking_to_buy():
    """Self-dealing mints nothing by design, so a merchant matched against
    its own ask is a wasted turn and a confusing trace."""
    for merchant in MERCHANTS:
        for need in merchant.needs:
            own_matches = [m.actor_id for m, _ in _sellers_of(need.text)
                           if m.actor_id == merchant.actor_id]
            assert not own_matches, (
                f"{merchant.actor_id} both sells and needs {need.text!r}"
            )


# --- every merchant briefs its own agent -------------------------------------

def test_every_merchant_has_a_style():
    """`persona` existed from the start and nothing read it, so all 32
    brokers ran identical prompts and the roster's variety was decorative."""
    assert all(m.style for m in MERCHANTS)


def test_styles_are_real_keywords():
    """A typo would silently become prose and quietly stop steering."""
    from exchange.agents.mandate import KEYWORDS

    for merchant in MERCHANTS:
        for word in merchant.style.split(","):
            key = word.strip().lower().replace(" ", "_").replace("-", "_")
            assert key in KEYWORDS, f"{merchant.actor_id}: {key!r}"


def test_the_mandate_carries_both_the_style_and_the_merchants_words():
    from exchange.agents.mandate import Mandate

    merchant = next(m for m in MERCHANTS if m.actor_id == "m_nilgiri_cold")
    mandate = Mandate.from_input(merchant.mandate_input())

    assert "aggressive" in mandate.keywords
    assert "price_first" in mandate.keywords
    assert "Cheapest in the market" in mandate.note


def test_the_cluster_does_not_brief_its_agents_identically():
    """Nine merchants in one category briefing the same way reads as one
    buyer with nine names, and gives the Diplomat nothing to distinguish."""
    cluster = [m for m in MERCHANTS if m.category == "beverage"]

    assert len({m.style for m in cluster}) >= len(cluster) // 2


def test_no_merchant_mandate_survives_sanitising_as_an_instruction():
    """The roster is our own input, but it goes through the same door a
    merchant's does — so it must pass the same check."""
    from exchange.agents.mandate import Mandate

    for merchant in MERCHANTS:
        assert not Mandate.from_input(merchant.mandate_input()).rejected
