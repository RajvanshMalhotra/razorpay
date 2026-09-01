"""The one-key adapter: what it must never do quietly."""
import io
import json

from exchange.house.socialcrawl import BASE, SocialCrawl


def _opener(payload, capture=None, status=200):
    def call(request, timeout=None):
        if capture is not None:
            capture["url"] = request.full_url
            capture["headers"] = dict(request.headers)
        return io.BytesIO(json.dumps(payload).encode())
    return call


def _envelope(items, **extra):
    body = {"success": True, "platform": "reddit",
            "endpoint": "/v1/reddit/search",
            "data": {"items": items, "total": len(items), "dropped": 0},
            "credits_used": 1, "credits_remaining": 99, "cached": False}
    body["data"].update(extra.pop("data", {}))
    body.update(extra)
    return body


POST = {"content": {"text": "Instagram rebrand its wordmark"},
        "url": "https://reddit.com/r/marketing/1",
        "author": {"handle": "someone"},
        "published_at": "2026-08-30",
        "engagement": {"likes": 412, "comments": 87},
        "subreddit": "marketing"}


def test_it_reads_the_engagement_figures_rss_cannot_give():
    """The whole reason to spend a credit. Without these, `engagement` is
    always zero and can never break a tie."""
    got = SocialCrawl("k", opener=_opener(_envelope([POST]))).reddit_search("x")

    assert len(got.items) == 1
    assert got.items[0]["score"] == 412
    assert got.items[0]["comments"] == 87
    assert got.items[0]["community"] == "marketing"
    assert got.items[0]["text"].startswith("Instagram")


def test_an_item_that_maps_to_nothing_is_counted_not_skipped():
    """A source that silently returns nothing is the failure this module was
    written to stop repeating. An unreadable item must be visible."""
    got = SocialCrawl("k", opener=_opener(
        _envelope([POST, {"unexpected": "shape"}]))).reddit_search("x")

    assert len(got.items) == 1
    assert got.unmapped == 1


def test_dropped_is_read_from_inside_data_not_the_envelope_root():
    """It lives in `data`. Read from the root it is None, which a lenient
    client misreads as zero — a lossy page reported as a complete one."""
    body = _envelope([POST])
    body["data"]["dropped"] = 3
    body["dropped"] = 0          # the root field that has never existed

    got = SocialCrawl("k", opener=_opener(body)).reddit_search("x")

    assert got.dropped == 3


def test_a_refused_request_is_an_error_not_an_empty_result():
    body = {"success": False, "error": {"message": "insufficient credits"}}

    got = SocialCrawl("k", opener=_opener(body)).reddit_search("x")

    assert got.items == []
    assert "insufficient credits" in got.error
    assert not got


def test_the_network_failing_says_so_rather_than_reporting_silence():
    def dead(request, timeout=None):
        raise OSError("no route to host")

    got = SocialCrawl("k", opener=dead).reddit_search("x")

    assert got.items == [] and "OSError" in got.error


def test_an_empty_result_is_not_an_error():
    """A valid zero-match is 200 with items: [] and the credit refunded."""
    got = SocialCrawl("k", opener=_opener(_envelope([]))).reddit_search("x")

    assert got.items == [] and got.error == ""


def test_it_authenticates_with_the_documented_header_and_path():
    seen = {}
    SocialCrawl("sc_abc", opener=_opener(_envelope([]), seen)).subreddit_search(
        "marketing", "rebrand")

    assert seen["url"].startswith(f"{BASE}/v1/reddit/subreddit/search?")
    assert "subreddit=marketing" in seen["url"]
    # urllib title-cases header names.
    assert seen["headers"].get("X-api-key") == "sc_abc"


def test_x_search_goes_to_the_ai_search_endpoint():
    """There is no plain keyword search over tweets on this API."""
    seen = {}
    SocialCrawl("k", opener=_opener(_envelope([]), seen)).x_search(
        "which brand campaigns are people reacting to")

    assert "/v1/twitter/ai-search?" in seen["url"]


def test_credits_spent_are_reported_so_a_run_cannot_quietly_drain_the_balance():
    client = SocialCrawl("k", opener=_opener(_envelope([POST])))
    got = client.reddit_search("x")

    assert got.credits_used == 1
    assert got.credits_remaining == 99
    assert client.credits_remaining == 99


def test_absent_without_a_key():
    import os
    saved = os.environ.pop("SOCIALCRAWL_API_KEY", None)
    try:
        assert SocialCrawl.from_env() is None
    finally:
        if saved is not None:
            os.environ["SOCIALCRAWL_API_KEY"] = saved
