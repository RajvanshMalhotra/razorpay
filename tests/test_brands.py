"""The brand campaign radar: what companies are running, ranked from talk."""
import inspect

from exchange.eventlog import EventLog
from exchange.house.brands import (
    MIN_THREADS,
    Mention,
    XAPI,
    discover,
    label,
    publish,
    rank,
)
from exchange.house.social import Post


class Says:
    """A provider that returns exactly what it was handed."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def complete(self, messages, **kwargs):
        self.calls += 1
        return type("R", (), {"text": self.text})()


def _m(brand, title, community, source="reddit", score=None, comments=None):
    return Mention(brand=brand, title=title, url=f"u/{title}",
                   community=community, source=source, when="2026-08-30",
                   score=score, comments=comments)


# --- the load-bearing separation ---------------------------------------------

def test_ranking_calls_no_model_and_reads_no_network():
    """If `rank` could reach either, a loud opinion could become a position,
    and nothing on the radar would be checkable."""
    source = inspect.getsource(rank)
    # The docstring explains what rank must not touch, using the very words
    # being searched for. Scan the code, or the test passes on its own prose.
    # (getdoc re-indents, so it never matches the raw source — cut the span.)
    start = source.index('"""')
    body = source[:start] + source[source.index('"""', start + 3) + 3:]

    assert "provider" not in body
    assert "complete(" not in body
    assert "search" not in body.lower()
    assert "urllib" not in body and "http" not in body.lower()


def test_spread_beats_volume():
    """Forty posts in one subreddit is a community with a hobby. Eight across
    five is a campaign the market noticed."""
    loud = [_m("Zomato", f"Zomato monsoon ad {i}", "marketing") for i in range(8)]
    broad = [_m("CRED", f"CRED rebrand {i}", c) for i, c in
             enumerate(("marketing", "advertising", "PPC", "ecommerce"))]
    mentions = loud + broad
    labels = ({i: ("Zomato", "Zomato monsoon") for i in range(1, 9)}
              | {i: ("CRED", "CRED rebrand") for i in range(9, 13)})

    ranked, _ = rank(mentions, labels)

    # 8 threads x 1 community = 8; 4 threads x 4 communities = 16.
    assert [r.name for r in ranked] == ["CRED rebrand", "Zomato monsoon"]


def test_a_campaign_in_one_thread_is_refused_and_the_refusal_is_logged():
    mentions = [_m("Nykaa", "Nykaa festive ad", "marketing"),
                _m("boAt", "boAt launch a", "marketing"),
                _m("boAt", "boAt launch b", "PPC")]
    labels = {1: ("Nykaa", "Nykaa festive"), 2: ("boAt", "boAt launch"),
              3: ("boAt", "boAt launch")}

    ranked, refused = rank(mentions, labels)

    assert [r.name for r in ranked] == ["boAt launch"]
    assert [r.name for r in refused] == ["Nykaa festive"]
    assert str(MIN_THREADS) in refused[0].reason


def test_the_order_is_total_so_a_rerun_cannot_shuffle():
    """Two campaigns with identical heat must still have a fixed order."""
    mentions = [_m("A", "A one", "marketing"), _m("A", "A two", "PPC"),
                _m("B", "B one", "marketing"), _m("B", "B two", "PPC")]
    labels = {1: ("A", "A camp"), 2: ("A", "A camp"),
              3: ("B", "B camp"), 4: ("B", "B camp")}

    first, _ = rank(mentions, labels)
    second, _ = rank(list(reversed(mentions)),
                     {1: ("B", "B camp"), 2: ("B", "B camp"),
                      3: ("A", "A camp"), 4: ("A", "A camp")})

    assert [r.name for r in first] == [r.name for r in second]


def test_engagement_breaks_a_tie_but_never_makes_the_ranking():
    """A run with no credentials reports 0 engagement everywhere, and must
    still produce a real order rather than a flat one."""
    mentions = [_m("A", "A one", "marketing", score=500, comments=100),
                _m("A", "A two", "PPC", score=500, comments=100),
                _m("B", "B one", "marketing"), _m("B", "B two", "PPC")]
    labels = {1: ("A", "A camp"), 2: ("A", "A camp"),
              3: ("B", "B camp"), 4: ("B", "B camp")}

    ranked, _ = rank(mentions, labels)

    assert [r.name for r in ranked] == ["A camp", "B camp"]
    assert ranked[0].heat == ranked[1].heat  # the heat did not decide it
    assert ranked[1].engagement == 0


# --- grouping ----------------------------------------------------------------

def test_unattributed_posts_are_counted_not_silently_dropped():
    """A model that returns nothing produces zero rows, and zero rows looks
    exactly like a quiet week."""
    mentions = [_m("", "Some ad", "marketing")] * 3

    mapping, fallbacks = label(mentions, Says(""))

    assert mapping == {}
    assert fallbacks == 3


def test_a_post_about_using_a_companys_ad_platform_is_not_its_campaign():
    """The measurement that forced attribution onto the model: "learn meta and
    google adds campaign" is a beginner asking about tools, and lexical
    matching filed it as evidence that both companies ran noticed campaigns."""
    mentions = [_m("", "How difficult it is learn meta and google adds campaign?", "PPC"),
                _m("", "Instagram rebrand its wordmark", "marketing")]

    mapping, fallbacks = label(
        mentions, Says("1: none\n2: Instagram | Instagram wordmark rebrand"))

    assert mapping == {2: ("Instagram", "Instagram wordmark rebrand")}
    assert fallbacks == 1


def test_a_campaign_named_without_a_company_is_dropped():
    """There would be nothing to check the row against."""
    mapping, fallbacks = label([_m("", "Some rebrand", "marketing")],
                               Says("1: a clever rebrand"))

    assert mapping == {} and fallbacks == 1


def test_no_mentions_never_calls_the_model():
    provider = Says("")
    assert label([], provider) == ({}, 0)
    assert provider.calls == 0


# --- collecting --------------------------------------------------------------

def test_discover_collects_without_guessing_who_a_post_is_about():
    """Attribution is the model's job now. Guessing it here is what filed a
    beginner's Google Ads question as a Google campaign."""
    def searcher(query, anchors):
        return [Post("Our Q3 campaign flopped", "marketing", "a", "u1", "d", 1),
                Post("Apple new campaign is clever", "marketing", "b", "u2", "d", 2)]

    found = discover(searcher=searcher)

    assert len(found) == 2
    assert all(m.brand == "" for m in found)


def test_the_same_post_found_by_two_angles_counts_once():
    """"Apple campaign" and "Apple ad" return overlapping results, and one
    post counted twice would inflate both threads and heat."""
    def searcher(query, anchors):
        return [Post("Apple campaign is clever", "marketing", "a", "u1", "d", 1)]

    found = discover(searcher=searcher)

    assert len(found) == 1


def test_x_is_absent_rather_than_empty_when_nobody_has_paid_for_it():
    """X has no free read path. An absent source must never be reported as a
    source that was read and found nothing."""
    import os

    saved = os.environ.pop("X_BEARER_TOKEN", None)
    try:
        assert XAPI.from_env() is None
    finally:
        if saved is not None:
            os.environ["X_BEARER_TOKEN"] = saved


def test_x_failing_does_not_take_the_radar_down():
    def dead(request, timeout=None):
        raise OSError("no network")

    assert XAPI("token", opener=dead).mentions("Zomato") == []


def test_every_row_records_which_sources_it_was_built_from():
    """A ranking built from Reddit alone must say so rather than implying it
    read the whole internet."""
    mentions = [_m("A", "A one", "marketing"),
                _m("A", "A two", "x", source="x", score=5, comments=1)]
    labels = {1: ("A", "A camp"), 2: ("A", "A camp")}

    ranked, _ = rank(mentions, labels)

    assert ranked[0].sources == ("reddit", "x")


# --- publishing --------------------------------------------------------------

def test_the_radar_writes_its_evidence_and_its_refusals():
    log = EventLog(":memory:")
    mentions = [_m("CRED", "CRED rebrand a", "marketing"),
                _m("CRED", "CRED rebrand b", "PPC"),
                _m("Nykaa", "Nykaa ad", "marketing")]
    ranked, refused = rank(mentions, {1: ("CRED", "CRED rebrand"), 2: ("CRED", "CRED rebrand"),
                                      3: ("Nykaa", "Nykaa festive")})

    publish(log, ranked, refused, "corr")

    events = log.read_all()
    ranked_rows = [e for e in events if e.type == "CAMPAIGN_RANKED"]
    refusals = [e for e in events if e.type == "PRIVACY_REFUSED"]

    assert len(ranked_rows) == 1 and len(refusals) == 1
    row = ranked_rows[0].payload
    assert row["scope"] == "brand_radar"
    assert row["campaign"] == "CRED rebrand"
    assert row["heat"] == 4 and row["threads"] == 2 and row["spread"] == 2
    assert len(row["evidence"]) == 2
    assert row["evidence"][0]["url"]
    assert refusals[0].payload["scope"] == "brand_radar"


def test_the_radar_never_writes_into_the_procurement_board():
    """Two boards, one log. A reader must be able to tell which is which, or
    an outside mention has been laundered into a settled figure."""
    log = EventLog(":memory:")
    ranked, refused = rank(
        [_m("A", "A one", "marketing"), _m("A", "A two", "PPC")],
        {1: ("A", "A camp"), 2: ("A", "A camp")})

    publish(log, ranked, refused, "corr")

    for event in log.read_all():
        assert event.payload.get("scope") == "brand_radar"
        assert "value_paise" not in event.payload
        assert "merchants" not in event.payload


# --- two boards, one event type ----------------------------------------------

def test_radar_rows_never_reach_the_procurement_board():
    """Both write CAMPAIGN_RANKED. A reader that filters on the type alone
    would rank a rival's rebrand beside a settled category, and would put
    outside chatter into the playbook that gets auctioned."""
    from exchange.house.campaigns import is_board_row

    log = EventLog(":memory:")
    ranked, refused = rank(
        [_m("CRED", "CRED rebrand a", "marketing"),
         _m("CRED", "CRED rebrand b", "PPC")],
        {1: ("CRED", "CRED rebrand"), 2: ("CRED", "CRED rebrand")})
    publish(log, ranked, refused, "corr")

    rows = [e for e in log.read_all() if e.type == "CAMPAIGN_RANKED"]
    assert rows and not any(is_board_row(e) for e in rows)


def test_a_row_written_before_the_radar_existed_is_still_the_board():
    """Every already-published log, including the demo's, has rows with no
    scope at all. Absence must mean procurement or those boards vanish."""
    from exchange.house.campaigns import is_board_row

    class Old:
        payload = {"rank": 1, "campaign": "Cold Brew", "movement": 1.5}

    assert is_board_row(Old())


def test_socialcrawl_asks_x_once_not_once_per_brand():
    """ai-search costs five credits and a free account starts with 100. One
    sweep must not be able to spend them all."""
    calls = []

    class Crawl:
        credits_remaining = 95

        def x_search(self, query):
            calls.append(query)
            return type("R", (), {"error": "", "items": [
                {"text": "Everyone is talking about the Instagram rebrand",
                 "url": "https://x.com/1", "when": "2026-08-30",
                 "score": 900, "comments": 40}]})()

    found = discover(brands=("Apple", "Meta", "Nike", "CRED"),
                     searcher=lambda q, a: [], crawl=Crawl())

    assert len(calls) == 1
    assert len(found) == 1 and found[0].source == "x"
    assert found[0].score == 900


def test_socialcrawl_failing_leaves_the_reddit_side_standing():
    class Broken:
        def x_search(self, query):
            return type("R", (), {"error": "insufficient credits", "items": []})()

    found = discover(brands=("Apple",),
                     searcher=lambda q, a: [
                         Post("Apple ad is everywhere", "marketing", "a",
                              "u1", "d", 1)],
                     crawl=Broken())

    assert [m.source for m in found] == ["reddit"]
