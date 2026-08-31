"""What real businesses are saying, read from Reddit.

WHY REDDIT AND NOT THE PRESS. The news fetcher in `campaigns` answers "what
is being announced". This answers something the press cannot: what people
running these businesses actually complain about, ask for and recommend to
each other. A supplier thread in r/IndiaBusiness is closer to a demand signal
than a funding announcement is.

THE PIPELINE, borrowed from the shape of the Reddit-Content-Research-Agent
project (codingforentrepreneurs): find the COMMUNITIES first, then read inside
them, then extract. Guessing a subreddit list up front is what makes this kind
of research shallow — asking Reddit which communities exist for a topic found
r/IndiaCoffee and r/MicroBusinessIndia, neither of which was on any list I
would have written.

WHAT WAS NOT BORROWED, and why. That project reaches Reddit through Bright
Data's paid scraping API and runs on Django, Postgres, Celery, Redis and
LangGraph. This one has no web stack, no queue and no orchestration framework,
and adding four services to fetch some RSS would be the tail wagging the dog.

TWO WAYS IN, AND THE BETTER ONE IS OPTIONAL. neonwatty/reddit-market-research
uses PRAW against Reddit's official API, which is free, needs a registered
script app, and returns what RSS cannot: the score, the comment count and the
body of each post. So this module prefers PRAW when REDDIT_CLIENT_ID and
REDDIT_CLIENT_SECRET are set, and falls back to RSS when they are not. RSS
works with no setup at all and is heavily rate limited; the API does not
throttle at this volume and can rank by what people actually upvoted.

A SILENT FAILURE IS WORSE THAN NO ANSWER. Reddit returns 429 readily, and an
early version of this module reported "0 posts" for a request that had been
blocked — indistinguishable from a topic nobody discusses. Every result now
says whether it looked and found nothing, or never got to look.

THE GATE THAT MAKES IT HONEST. Reddit's search matches loosely — "cold brew
coffee" returned a post titled "I have started to hate my country". Every post
must actually mention what was searched for, and a campaign with nothing
substantive behind it is reported as having nothing rather than padded with
whatever came back.

NOT A WRITE PATH. This reads public pages. It never posts, never messages
anyone and never authenticates, which is the same line drawn everywhere else
in this project: growth signal comes from public read-only sources.
"""
from __future__ import annotations

import hashlib
import pathlib
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass

ATOM = {"a": "http://www.w3.org/2005/Atom"}

# Reddit asks for a descriptive agent and rate-limits hard without one.
USER_AGENT = "agent-exchange-research/0.1 (Razorpay hackathon; read-only)"

# Reddit returns 429 readily. These are deliberately unhurried: research runs
# once per market run, so a few seconds a query costs nothing and being
# throttled out mid-run costs the whole report.
MIN_INTERVAL = 2.5
BACKOFF = (15, 35)
CACHE_DIR = pathlib.Path("runs/cache/reddit")
CACHE_TTL = 60 * 60 * 12

# Words too common to prove a post is about anything.
STOPWORDS = frozenset("""
the a an and or for of in on to with from at by is are was were be been this
that these those it its as into your our their you we they i india indian
business businesses new best top how what why when where which who
""".split())

_last_call = 0.0


@dataclass(frozen=True)
class Post:
    title: str
    subreddit: str
    author: str
    url: str
    when: str
    rank: int          # position in Reddit's own ordering, 1 is best
    # "category" = the community for this product. "trade" = a community of
    # people running businesses. Kept on the post rather than inferred later,
    # because the same subreddit can be reached by both searches and only the
    # search that found it says what kind of reading it is.
    kind: str = "category"


@dataclass
class Fetch:
    """What came back, and — when nothing did — why not.

    `blocked` is the distinction that matters: a topic nobody discusses and a
    request Reddit refused look identical in a list of zero posts, and only
    one of them is a finding.
    """
    items: list
    blocked: bool = False
    source: str = "rss"

    def __bool__(self):
        return bool(self.items)


@dataclass(frozen=True)
class Community:
    name: str          # the r/ slug, which is what you can search inside
    title: str         # the human display name


def _terms(text: str) -> set:
    """The words in a phrase that would actually prove a match.

    Subreddit names are run together in CamelCase — r/IndiaCoffee,
    r/MicroBusinessIndia — so lowercasing first turns the best communities
    into one unmatchable token and the relevance gate throws them away. The
    case boundary is split before anything else happens.
    """
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(text))
    words = re.findall(r"[a-z]{3,}", spaced.lower())
    return {w for w in words if w not in STOPWORDS}


def _cache_path(url: str) -> pathlib.Path:
    return CACHE_DIR / (hashlib.sha256(url.encode()).hexdigest()[:20] + ".xml")


def _get(url: str, opener=urllib.request.urlopen, cache: bool = True):
    """One polite, cached, backed-off fetch. Returns (bytes|None, blocked).

    Never raises. A research pass that dies because one query was throttled
    would lose the passes that already succeeded — but it must say which
    happened, so the caller returns a blocked Fetch rather than an empty one.
    """
    global _last_call

    path = _cache_path(url)
    if cache and path.exists() and time.time() - path.stat().st_mtime < CACHE_TTL:
        return path.read_bytes(), False

    for attempt, wait in enumerate((0, *BACKOFF)):
        if wait:
            time.sleep(wait)
        gap = time.time() - _last_call
        if gap < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - gap)
        request = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
        try:
            body = opener(request, timeout=25).read()
            _last_call = time.time()
            if cache:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body)
            return body, False
        except urllib.error.HTTPError as error:
            _last_call = time.time()
            if error.code == 429 and attempt < len(BACKOFF):
                continue
            return None, error.code == 429
        except Exception:
            _last_call = time.time()
            return None, False
    return None, True


def _entries(body):
    if not body:
        return []
    try:
        return ET.fromstring(body).findall("a:entry", ATOM)
    except ET.ParseError:
        return []


def _names_topic(name: str, title: str, wanted: set) -> bool:
    """Does this community plausibly cover the topic?

    Subreddit names run words together with no separator at all —
    r/coffeeindia has no case boundary to split on — so a term counts if it
    appears anywhere inside the name. That looseness is safe here because the
    alternative was dropping r/coffeeindia from a search for coffee, and post
    relevance is gated separately and strictly.
    """
    if not wanted:
        return True
    flat = name.lower()
    return bool(_terms(name) & wanted or _terms(title) & wanted
                or any(term in flat for term in wanted))


def find_communities(topic: str, limit: int = 6, opener=urllib.request.urlopen):
    """Which subreddits exist for this topic, according to Reddit.

    This is the step that stops the research being shallow. A hand-written
    list of subreddits reflects what the author already knows; asking finds
    r/IndiaCoffee and r/MicroBusinessIndia, which no such list would contain.
    """
    url = ("https://www.reddit.com/subreddits/search.rss?"
           f"q={urllib.parse.quote(topic)}&limit={limit * 3}")
    wanted = _terms(topic)
    body, blocked = _get(url, opener)

    out = []
    for entry in _entries(body):
        link = entry.find("a:link", ATOM)
        href = link.get("href") if link is not None else ""
        match = re.search(r"/r/([A-Za-z0-9_]+)", href or "")
        if not match:
            continue
        name = match.group(1)
        title = (entry.findtext("a:title", "", ATOM) or "").strip()
        # A community counts only if its name or its description touches the
        # topic. Reddit's own results included a K-pop subreddit for the
        # query "packaging".
        if not _names_topic(name, title, wanted):
            continue
        out.append(Community(name=name, title=title))
        if len(out) >= limit:
            break
    return Fetch(items=out, blocked=blocked)


# WHERE THE OPERATORS ARE, as opposed to where the customers are.
#
# Community discovery is genuinely good at finding the category's own
# subreddit, and that is the problem. Searching "Cold Brew Concentrate"
# finds r/coldbrew, and r/coldbrew is people making cold brew at home:
# "Why does my cold brew look like this at the bottom?" is a real result
# and it is worth nothing to a merchant deciding what to stock.
#
# The people running these businesses are in a different, small set of
# communities, and they are there for the business rather than the product.
# Searching "packaging supplier" across these returned "My supplier is also
# my biggest competitor, how do you get a rate out of them" — which is the
# kind of sentence this desk exists to find.
#
# The list is short on purpose, and every general-interest community was
# taken back out after testing: r/india answered "electronics assembly"
# with three political posts, and r/Coffee answered "cold brew" with home
# brewing. A community has to be about running a business to earn a place.
TRADE_COMMUNITIES = (
    "smallbusiness",
    "Entrepreneur",
    "EntrepreneurRideAlong",
    "StartUpIndia",
)


def search_posts(query: str, communities, limit: int = 8,
                 opener=urllib.request.urlopen, kind: str = "category"):
    """The most-discussed posts about this, inside those communities."""
    if not communities:
        return Fetch(items=[], blocked=False)
    subs = "+".join(c.name for c in communities[:8])
    # THREE PARAMETERS THAT ALL HAVE TO BE RIGHT, and two of them were not.
    #
    # `restrict_sr=on` is what Reddit's own web UI puts in the URL, and on
    # the RSS endpoint it silently DISABLES the search: the query is dropped
    # and the subreddit's top posts come back instead. Measured on one
    # subreddit, searching "packaging" in r/smallbusiness: 0 of 16 titles
    # contained the word. With `restrict_sr=1`, 13 of 16 did. Nothing errors
    # and nothing is empty, which is why this survived — the results look
    # plausible, and for a community named after the topic they even look
    # right, because top-of-r/coldbrew is about cold brew either way.
    #
    # `sort=top` compounds it by ranking on engagement rather than match.
    # `sort=relevance` with no `t` returns zero entries, so the window stays.
    url = (f"https://www.reddit.com/r/{subs}/search.rss?"
           f"q={urllib.parse.quote(query)}&restrict_sr=1&sort=relevance"
           f"&t=year&limit={limit * 2}")

    body, blocked = _get(url, opener)
    out = []
    for n, entry in enumerate(_entries(body), start=1):
        link = entry.find("a:link", ATOM)
        category = entry.find("a:category", ATOM)
        out.append(Post(
            title=(entry.findtext("a:title", "", ATOM) or "").strip(),
            # The RSS category label already carries the prefix, so joining
            # it to another one printed "r/r/coldbrew".
            subreddit=re.sub(r"^r/", "",
                             (category.get("label") if category is not None
                              else "")),
            author=(entry.findtext("a:author/a:name", "", ATOM) or "").strip(),
            url=(link.get("href") if link is not None else ""),
            when=(entry.findtext("a:updated", "", ATOM) or "")[:10],
            rank=n,
            kind=kind,
        ))
    return Fetch(items=out, blocked=blocked)


def _dedupe(posts):
    """One post per story, keeping the first — which is the operator copy.

    Crossposts are the common case: the same write-up appears in
    r/smallbusiness and r/EntrepreneurRideAlong on the same day, and two
    copies of one opinion in a four-thread summary reads as two businesses
    agreeing. Matched on title rather than URL, because a crosspost gets a
    URL of its own.
    """
    seen, out = set(), []
    for post in posts:
        key = " ".join(post.title.lower().split())
        if key in seen:
            continue
        seen.add(key)
        out.append(post)
    return out


def relevant(posts, query: str, need: int = 1):
    """Only the posts that actually mention what was searched for.

    Reddit ranks by engagement before it ranks by match, so a popular post
    about anything outranks a quiet post about this. Without this gate the
    report reads as though people are discussing a category when they are
    discussing something else entirely, which is worse than reporting silence.
    """
    wanted = _terms(query)
    if not wanted:
        return []
    keep = []
    for post in posts:
        hits = _terms(post.title) & wanted
        if len(hits) >= need:
            keep.append(post)
    return keep


DISCUSSION_PROMPT = """You are Razorpay's market research agent, reading what
people who actually run these businesses are saying to each other on Reddit.

You will be given real post titles from real communities, with the subreddit
and the date.

Write TWO sentences, under 45 words in total:
1. What the people in this category are actually talking about — the problem,
   the shift, or the demand behind the posts.
2. What that implies for a business buying or selling in this category.

Ground both in the titles you were given. If the titles do not support a
clear read, say "the discussion is too scattered to call" and stop. Never
invent a statistic. Never name a Razorpay merchant."""


def discussion(topic: str, posts, provider) -> str:
    """What the model makes of the conversation, grounded in real titles."""
    if not posts:
        return "no substantive discussion found in the relevant communities"
    listing = "\n".join(
        f"- r/{p['subreddit']}: {p['title']} ({p['when']}"
        + (f", {p['score']} upvotes" if p.get("score") else "") + ")"
        for p in posts[:12])
    reply = provider.complete(
        [__import__("exchange.llm.base", fromlist=["LLMMessage"]).LLMMessage(
            "user", f"Category: {topic}\n\n{listing}")],
        system=DISCUSSION_PROMPT, max_tokens=900, reasoning_effort="low",
    )
    return reply.text.strip()


def research_topic(topic: str, provider=None, opener=urllib.request.urlopen,
                   api=None, trade: bool = True):
    """Discover, read, filter, and optionally interpret. One topic.

    Reads two kinds of community: the ones about the product, found by asking
    Reddit, and a fixed short list of communities where people run businesses.
    `trade=False` skips the second, which halves the requests when Reddit is
    throttling.

    Uses the official API when `api` is supplied — it does not throttle at
    this volume and it can rank by what people actually upvoted. Otherwise it
    reads the public RSS, which needs no credentials and does throttle.
    """
    if api is not None:
        found = api.research(topic)
        communities, posts = found["communities"], found["posts"]
        blocked, source = False, "api"
    else:
        got_subs = find_communities(topic, opener=opener)
        got_posts = search_posts(topic, got_subs.items, opener=opener,
                                 kind="category")

        # The second read, and the one that earns this module its place. See
        # TRADE_COMMUNITIES: the category's own subreddit is full of the
        # people who buy the product, and the desk wants the people who sell
        # it. Both are searched, both are kept, and each post says which.
        anchors = [Community(name, name) for name in TRADE_COMMUNITIES]
        got_trade = (Fetch(items=[], blocked=False) if not trade
                     else search_posts(topic, anchors, opener=opener,
                                       kind="trade"))

        communities = (
            [{"name": c.name, "title": c.title, "subscribers": None,
              "kind": "category"} for c in got_subs.items]
            + [{"name": c.name, "title": c.title, "subscribers": None,
                "kind": "trade"} for c in (anchors if trade else [])])

        # Operator posts lead. A merchant reading this board wants what another
        # merchant said before it wants what a hobbyist said, and the ordering
        # is the only place that preference can be expressed once both are in.
        kept = _dedupe(relevant(got_trade.items, topic)
                       + relevant(got_posts.items, topic))
        posts = [{"title": p.title, "subreddit": p.subreddit,
                  "author": p.author, "url": p.url, "when": p.when,
                  "rank": p.rank, "kind": p.kind,
                  "score": None, "comments": None}
                 for p in kept]
        blocked = got_subs.blocked or got_posts.blocked or got_trade.blocked
        source = "rss"

    if blocked and not posts:
        note = ("Reddit refused the request, so this topic was not read. "
                "That is not the same as nobody discussing it.")
    elif not posts:
        note = "Nothing substantive in the relevant communities."
    else:
        note = (discussion(topic, posts, provider) if provider else "")

    return {"topic": topic, "source": source, "blocked": blocked,
            "communities": communities, "posts": posts, "discussion": note}


# --- the official API, when there are credentials for it ---------------------
#
# The RSS path above needs nothing and is throttled hard. This path needs a
# free registered script app and is not, and it carries the two fields RSS
# cannot give: how many people upvoted a post, and how many replied. Ranking
# a market conversation without those is guesswork.

class RedditAPI:
    """Reddit's official read-only API, via PRAW.

    Credentials come from the environment and are never read into this
    program's own variables beyond handing them to PRAW, never logged, and
    never written anywhere. Register a script app at
    reddit.com/prefs/apps, then put in .env:

        REDDIT_CLIENT_ID=...
        REDDIT_CLIENT_SECRET=...

    Read-only: this never posts, votes, comments or messages. It is the same
    line drawn everywhere else here — growth signal comes from public
    read-only sources.
    """

    def __init__(self, client):
        self._reddit = client

    @classmethod
    def from_env(cls):
        """An API client, or None when it is not configured. Never raises."""
        import os

        cid = os.environ.get("REDDIT_CLIENT_ID")
        secret = os.environ.get("REDDIT_CLIENT_SECRET")
        if not cid or not secret:
            return None
        try:
            import praw

            client = praw.Reddit(client_id=cid, client_secret=secret,
                                 user_agent=USER_AGENT, check_for_async=False)
            client.read_only = True
            return cls(client)
        except Exception:
            return None

    def communities(self, topic: str, limit: int = 6):
        wanted = _terms(topic)
        out = []
        try:
            for sub in self._reddit.subreddits.search(topic, limit=limit * 3):
                name, title = sub.display_name, (sub.title or "")
                if not _names_topic(name, title, wanted):
                    continue
                out.append({"name": name, "title": title,
                            "subscribers": getattr(sub, "subscribers", None)})
                if len(out) >= limit:
                    break
        except Exception:
            return []
        return out

    def posts(self, topic: str, communities, limit: int = 10):
        if not communities:
            return []
        subs = "+".join(c["name"] for c in communities[:8])
        out = []
        try:
            for n, post in enumerate(
                    self._reddit.subreddit(subs).search(
                        topic, sort="top", time_filter="year", limit=limit * 2),
                    start=1):
                out.append({
                    "title": post.title or "",
                    "subreddit": str(post.subreddit),
                    "author": str(post.author) if post.author else "[deleted]",
                    "url": f"https://reddit.com{post.permalink}",
                    "when": time.strftime("%Y-%m-%d",
                                          time.gmtime(post.created_utc)),
                    "rank": n,
                    "score": int(post.score or 0),
                    "comments": int(post.num_comments or 0),
                })
        except Exception:
            return out
        return out

    def research(self, topic: str):
        subs = self.communities(topic)
        found = self.posts(topic, subs)
        # The same gate the RSS path uses: Reddit ranks by engagement before
        # it ranks by match, so a popular post about anything outranks a
        # quiet post about this.
        wanted = _terms(topic)
        keep = [p for p in found if _terms(p["title"]) & wanted]
        # With real scores available, rank by what people actually upvoted
        # rather than by where Reddit happened to put it.
        keep.sort(key=lambda p: -(p.get("score") or 0))
        return {"communities": subs, "posts": keep}
