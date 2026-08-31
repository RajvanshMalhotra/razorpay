"""Reading what real businesses say, without a Reddit account.

The network is faked here — a test that depends on Reddit being reachable is
a test that fails on a train. What is pinned is the judgement: which
communities count, which posts count, and the difference between finding
nothing and never getting to look.
"""
import sys

import pytest

from exchange.house import social
from exchange.house.social import (
    Community,
    _dedupe,
    Fetch,
    Post,
    find_communities,
    relevant,
    research_topic,
    search_posts,
)

ATOM = "http://www.w3.org/2005/Atom"


def _feed(entries) -> bytes:
    body = "".join(
        f'<entry><title>{t}</title>'
        f'<link href="https://www.reddit.com{href}"/>'
        f'<author><name>/u/someone</name></author>'
        f'<updated>2026-05-01T00:00:00+00:00</updated>'
        f'<category label="{label}"/></entry>'
        for t, href, label in entries)
    return f'<feed xmlns="{ATOM}">{body}</feed>'.encode()


def _opener(body=None, error=None):
    class R:
        @staticmethod
        def read():
            return body

    def open_(request, timeout=None):
        if error:
            raise error
        return R()
    return open_


@pytest.fixture(autouse=True)
def _no_cache_no_sleep(tmp_path, monkeypatch):
    monkeypatch.setattr(social, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(social, "MIN_INTERVAL", 0)
    monkeypatch.setattr(social, "BACKOFF", ())
    monkeypatch.setattr(social.time, "sleep", lambda _s: None)


# --- which communities count -------------------------------------------------

def test_communities_come_from_reddit_not_from_a_guess():
    """A hand-written list of subreddits reflects what the author already
    knows. Asking found r/IndiaCoffee, which no such list would contain."""
    feed = _feed([("IndiaCoffee", "/r/IndiaCoffee/", "IndiaCoffee"),
                  ("coffeeindia", "/r/coffeeindia/", "coffeeindia")])

    got = find_communities("coffee", opener=_opener(feed))

    assert [c.name for c in got.items] == ["IndiaCoffee", "coffeeindia"]


def test_an_unrelated_community_is_dropped():
    """Reddit returned a K-pop subreddit for the query "packaging"."""
    feed = _feed([("Packaging", "/r/Packaging/", "Packaging"),
                  ("Reddit K-Pop", "/r/kpop/", "kpop")])

    got = find_communities("packaging", opener=_opener(feed))

    assert [c.name for c in got.items] == ["Packaging"]


# --- which posts count -------------------------------------------------------

def test_a_post_that_does_not_mention_the_topic_is_dropped():
    """Reddit ranks by engagement before it ranks by match, so a popular post
    about anything outranks a quiet post about this. Searching "cold brew
    coffee" really did return "I have started to hate my country"."""
    posts = [Post("I have started to hate my country", "india", "u", "", "", 1),
             Post("Best cold brew concentrate in Bangalore", "india", "u", "", "", 2)]

    kept = relevant(posts, "cold brew concentrate")

    assert [p.rank for p in kept] == [2]


def test_common_words_alone_cannot_make_a_post_relevant():
    """Otherwise every post mentioning "business" or "india" matches every
    query, and the gate stops working."""
    posts = [Post("How to start a business in India", "india", "u", "", "", 1)]

    assert relevant(posts, "india business packaging") == []


def test_the_subreddit_prefix_is_not_doubled():
    feed = _feed([("A post about packaging", "/r/Packaging/x", "r/Packaging")])

    got = search_posts("packaging", [Community("Packaging", "Packaging")],
                       opener=_opener(feed))

    assert got.items[0].subreddit == "Packaging"


# --- the distinction that matters --------------------------------------------

def test_being_refused_is_not_the_same_as_finding_nothing():
    """THE ONE THAT MATTERS. An early version reported "0 posts" for a request
    Reddit had blocked, which reads exactly like a topic nobody discusses.
    Only one of those two is a finding."""
    import urllib.error

    blocked = urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None)

    out = research_topic("cold brew", opener=_opener(error=blocked))

    assert out["blocked"] is True
    assert "refused" in out["discussion"]
    assert "not the same as" in out["discussion"]


def test_a_genuinely_quiet_topic_says_so():
    out = research_topic("cold brew", opener=_opener(_feed([])))

    assert out["blocked"] is False
    assert "Nothing substantive" in out["discussion"]


def test_a_network_failure_is_never_raised_at_the_caller():
    """One throttled query must not lose the passes that already succeeded."""
    out = research_topic("anything", opener=_opener(error=OSError("down")))

    assert out["posts"] == []
    assert out["blocked"] is False


# --- the official API path ---------------------------------------------------

class FakeAPI:
    def research(self, topic):
        return {"communities": [{"name": "IndiaCoffee", "title": "India Coffee",
                                 "subscribers": 12000}],
                "posts": [{"title": "cold brew supplier wanted",
                           "subreddit": "IndiaCoffee", "author": "u",
                           "url": "https://reddit.com/x", "when": "2026-05-01",
                           "rank": 1, "score": 340, "comments": 22}]}


def test_the_api_path_is_used_when_credentials_exist():
    out = research_topic("cold brew", api=FakeAPI())

    assert out["source"] == "api"
    assert out["posts"][0]["score"] == 340


def test_credentials_absent_means_no_api_client_and_no_crash(monkeypatch):
    """The API is an upgrade, never a requirement. Without a registered app
    the module still works on public RSS."""
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)

    assert social.RedditAPI.from_env() is None


def test_this_module_never_writes_to_reddit():
    """THE LOAD-BEARING TEST. Outbound social was cut from this project on
    purpose: posting, voting or messaging violates the platform's terms and
    reads as a spambot. Reading public pages does not.

    Matched on real call sites rather than substrings — an earlier version of
    this test flagged the `Post(` dataclass constructor and would have kept
    doing so forever."""
    import inspect
    import re as _re

    source = inspect.getsource(social)
    writes = _re.findall(
        r"\.\s*(submit|reply|upvote|downvote|save|delete|edit|message|"
        r"subscribe|vote)\s*\(", source)

    assert writes == [], f"write call found: {writes}"
    assert "read_only = True" in source, "the client is pinned read-only"


def test_the_camel_case_in_a_subreddit_name_is_split():
    """r/IndiaCoffee lowercased in one piece is "indiacoffee", which matches
    nothing — so the gate threw away exactly the communities worth having."""
    assert "coffee" in social._terms("IndiaCoffee")
    # "india" and "business" are stopwords — too common to prove anything —
    # so what survives is the word that actually names the niche.
    assert social._terms("MicroBusinessIndia") == {"micro"}


def test_a_run_together_community_name_still_matches():
    """r/coffeeindia has no case boundary to split on, so term matching alone
    dropped it from a search for coffee — the exact community worth having."""
    assert social._names_topic("coffeeindia", "", {"coffee"})
    assert not social._names_topic("kpop", "Reddit K-Pop", {"packaging"})


# --- the search that was not searching ---------------------------------------

def test_the_search_url_actually_restricts_and_ranks_by_match():
    """`restrict_sr=on` is what Reddit's own web UI emits, and on the RSS
    endpoint it silently drops the query and returns the subreddit's top
    posts. Nothing errors and nothing is empty, so the only thing that
    catches it is asserting the parameter."""
    seen = {}

    class Response:
        def read(self):
            return b"<feed xmlns='http://www.w3.org/2005/Atom'></feed>"

    def capture(request, timeout=None):
        seen["url"] = request.full_url
        return Response()

    search_posts("packaging", [Community("smallbusiness", "s")],
                 opener=capture)

    assert "restrict_sr=1" in seen["url"]
    assert "restrict_sr=on" not in seen["url"]
    # Ranking by engagement returns the year's most popular posts regardless
    # of what was asked for, which is how the above went unnoticed.
    assert "sort=relevance" in seen["url"]
    assert "sort=top" not in seen["url"]


def test_a_crossposted_story_counts_once():
    """The same write-up in r/smallbusiness and r/EntrepreneurRideAlong is one
    business's opinion. Two copies in a four-thread summary reads as two."""
    same = "After 22 years chasing clients, I am rebuilding"
    posts = [
        Post(same, "smallbusiness", "a", "https://x/1", "2026-08-01", 1, "trade"),
        Post(same, "EntrepreneurRideAlong", "a", "https://x/2", "2026-08-01", 2,
             "trade"),
        Post("A different thread", "smallbusiness", "b", "https://x/3",
             "2026-08-02", 3, "trade"),
    ]

    kept = _dedupe(posts)

    assert [p.title for p in kept] == [same, "A different thread"]
    # The operator copy is kept, not the last one seen.
    assert kept[0].subreddit == "smallbusiness"


def test_a_post_says_which_search_found_it():
    """Operator talk and customer talk are both worth reading and must never
    be presented as the same thing."""
    class Response:
        def read(self):
            return b"<feed xmlns='http://www.w3.org/2005/Atom'></feed>"

    got = search_posts("packaging", [Community("smallbusiness", "s")],
                       opener=lambda r, timeout=None: Response(), kind="trade")

    assert got.items == []
    # And the default stays "category", so the discovered-community path is
    # unchanged by the addition of the trade one.
    assert Post("t", "s", "a", "u", "w", 1).kind == "category"
