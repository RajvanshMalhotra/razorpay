"""One key that buys the sources we cannot reach on our own.

WHY THIS EXISTS. The radar reads Reddit over public RSS, which needs no
credentials and pays for that with two real costs: it throttles hard, and it
cannot report a score or a comment count, so `engagement` is always zero and
can never break a tie. X is worse — its free tier allows one account every
fifteen minutes, which is about six hours for one sweep of the seed list.

SocialCrawl fronts both behind a single key: `/v1/reddit/search` returns
scores and comment counts for one credit, and `/v1/twitter/ai-search` answers
a natural-language question over X for five. A new account starts with 100
free credits, so a demo run costs nothing and the ladder is honest:

    no keys            Reddit RSS. Throttled, no engagement figures.
    REDDIT_CLIENT_*    Reddit's own API. Scores and comments, no throttling.
    SOCIALCRAWL_API_KEY  the above plus X, without an X contract.
    X_BEARER_TOKEN     X directly, for anyone who has paid for it.

Every layer is optional and every result records which sources were actually
read, so a board built from Reddit alone says so.

WHAT IS KNOWN EXACTLY AND WHAT IS NOT. The envelope is documented and is
relied on precisely: `data.items` for a list, `data.dropped` as an integrity
counter, `credits_used`, `cached`. The per-item field names are a canonical
archetype the public docs do not spell out, so items are read through a small
map of candidate paths and an item that maps to nothing is COUNTED rather
than skipped quietly. A source that silently returns nothing is the failure
this whole module was written to stop repeating.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass

BASE = "https://www.socialcrawl.dev"

# Candidate paths for each field we need, tried in order; the first that
# resolves to a non-empty value wins.
#
# EVERY ITEM IS WRAPPED. A search result is not the post — it is
# `{"post": {...}, "computed": {...}}`, and the post's own subreddit lives one
# level deeper again in `post.ext`. The first version of this map read the
# unwrapped paths and mapped 0 of 7 real items. It did not fail: it returned
# an empty list, spent a credit, and would have reported "nobody is
# discussing this". That is why `unmapped` is counted rather than skipped, and
# why the unwrapped paths are kept below as fallbacks rather than deleted —
# other archetypes on this API are not wrapped the same way.
FIELDS = {
    "text": (("post", "content", "text"), ("post", "ext", "title"),
             ("content", "text"), ("title",), ("text",)),
    "url": (("post", "url"), ("url",), ("permalink",)),
    "author": (("post", "author", "username"), ("author", "username"),
               ("author", "handle"), ("author", "name")),
    "when": (("post", "published_at"), ("published_at",), ("created_at",)),
    "score": (("post", "engagement", "likes"), ("engagement", "likes"),
              ("engagement", "score"), ("score",)),
    "comments": (("post", "engagement", "comments"), ("engagement", "comments"),
                 ("comments",), ("num_comments",)),
    "community": (("post", "ext", "subreddit"), ("subreddit",),
                  ("community",), ("source",)),
}


@dataclass
class Reply:
    """What came back, plus what it cost and what it lost."""
    items: list
    credits_used: int = 0
    credits_remaining: int | None = None
    cached: bool = False
    dropped: int = 0        # discarded upstream, reported by the API
    unmapped: int = 0       # returned to us but matching no known field
    error: str = ""

    def __bool__(self):
        return bool(self.items)


def _dig(item, path):
    node = item
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


# The API returns counts as STRINGS — '401', not 401. Ranking adds them, so
# left alone `engagement` either raises or silently concatenates two numbers
# into a much larger one and reorders the board.
NUMERIC = ("score", "comments")


def _read(item) -> dict:
    out = {}
    for name, paths in FIELDS.items():
        for path in paths:
            value = _dig(item, path)
            if value in (None, "", []):
                continue
            if name in NUMERIC:
                try:
                    value = int(float(value))
                except (TypeError, ValueError):
                    continue
            out[name] = value
            break
    return out


class SocialCrawl:
    """Read-only. Never posts, never messages anyone, never authenticates as
    a user — the same line drawn everywhere else in this project."""

    def __init__(self, key: str, opener=None, base: str = BASE) -> None:
        self._key = key
        self._opener = opener or urllib.request.urlopen
        self._base = base
        self.credits_remaining: int | None = None

    @classmethod
    def from_env(cls, opener=None):
        key = os.environ.get("SOCIALCRAWL_API_KEY")
        return cls(key, opener=opener) if key else None

    def get(self, path: str, **params) -> Reply:
        url = f"{self._base}/v1/{path.lstrip('/')}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(
            url, headers={"x-api-key": self._key, "Accept": "application/json"})
        try:
            body = json.loads(self._opener(request, timeout=30).read())
        except Exception as exc:                       # noqa: BLE001
            # One source failing is not the radar failing, but it must be
            # distinguishable from a source that answered with nothing.
            return Reply(items=[], error=f"{type(exc).__name__}: {exc}")

        if not body.get("success", False):
            detail = (body.get("error") or {})
            return Reply(items=[], error=str(detail.get("message") or detail
                                             or "request refused"))

        data = body.get("data") or {}
        raw = data.get("items", []) if isinstance(data, dict) else []
        read, unmapped = [], 0
        for item in raw:
            mapped = _read(item)
            if not mapped.get("text"):
                unmapped += 1
                continue
            read.append(mapped)

        self.credits_remaining = body.get("credits_remaining")
        return Reply(
            items=read,
            credits_used=body.get("credits_used", 0),
            credits_remaining=body.get("credits_remaining"),
            cached=bool(body.get("cached")),
            # Inside `data`, not at the envelope root. Reading it from the
            # root yields None, which a lenient client misreads as zero.
            dropped=int(data.get("dropped") or 0) if isinstance(data, dict) else 0,
            unmapped=unmapped,
        )

    # --- the two calls the radar actually makes ------------------------------

    def reddit_search(self, query: str, sort: str = "relevance",
                      timeframe: str = "month") -> Reply:
        """1 credit. Scores and comment counts, which RSS cannot give.

        There is no `limit` parameter — passing one is a 400, which is how
        the first live call failed. The optional set is sort, timeframe,
        after and trim; a month matches the window the RSS path uses, so
        both paths answer the same question about the same period.
        """
        return self.get("reddit/search", query=query, sort=sort,
                        timeframe=timeframe)

    def subreddit_search(self, subreddit: str, query: str,
                         sort: str = "relevance",
                         timeframe: str = "month") -> Reply:
        """1 credit. The same shape as the RSS path, without the throttling."""
        return self.get("reddit/subreddit/search", subreddit=subreddit,
                        query=query, sort=sort, timeframe=timeframe)

    def x_search(self, query: str) -> Reply:
        """5 credits. Natural-language search over X with source citations.

        There is no plain keyword search over tweets on this API — the listed
        X endpoints are profile, user timeline, single tweet, community
        timeline and this. So the radar asks a question rather than matching a
        term, which is why the query reads like a sentence.
        """
        return self.get("twitter/ai-search", query=query)
