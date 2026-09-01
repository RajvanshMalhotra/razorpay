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
free credentials on the API path.

X (Twitter) is behind a bearer token. An earlier version of this comment said
there was no free path at all, which is wrong and worth correcting rather than
quietly deleting: there IS a free tier, and firecrawl/gemini-trendfinder
states its shape plainly — "the X API free plan is rate limited to only
monitor 1 X account every 15 min". At the fan-out this radar wants that is
roughly six hours for one sweep of the seed list, so the free tier exists and
is unusable here, which is a different claim and the true one. The scraping
mirrors that used to avoid the API are gone.

So X stays absent unless X_BEARER_TOKEN is set, and every result records which
sources were actually read. A ranking built from Reddit alone must say so
rather than implying it read the whole internet.
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
from exchange.house.socialcrawl import parse_answer
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
             communities=MARKETING_COMMUNITIES, queries=QUERIES,
             crawl=None) -> list[Mention]:
    """Collect posts that might discuss a brand's campaign. Reads; never ranks.

    ATTRIBUTION IS NOT DONE HERE, and the reason is a measurement. Matching
    brand names in titles put "How difficult it is learn meta and google adds
    campaign?" under both Meta's and Google's campaigns - a beginner asking
    about ad platforms, recorded as evidence that two companies were running
    campaigns people had noticed. On a board that gets auctioned, that is an
    invented finding with a price on it.

    So posts come back unattributed, and `label` decides both which company a
    post is about and whether it is about a campaign at all. The model does
    extraction, which it is good at. It still never decides an order.
    """
    searcher = searcher or _reddit_search
    anchors = [Community(name, name) for name in communities]

    found: list[Mention] = []
    for query in queries:
        for post in searcher(query, anchors):
            found.append(Mention(
                brand="", title=post.title, url=post.url,
                community=post.subreddit, source="reddit",
                when=post.when, score=None, comments=None))

    # X, when somebody has paid for it, one way or the other.
    if x is not None:
        for brand in brands:
            found.extend(x.mentions(brand))
    if crawl is not None:
        found.extend(_from_crawl(crawl, brands))
        if x is None:          # not both; the direct token wins if present
            from_x = _x_from_crawl(crawl)
            found.extend(from_x)

            # X FINDS THEM, REDDIT MEASURES THEM. On its own X contributes one
            # citation per company, which is one thread in one community and
            # is refused by the floor — correctly, because one tweet is not a
            # campaign. Its worth is that it names campaigns nothing in our
            # seed list would have: Cracker Barrel and Kuda came back from X
            # and appear in no marketing subreddit we search. So whatever X
            # names is then searched on Reddit, where the threads and the
            # engagement figures are, and only then can it earn a place.
            discovered = {m.brand for m in from_x if m.brand} - set(brands)
            if discovered:
                found.extend(_from_crawl(crawl, sorted(discovered)))
    return _unique(found)


def _from_crawl(crawl, brands) -> list[Mention]:
    """One search per brand, which the throttled path could not afford.

    THE QUERY DECIDES THE ANSWER, again. Broad queries — "ad campaign",
    "brand rebrand" — return practitioners discussing their own work: "Im
    rebranding, help lol". Naming the company returns the reaction to what
    that company actually did: "Brands poke fun at Instagram's new logo",
    401 upvotes, alongside the same story in r/logodesign and r/graphic_design.
    That is a campaign with measurable spread, which is what the ranking is
    made of.

    One credit each, cached for two minutes, and a cache hit is free — so a
    sweep of the seed list costs about what one X question costs.
    """
    out: list[Mention] = []
    for brand in brands:
        reply = crawl.reddit_search(f"{brand} campaign rebrand ad")
        if reply.error:
            continue
        out.extend(Mention(
            brand="", title=item.get("text", "")[:250],
            url=item.get("url", ""), community=item.get("community", "reddit"),
            source="reddit", when=(item.get("when") or "")[:10],
            score=item.get("score"), comments=item.get("comments"))
            for item in reply.items)
    return out


def _x_from_crawl(crawl) -> list[Mention]:
    """One natural-language question over X for the whole field.

    `twitter/ai-search` is the only way into X on this API — there is no
    keyword search over tweets — and it answers in prose with citations
    rather than returning rows. So the answer is parsed back into one mention
    per company, each carrying the tweet it cited.

    WHAT X IS FOR HERE, AND WHAT IT IS NOT. It reports no upvote or reply
    count, so these mentions carry no engagement and every one of them sits in
    the single community "x". They widen the field — X surfaced Cracker Barrel
    and Kuda, which no marketing subreddit had mentioned — and they let a
    campaign be seen at all. Depth still has to come from Reddit, where the
    threads and the numbers are.
    """
    reply = crawl.x_search(
        "Which brand marketing campaigns, ads or rebrands are people "
        "reacting to on X right now? Name the company for each.")
    if reply.error or not reply.answer:
        return []
    return [Mention(brand=row["company"], title=row["text"], url=row["url"],
                    community="x", source="x", when="",
                    score=None, comments=None)
            for row in parse_answer(reply.answer)]


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


LABEL_PROMPT = """You identify marketing campaigns that companies ran.

You are given numbered post titles from marketing communities. MOST are
practitioners discussing their own advertising work - their budgets, their ad
groups, their clients. Those are not what you are looking for.

You want only posts reacting to a campaign, ad or rebrand that a named company
actually ran.

Reply with one line per post, exactly `<number>: <company> | <campaign name>`.
The campaign name must identify the campaign AND carry the company, exactly
like "Instagram wordmark rebrand" or "Swiggy birthday full-page ad". Never
"Instagram" alone, and never a bare "Rebrand" or "Wordmark redesign" - a name
that could belong to any company describes no campaign.

GROUP BOLDLY. Posts about the same campaign MUST get the byte-identical
company and campaign name. Five posts reacting to one new logo - in different
subreddits, worded differently, some praising and some mocking - are ONE
campaign and must share one name. Splitting them into five near-identical
names is the most common way to get this wrong, and it destroys the answer:
the whole signal is that one campaign was discussed in many places. Before you
write a name, check whether you have already used one that means the same
thing, and reuse that one exactly.

Reply `<number>: none` for everything else, and be strict. A post that merely
mentions a company's ad platform - running Google Ads, using Meta Ads Manager
- is `none`, because there the company is the tool and not the advertiser.
When in doubt, `none`."""


def label(mentions, provider):
    """Attribute and group.

    Returns ({index: (company, campaign)}, fallbacks, answered).

    THE TWO NUMBERS MEAN DIFFERENT THINGS AND ONLY ONE IS A FAULT.

    `fallbacks` counts posts the model looked at and said were not about a
    company's campaign. On this source that is most of them — 88 of 100 in
    the first live run — and it is the correct answer, not a failure. These
    communities are full of practitioners discussing their own work.

    `answered` counts posts the model addressed at all, either by naming a
    campaign or by explicitly saying `none`. THAT is the health check: a
    model returning nothing also produces zero rows, and zero rows looks
    exactly like a quiet week. Refusing on a high fallback rate — which the
    first version did — refuses precisely when the model is being correctly
    strict.
    """
    if not mentions:
        return {}, 0, 0

    listing = "\n".join(f"{i}. [{m.community}] {m.title}"
                        for i, m in enumerate(mentions, start=1))
    reply = provider.complete(
        [LLMMessage("user", listing)],
        system=LABEL_PROMPT,
        max_tokens=12000,
        reasoning_effort="low",
    )

    named: dict[int, tuple] = {}
    answered: set[int] = set()
    for line in reply.text.splitlines():
        match = re.match(r"\s*(\d+)\s*[:.)-]\s*(.+)", line)
        if not match:
            continue
        index, rest = int(match.group(1)), match.group(2).strip()
        if not (1 <= index <= len(mentions)):
            continue
        answered.add(index)
        if rest.lower() == "none":
            continue
        if "|" not in rest:
            # Named a campaign but no company, so there is nothing to check it
            # against. Dropped rather than half-recorded.
            continue
        brand, campaign = (part.strip() for part in rest.split("|", 1))
        if brand and campaign:
            named[index] = (brand, campaign)

    return named, len(mentions) - len(named), len(answered)


def rank(mentions, labels, floor: int = MIN_THREADS):
    """Order the campaigns. Pure arithmetic, and deliberately unable to be
    anything else — no provider, no network, no reading of any post's text.

    Returns (ranked, refused).
    """
    grouped: dict[str, Campaign] = {}
    for i, mention in enumerate(mentions, start=1):
        attributed = labels.get(i)
        if not attributed:
            continue
        brand, name = attributed
        # KEYED ON BOTH, and this is a correctness fix rather than a tidy-up.
        # Asked to name the campaign and not the company, the model sometimes
        # returns a bare "Rebrand" - and keyed on the name alone that merges
        # Instagram's rebrand with Cracker Barrel's into one row with twice
        # the heat, which is a campaign that does not exist.
        row = grouped.setdefault((brand, name),
                                 Campaign(name=name, brand=brand))
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
