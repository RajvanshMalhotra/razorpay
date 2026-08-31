"""The internal campaign board.

The board makes one claim — *this is what is climbing across Razorpay's
clients* — and that claim is only worth anything if the numbers come from
the log and nothing else can move them. Most of these tests are about the
second half of that sentence.
"""
import pytest

from exchange import events as ev
from exchange.eventlog import EventLog
from exchange.house.campaigns import (
    THREADS_PER_ROW,
    Campaign,
    Source,
    fetch_news,
    label,
    observe,
    publish,
    rank,
    research,
)


class Says:
    """A provider that returns exactly what it was handed."""

    def __init__(self, text):
        self.text = text
        self.calls = 0

    def complete(self, messages, **kwargs):
        self.calls += 1
        return type("R", (), {"text": self.text})()


def _turn(actor, round_no, need, outcome="settled", amount=100_000):
    return type("E", (), {
        "type": ev.TURN_ENDED, "actor_id": actor,
        "payload": {"round": round_no, "need": need,
                    "outcome": outcome, "amount": amount},
    })()


# --- reading the log ---------------------------------------------------------

def test_only_turns_that_ended_are_read():
    events = [
        _turn("m_a", 1, "cold brew concentrate in bulk"),
        type("E", (), {"type": ev.ORDER_POSTED, "actor_id": "m_a",
                       "payload": {"qty": 40}})(),
    ]

    assert len(observe(events)) == 1


def test_a_turn_with_no_need_text_is_skipped():
    """A seeded ask has no buyer's words behind it, so it names no campaign."""
    events = [_turn("m_a", 1, "   "), _turn("m_b", 1, "jute sacks")]

    assert [t.need for t in observe(events)] == ["jute sacks"]


def test_a_walked_turn_is_still_read():
    """Demand that failed to clear is still demand, and the attempt count is
    how a reader sees a campaign that is hot but not converting."""
    turns = observe([_turn("m_a", 1, "cold brew", outcome="walked", amount=0)])

    assert turns[0].outcome == "walked"


# --- the ranking, which is arithmetic ----------------------------------------

def test_movement_compares_the_closing_rounds_to_the_opening_ones():
    turns = observe([
        _turn("m_a", 1, "cold brew", amount=100_000),
        _turn("m_b", 1, "cold brew", amount=100_000),
        _turn("m_c", 5, "cold brew", amount=600_000),
    ])

    ranked, _ = rank(turns, {"cold brew": "Cold Brew"}, floor=3)

    assert ranked[0].early_paise == 200_000
    assert ranked[0].late_paise == 600_000
    assert ranked[0].movement == 3.0


def test_a_campaign_with_no_early_value_reports_zero_not_infinity():
    """Dividing by an empty opening round would put a made-up number at the
    top of the board, which is the one place a made-up number does most
    damage."""
    turns = observe([
        _turn("m_a", 5, "nitro taps", amount=500_000),
        _turn("m_b", 5, "nitro taps", amount=500_000),
        _turn("m_c", 5, "nitro taps", amount=500_000),
    ])

    ranked, _ = rank(turns, {"nitro taps": "Nitro Taps"}, floor=3)

    assert ranked[0].movement == 0.0
    assert ranked[0].early_paise == 0


def test_only_settled_turns_carry_value():
    turns = observe([
        _turn("m_a", 1, "cold brew", outcome="walked", amount=900_000),
        _turn("m_b", 1, "cold brew", amount=100_000),
        _turn("m_c", 1, "cold brew", amount=100_000),
    ])

    ranked, _ = rank(turns, {"cold brew": "Cold Brew"}, floor=3)

    assert ranked[0].value_paise == 200_000
    assert ranked[0].attempts == 3
    assert ranked[0].settled == 2


def test_the_fastest_climb_outranks_the_biggest_number():
    """The largest category is usually largest for boring reasons. A board
    that sorted by size would report the boring thing every time."""
    turns = observe([
        _turn("m_a", 1, "cartons", amount=5_000_000),
        _turn("m_b", 1, "cartons", amount=5_000_000),
        _turn("m_c", 5, "cartons", amount=5_000_000),
        _turn("m_d", 1, "cold brew", amount=100_000),
        _turn("m_e", 1, "cold brew", amount=100_000),
        _turn("m_f", 5, "cold brew", amount=900_000),
    ])

    ranked, _ = rank(turns, {"cartons": "Cartons", "cold brew": "Cold Brew"},
                     floor=3)

    assert [c.name for c in ranked] == ["Cold Brew", "Cartons"]


def test_ranking_reads_no_press_and_calls_no_model():
    """The load-bearing separation. If `rank` could reach either, a headline
    could become a figure, and nothing on the board would be checkable."""
    import inspect

    source = inspect.getsource(rank)

    assert "provider" not in source
    assert "fetch" not in source and "news" not in source.lower()
    # Reddit is a third source and gets the same bar. An upvote count is not
    # allowed to become a ranking figure any more than a headline is.
    assert "social" not in source and "reddit" not in source.lower()
    assert "discussion" not in source


# --- the floor ---------------------------------------------------------------

def test_a_campaign_below_the_floor_is_refused_not_ranked():
    turns = observe([_turn("m_a", 1, "lithium cells"),
                     _turn("m_a", 2, "lithium cells")])

    ranked, refused = rank(turns, {"lithium cells": "Lithium Cells"}, floor=3)

    assert ranked == []
    assert refused[0].k == 1
    assert "below the board floor" in refused[0].reason


def test_the_floor_counts_distinct_merchants_not_rows():
    """One merchant trading four times is one merchant. Counting rows would
    let a single business publish itself."""
    turns = observe([_turn("m_a", r, "cold brew") for r in (1, 2, 3, 4)])

    ranked, refused = rank(turns, {"cold brew": "Cold Brew"}, floor=3)

    assert ranked == []
    assert refused[0].k == 1


def test_an_empty_log_ranks_nothing_and_does_not_raise():
    assert rank([], {}, floor=3) == ([], [])


# --- labelling ---------------------------------------------------------------

def test_distinct_phrasings_become_one_campaign():
    mapping, fallbacks = label(
        ["cold brew concentrate in bulk", "unsweetened cold brew, cafe grade"],
        Says("1: Cold Brew Concentrate\n2: Cold Brew Concentrate"),
    )

    assert len(set(mapping.values())) == 1
    assert fallbacks == 0


def test_a_phrase_the_model_skipped_still_gets_a_campaign():
    """An unlabelled phrase dropped from the board would shrink a campaign's
    value and merchant count silently — and an undercount is indistinguishable
    from a real decline."""
    mapping, fallbacks = label(["cold brew in bulk", "jute sacks hessian lined"],
                               Says("1: Cold Brew"))

    assert set(mapping) == {"cold brew in bulk", "jute sacks hessian lined"}
    assert mapping["jute sacks hessian lined"]
    assert fallbacks == 1, "the caller must be able to see the model missed one"


def test_labelling_an_empty_list_calls_no_model():
    provider = Says("")

    assert label([], provider) == ({}, 0)
    assert provider.calls == 0


def test_a_reply_the_parser_cannot_read_leaves_every_phrase_named():
    mapping, fallbacks = label(["cold brew in bulk"],
                               Says("I could not do that."))

    assert mapping["cold brew in bulk"] == "Cold Brew In Bulk"
    assert fallbacks == 1, "an unreadable reply is a failure, not a grouping"


# --- the press ---------------------------------------------------------------

def test_sources_are_parsed_with_publisher_and_date():
    rss = b"""<rss><channel><item>
      <title>A real headline</title>
      <link>https://example.com/a</link>
      <pubDate>Fri, 28 Aug 2026 10:02:28 GMT</pubDate>
      <source>Indian Retailer</source>
    </item></channel></rss>"""

    sources = fetch_news("anything", opener=lambda *a, **k: type(
        "R", (), {"read": staticmethod(lambda: rss)})())

    assert sources[0].url == "https://example.com/a"
    assert sources[0].publisher == "Indian Retailer"
    assert sources[0].published.startswith("Fri, 28 Aug 2026")


def test_a_news_outage_leaves_the_ranking_standing():
    """The press is an explanation, not evidence. Losing it must not lose the
    row, and the row must say the explanation is missing rather than imply
    one was found."""
    def dead(*args, **kwargs):
        raise OSError("no network")

    campaign = research(
        Campaign(name="Cold Brew", merchants=("m_a", "m_b", "m_c")),
        Says("should never be called"),
        fetcher=lambda q: fetch_news(q, opener=dead),
    )

    assert campaign.sources == []
    assert "no public coverage" in campaign.driver


def test_research_never_changes_a_number():
    before = Campaign(name="Cold Brew", merchants=("m_a", "m_b", "m_c"),
                      value_paise=900_000, early_paise=100_000,
                      late_paise=800_000, settled=3, attempts=4)

    after = research(before, Says("Brands are leaning on summer bundles."),
                     fetcher=lambda q: [Source("H", "u", "d", "p")])

    assert after.value_paise == 900_000
    assert after.movement == 8.0
    assert after.driver == "Brands are leaning on summer bundles."


# --- what lands in the log ---------------------------------------------------

@pytest.fixture
def log(tmp_path):
    log = EventLog(str(tmp_path / "board.db"))
    yield log
    log.close()


def test_every_row_is_marked_internal(log):
    """The audience is the product decision. A row that lost this marking
    would be indistinguishable from the free merchant-facing feed."""
    publish(log, [Campaign(name="Cold Brew", merchants=("m_a", "m_b", "m_c"))],
            [], correlation_id="research_1")

    rows = [e for e in log.read_all() if e.type == ev.CAMPAIGN_RANKED]
    assert rows[0].payload["audience"] == "razorpay_internal"


def test_rank_position_is_written_not_implied_by_order(log):
    """Reading rank from log order would break the moment anything else
    appended between two rows."""
    publish(log, [Campaign(name="A", merchants=("m_a", "m_b", "m_c")),
                  Campaign(name="B", merchants=("m_d", "m_e", "m_f"))],
            [], correlation_id="research_1")

    rows = [e for e in log.read_all() if e.type == ev.CAMPAIGN_RANKED]
    assert [r.payload["rank"] for r in rows] == [1, 2]


def test_sources_reach_the_log_with_their_urls(log):
    """A citation nobody can follow is a claim. The URL is what makes the
    sentence beside a row checkable rather than decorative."""
    campaign = Campaign(name="Cold Brew", merchants=("m_a", "m_b", "m_c"))
    campaign.sources = [Source("A real headline", "https://example.com/a",
                              "Fri, 28 Aug 2026", "Indian Retailer")]

    publish(log, [campaign], [], correlation_id="research_1")

    row = next(e for e in log.read_all() if e.type == ev.CAMPAIGN_RANKED)
    assert row.payload["sources"][0]["url"] == "https://example.com/a"


def test_a_refusal_is_published_as_loudly_as_a_row(log):
    """A floor nobody can see is indistinguishable from no floor."""
    from exchange.house.campaigns import Refusal

    publish(log, [], [Refusal("Lithium Cells", 1, "below the board floor")],
            correlation_id="research_1")

    refusals = [e for e in log.read_all() if e.type == ev.PRIVACY_REFUSED]
    assert refusals[0].payload["scope"] == "campaign_board"
    assert refusals[0].payload["campaign"] == "Lithium Cells"


def test_the_whole_board_reads_on_one_thread(log):
    """Rows and refusals share a correlation id, so `read_by_correlation`
    returns the board exactly as it was published — including what was left
    off it."""
    from exchange.house.campaigns import Refusal

    publish(log, [Campaign(name="A", merchants=("m_a", "m_b", "m_c"))],
            [Refusal("B", 1, "below")], correlation_id="research_1")

    assert len(log.read_by_correlation("research_1")) == 2


# --- what operators are saying -----------------------------------------------

def _reddit(discussion="Operators say margins are thin.", posts=None,
            blocked=False):
    """Stand-in for `social.research_topic`, which is the shape research
    consumes. Returning the dict rather than the module keeps these tests off
    the network entirely."""
    def call(topic):
        return {"topic": topic, "source": "rss", "blocked": blocked,
                "communities": [], "discussion": discussion,
                "posts": posts if posts is not None else [
                    {"title": "Anyone else seeing cold brew slow down?",
                     "subreddit": "r/indiacoffee", "url": "https://r/1",
                     "when": "2026-08-02", "score": 41, "comments": 12},
                ]}
    return call


def test_the_discussion_rides_beside_the_press_not_instead_of_it():
    campaign = research(
        Campaign(name="Cold Brew", merchants=("m_a", "m_b", "m_c")),
        Says("Brands are leaning on summer bundles."),
        fetcher=lambda q: [Source("H", "u", "d", "p")],
        social=_reddit(),
    )

    assert campaign.driver == "Brands are leaning on summer bundles."
    assert campaign.discussion == "Operators say margins are thin."
    assert campaign.sources and campaign.threads
    assert campaign.threads[0]["subreddit"] == "r/indiacoffee"


def test_a_quiet_press_does_not_lose_the_discussion():
    """The categories nobody has written about are exactly the ones only
    operators are talking about. An `else` here would drop them."""
    campaign = research(
        Campaign(name="Cold Brew", merchants=("m_a", "m_b", "m_c")),
        Says("should never be called"),
        fetcher=lambda q: [],
        social=_reddit(),
    )

    assert "no public coverage" in campaign.driver
    assert campaign.discussion == "Operators say margins are thin."


def test_a_refusal_is_not_reported_as_a_silence():
    campaign = research(
        Campaign(name="Cold Brew", merchants=("m_a", "m_b", "m_c")),
        Says("x"),
        fetcher=lambda q: [Source("H", "u", "d", "p")],
        social=_reddit(discussion="", posts=[], blocked=True),
    )

    assert campaign.discussion_blocked is True
    assert "refused" in campaign.discussion
    assert "nothing substantive" not in campaign.discussion


def test_a_genuinely_quiet_category_says_so_without_claiming_a_refusal():
    campaign = research(
        Campaign(name="Cold Brew", merchants=("m_a", "m_b", "m_c")),
        Says("x"),
        fetcher=lambda q: [Source("H", "u", "d", "p")],
        social=_reddit(discussion="", posts=[], blocked=False),
    )

    assert campaign.discussion_blocked is False
    assert "nothing substantive" in campaign.discussion


def test_reddit_falling_over_leaves_the_row_standing():
    def dead(topic):
        raise OSError("no network")

    campaign = research(
        Campaign(name="Cold Brew", merchants=("m_a", "m_b", "m_c"),
                 value_paise=900_000),
        Says("Brands are leaning on summer bundles."),
        fetcher=lambda q: [Source("H", "u", "d", "p")],
        social=dead,
    )

    assert campaign.value_paise == 900_000
    assert campaign.driver == "Brands are leaning on summer bundles."
    assert campaign.discussion_blocked is True
    assert "OSError" in campaign.discussion


def test_the_discussion_never_changes_a_number():
    before = Campaign(name="Cold Brew", merchants=("m_a", "m_b", "m_c"),
                      value_paise=900_000, early_paise=100_000,
                      late_paise=800_000, settled=3, attempts=4)

    after = research(before, Says("d"),
                     fetcher=lambda q: [Source("H", "u", "d", "p")],
                     social=_reddit(discussion="Everyone says it is booming."))

    assert after.value_paise == 900_000
    assert after.movement == 8.0
    assert after.settled == 3


def test_only_a_handful_of_threads_travel_with_a_row():
    many = [{"title": f"t{i}", "subreddit": "r/x", "url": f"u{i}",
             "when": "d", "score": i, "comments": i} for i in range(20)]

    campaign = research(
        Campaign(name="Cold Brew", merchants=("m_a", "m_b", "m_c")),
        Says("d"),
        fetcher=lambda q: [Source("H", "u", "d", "p")],
        social=_reddit(posts=many),
    )

    assert len(campaign.threads) == THREADS_PER_ROW


def test_no_reddit_configured_still_publishes_the_board():
    """`social=None` is the default and must not fabricate an empty section
    that reads as 'we looked and nobody was talking'."""
    campaign = research(
        Campaign(name="Cold Brew", merchants=("m_a", "m_b", "m_c")),
        Says("d"),
        fetcher=lambda q: [Source("H", "u", "d", "p")],
    )

    assert campaign.discussion == ""
    assert campaign.threads == []
    assert campaign.discussion_blocked is False
