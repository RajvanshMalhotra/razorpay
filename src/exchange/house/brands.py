"""Rank the marketing campaigns companies are actually running.

WHAT THIS IS AND WHY IT IS A SEPARATE AGENT. The campaign board in
`campaigns.py` ranks what Razorpay's own merchants are BUYING, computed from
the settled trades in the log. Every figure there is recomputable by anyone
holding the same events, and that is the property its credibility rests on.

This ranks something the log cannot see: which marketing campaigns — from
startups and from big technology companies — people are actually talking
about right now. No trade in our market says anything about whether a rival's
Diwali campaign is landing, so this reads the outside world instead.

THE TWO ARE NEVER MIXED, and neither can move a number in the other. That
separation is not tidiness. A board that let an outside mention change an
internal figure would have laundered a tweet into a settled amount.

WHERE THE RANKING COMES FROM. Not from a model. `rank` is arithmetic over the
mentions that were collected, and a test asserts it can reach neither the
model nor the network. What a model does here is exactly one job: group many
phrasings of the same campaign under one name. It never decides an order.

    threads   distinct posts discussing the campaign
    spread    distinct communities those posts came from
    heat      threads x spread

Spread is in there because volume alone is gameable and misleading: forty
posts inside one subreddit is a community with a hobby, while eight posts
across five communities is a campaign the market has noticed. A campaign
below the thread floor is refused a place and the refusal is logged, for the
same reason the privacy floor is logged — a floor nobody can see is
indistinguishable from no floor.

TWO SOURCES, ONE OPTIONAL. Reddit needs no credentials on the RSS path and
free credentials on the API path. X (Twitter) has no free read path at all:
nitter instances are dead and the official API requires a paid bearer token.
So X is behind an interface that stays absent unless X_BEARER_TOKEN is set,
and every result records which sources were actually read. A ranking built
from Reddit alone must say so rather than implying it read the whole internet.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from exchange import events as ev
from exchange.house.insights import HOUSE_ACTOR_ID
from exchange.house.social import (
    MARKETING_COMMUNITIES,
    Community,
    _dedupe,
    search_posts,
)
from exchange.llm.base import LLMMessage

# A campaign discussed in a single thread is one person's opinion. Two
# threads is the minimum that can be called a conversation, and the number
# in force is written into every row and every refusal.
MIN_THREADS = int(os.environ.get("BRAND_MIN_THREADS", "2"))

# The brands a post can be ATTRIBUTED to. Discovery does not search for
# these — it searches for campaign talk and then asks which of these a post
# names. A post naming none of them is dropped, because a row whose brand
# was inferred is a row nobody can check. Extend the list to widen the net.
SEED_BRANDS = (
    "Zomato", "Swiggy", "Zepto", "Blinkit", "CRED", "Groww", "Zerodha",
    "Nykaa", "Myntra", "boAt", "Mamaearth", "Lenskart", "Rapido",
    "Apple", "Google", "Meta", "Amazon", "Netflix", "Spotify", "Nike",
    "Duolingo", "Figma", "Notion", "Canva", "OpenAI", "Anthropic",
)

# WHAT IS SEARCHED, AND WHY IT IS NOT ONE QUERY PER BRAND.
#
# The first version searched "<brand> campaign" for every brand on the seed
# list. That is 26 brands x 4 angles = 104 throttled requests, which took
# longer than ten minutes and never finished. It was also the wrong shape:
# it could only ever confirm the list it started with.
#
# These few queries are run instead, and the brand is detected in whatever
# comes back. Four requests, not a hundred — and a campaign by a company
# nobody thought to seed still surfaces, because the search was never about
# the company in the first place.
QUERIES = (
    "campaign",
    "rebrand",
    "ad campaign",
    "brand launch",
)


@dataclass(frozen=True)
class Mention:
    """One post that discusses a campaign. The evidence under a row."""
    brand: str
    title: str
    url: str
    community: str
    source: str           # "reddit" or "x"
    when: str
    score: int | None = None      # None on RSS, which cannot report it
    comments: int | None = None


@dataclass
class Campaign:
    name: str
    brand: str
    mentions: list = field(default_factory=list)

    @property
    def threads(self) -> int:
        return len(self.mentions)

    @property
    def spread(self) -> int:
        return len({m.community for m in self.mentions})

    @property
    def sources(self) -> tuple:
        return tuple(sorted({m.source for m in self.mentions}))

    @property
    def engagement(self) -> int:
        """Upvotes plus replies, where the source reported them.

        RSS cannot report either, so this is 0 for an RSS-only row. It is a
        tiebreak and never the ranking, precisely so that a run with no
        credentials still produces an honest order rather than a flat one.
        """
        return sum((m.score or 0) + (m.comments or 0) for m in self.mentions)

    @property
    def heat(self) -> int:
        return self.threads * self.spread


@dataclass(frozen=True)
class Refusal:
    name: str
    threads: int
    reason: str


def discover(brands=SEED_BRANDS, searcher=None, x=None,
             communities=MARKETING_COMMUNITIES, queries=QUERIES) -> list[Mention]:
    """Collect posts that discuss campaigns. Reads; never ranks.

    Runs a handful of broad campaign queries against the marketing
    communities, then attributes each post to whichever known brand it names.
    A post naming no brand on the list is dropped rather than guessed at: a
    row whose brand was inferred is a row nobody can check.

    `searcher` is injected so this runs from a fixture in tests and from
    Reddit in a run; `x` is the optional paid path and is simply absent when
    nobody has configured a bearer token.
    """
    searcher = searcher or _reddit_search
    anchors = [Community(name, name) for name in communities]
    lookup = {b.lower(): b for b in brands}

    found: list[Mention] = []
    for query in queries:
        for post in searcher(query, anchors):
            named = _brand_in(post.title, lookup)
            if named is None:
                continue
            found.append(Mention(
                brand=named, title=post.title, url=post.url,
                community=post.subreddit, source="reddit",
                when=post.when, score=None, comments=None))

    # X is queried per brand, because its API takes a real query and does not
    # throttle the way Reddit's RSS does. Only for brands already seen on
    # Reddit, so one paid source cannot invent a campaign on its own.
    if x is not None:
        for brand in sorted({m.brand for m in found}):
            found.extend(x.mentions(brand))
    return _unique(found)


def _brand_in(title: str, lookup: dict):
    """Which known brand does this post name, if any?

    Word-boundary matched. Substring matching put every post containing the
    word "meta" — metadata, metaphor — under Meta's campaigns.
    """
    for word in re.findall(r"[A-Za-z][A-Za-z0-9]+", title):
        found = lookup.get(word.lower())
        if found:
            return found
    return None


def _reddit_search(query, anchors):
    return _dedupe(search_posts(query, anchors, limit=12, kind="marketing").items)


def _unique(mentions):
    seen, out = set(), []
    for m in mentions:
        key = (m.source, m.url or m.title.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(m)
    return out


LABEL_PROMPT = """You group posts that discuss the same marketing campaign.

You are given numbered post titles, each about one brand. Reply with one line
per post, exactly `<number>: <campaign name>`.

The campaign name must name the CAMPAIGN, not the company and not the product
category — "Zomato monsoon delivery ads", not "Zomato" and not "food delivery".
Posts about the same campaign must get the exact same name. A post that
discusses the brand but no particular campaign gets the name `none`."""


def label(mentions, provider):
    """Group many phrasings into campaign names. Returns (mapping, fallbacks).

    `fallbacks` counts the posts the model left unnamed. It is returned rather
    than swallowed because the failure mode is silent and plausible: a model
    that returns nothing produces one campaign per post, and a board of
    one-post campaigns looks like a finding instead of a broken call.
    """
    if not mentions:
        return {}, 0

    listing = "\n".join(f"{i}. [{m.brand}] {m.title}"
                        for i, m in enumerate(mentions, start=1))
    reply = provider.complete(
        [LLMMessage("user", listing)],
        system=LABEL_PROMPT,
        max_tokens=12000,
        reasoning_effort="low",
    )

    named: dict[int, str] = {}
    for line in reply.text.splitlines():
        match = re.match(r"\s*(\d+)\s*[:.)-]\s*(.+)", line)
        if not match:
            continue
        index, name = int(match.group(1)), match.group(2).strip()
        if 1 <= index <= len(mentions):
            named[index] = name

    mapping, fallbacks = {}, 0
    for i, mention in enumerate(mentions, start=1):
        name = named.get(i, "")
        if not name or name.lower() == "none":
            fallbacks += 1
            continue
        mapping[i] = name
    return mapping, fallbacks


def rank(mentions, labels, floor: int = MIN_THREADS):
    """Order the campaigns. Pure arithmetic, and deliberately unable to be
    anything else — no provider, no network, no reading of any post's text.

    Returns (ranked, refused).
    """
    grouped: dict[str, Campaign] = {}
    for i, mention in enumerate(mentions, start=1):
        name = labels.get(i)
        if not name:
            continue
        row = grouped.setdefault(name, Campaign(name=name, brand=mention.brand))
        row.mentions.append(mention)

    ranked, refused = [], []
    for row in grouped.values():
        if row.threads < floor:
            refused.append(Refusal(
                row.name, row.threads,
                f"discussed in {row.threads} thread(s), below the floor of "
                f"{floor}"))
            continue
        ranked.append(row)

    # Heat first, then engagement, then spread, then the name — so the order
    # is total and a rerun on the same data cannot shuffle.
    ranked.sort(key=lambda r: (-r.heat, -r.engagement, -r.spread, r.name))
    refused.sort(key=lambda r: r.name)
    return ranked, refused


def publish(log, ranked, refused, correlation_id: str) -> None:
    """Write the radar to the log, ranked rows and refused rows alike."""
    for refusal in refused:
        log.append(HOUSE_ACTOR_ID, ev.PRIVACY_REFUSED, {
            "scope": "brand_radar",
            "campaign": refusal.name,
            "threads": refusal.threads,
            "floor": MIN_THREADS,
            "reason": refusal.reason,
        }, correlation_id=correlation_id)

    for position, row in enumerate(ranked, start=1):
        log.append(HOUSE_ACTOR_ID, ev.CAMPAIGN_RANKED, {
            "audience": "razorpay_internal",
            "scope": "brand_radar",
            "rank": position,
            "campaign": row.name,
            "brand": row.brand,
            "heat": row.heat,
            "threads": row.threads,
            "spread": row.spread,
            "engagement": row.engagement,
            "sources": list(row.sources),
            "floor": MIN_THREADS,
            "evidence": [
                {"title": m.title, "url": m.url, "community": m.community,
                 "source": m.source, "when": m.when,
                 "score": m.score, "comments": m.comments}
                for m in row.mentions[:6]
            ],
        }, correlation_id=correlation_id)


# --- X (Twitter), when somebody has paid for it ------------------------------
#
# There is no free read path. Verified rather than assumed: the public nitter
# instances that used to mirror timelines are gone, and api.x.com refuses
# unauthenticated reads. So this class is the plug: set X_BEARER_TOKEN and it
# appears, leave it unset and every result honestly records that only Reddit
# was read.

class XAPI:
    """Recent-search over X, via a bearer token the operator supplies."""

    ENDPOINT = "https://api.x.com/2/tweets/search/recent"

    def __init__(self, token: str, opener=None) -> None:
        self._token = token
        self._opener = opener

    @classmethod
    def from_env(cls):
        token = os.environ.get("X_BEARER_TOKEN")
        return cls(token) if token else None

    def mentions(self, brand: str, limit: int = 10) -> list[Mention]:
        import json
        import urllib.parse
        import urllib.request

        query = urllib.parse.quote(f'"{brand}" (campaign OR ad OR rebrand) -is:retweet lang:en')
        url = (f"{self.ENDPOINT}?query={query}&max_results={max(10, limit)}"
               f"&tweet.fields=created_at,public_metrics")
        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self._token}"})
        opener = self._opener or urllib.request.urlopen
        try:
            body = json.loads(opener(request, timeout=20).read())
        except Exception:                              # noqa: BLE001
            # One source failing is not the radar failing. The caller records
            # which sources were read, so an absent X is visible downstream
            # rather than silently reported as "nobody on X mentioned it".
            return []

        out = []
        for tweet in body.get("data", []):
            metrics = tweet.get("public_metrics", {})
            out.append(Mention(
                brand=brand,
                title=tweet.get("text", "").replace("\n", " ")[:200],
                url=f"https://x.com/i/web/status/{tweet.get('id', '')}",
                community="x",
                source="x",
                when=(tweet.get("created_at") or "")[:10],
                score=metrics.get("like_count"),
                comments=metrics.get("reply_count"),
            ))
        return out
