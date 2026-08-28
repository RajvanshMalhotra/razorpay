"""The internal campaign board: what is trending across Razorpay's clients.

This is the house's second job, and the one only the payment processor can
do. A single merchant knows its own sales. Razorpay sees the whole book, so
it can see which campaigns are climbing across the client base before any
one client could — and rank them.

WHO IT IS FOR. Not merchants. The board is marked `razorpay_internal` in
every event it writes, and nothing in the merchant-facing path reads it. A
merchant reaches this material only the way anything else is reached here:
by winning the auction for a lot minted from it. That separation is the
whole reason the intelligence has a price.

TWO SOURCES, NEVER MIXED. The ranking is arithmetic over the event log —
settled value, distinct merchants, movement from the opening rounds to the
closing ones — and every figure is recomputable by anyone who dumps the same
events. The sentence beside each row is different in kind: it comes from the
public press, and it carries the URL and date it came from. The desk is
built so the second source can never move a number produced by the first;
research runs after the ranking is fixed and only ever attaches text.

That split matters more than it looks. An agent that reads the news and then
reports a number has laundered a headline into a fact. Here the number's
provenance is the log and the sentence's provenance is a link, and a reader
can check each against its own source.
"""
from __future__ import annotations

import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from exchange import events as ev
from exchange.house.insights import HOUSE_ACTOR_ID
from exchange.llm.base import LLMMessage

# THE FLOOR FOR A BOARD ROW, WHICH IS NOT THE FLOOR FOR A LOT. A ranking row
# discloses that a named category moved and how many businesses were in it. A
# lot discloses the playbook. The second is far more revealing than the
# first, so they do not share a threshold — but a row derived from one or two
# merchants is still that merchant's private trading made public, so there is
# a floor here too, and a row below it is refused and the refusal logged.
CAMPAIGN_FLOOR_K = int(os.environ.get("CAMPAIGN_FLOOR_K", "3"))

NEWS_RSS = "https://news.google.com/rss/search"

LABEL_PROMPT = """You are Razorpay's market research agent, grouping what
merchants across the platform have been buying into named campaigns.

You will be given numbered phrases that merchants typed when they went
looking for supply. Group them into campaigns. Two phrases belong to the same
campaign when a marketer would describe them as the same push.

GROUP BOLDLY. Aim for six to ten campaigns across the whole list, however
many phrases you are given. A board with a row per phrase tells a reader
nothing they could not get by reading the phrases themselves; the value is
in seeing that nine differently-worded requests were one movement.

Reply with one line per phrase, exactly `<number>: <campaign name>`.
A campaign name is two to four plain words, title case, no punctuation.
Reuse the identical name for every phrase in a group. Nothing else."""

DRIVER_PROMPT = """You are Razorpay's market research agent. A category is
climbing across Razorpay's merchant base and you have been given real press
headlines about that category.

Write ONE sentence, under 25 words, saying what is driving demand — the
launches, the seasonality, the regulation, the consumer shift, whatever the
headlines actually show. Lead with the driver, not with a caveat.

Ground it in the headlines. If they genuinely show nothing, say "the press
shows no clear driver" and stop — but read them properly first; a headline
about openings, launches or funding in this category IS a driver. Never name
a Razorpay merchant."""


@dataclass(frozen=True)
class Turn:
    """One merchant's completed turn, as the board reads it."""
    actor_id: str
    round: int
    need: str
    outcome: str
    amount: int


@dataclass(frozen=True)
class Source:
    """A real page, with the date it was published. Both go on screen."""
    title: str
    url: str
    published: str
    publisher: str


@dataclass
class Campaign:
    name: str
    needs: tuple[str, ...] = ()
    merchants: tuple[str, ...] = ()
    value_paise: int = 0
    settled: int = 0
    attempts: int = 0
    early_paise: int = 0
    late_paise: int = 0
    driver: str = ""
    sources: list[Source] = field(default_factory=list)

    @property
    def k(self) -> int:
        return len(set(self.merchants))

    @property
    def movement(self) -> float:
        """Closing rounds against opening rounds, as a multiple.

        Zero early value is reported as 0.0 rather than infinity: a campaign
        that did not exist at the start cannot be said to have multiplied,
        and the honest reading is in `early_paise` beside it.
        """
        if self.early_paise <= 0:
            return 0.0
        return self.late_paise / self.early_paise


@dataclass(frozen=True)
class Refusal:
    name: str
    k: int
    reason: str


def observe(events) -> list[Turn]:
    """Every turn that reached an outcome, read straight off the log.

    TURN_ENDED is the right event to read because it is the only one that
    carries the round, the merchant's own words for what it wanted, and what
    the turn came to — all three, on one row, written when it happened.
    """
    out = []
    for event in events:
        if event.type != ev.TURN_ENDED:
            continue
        payload = event.payload
        need = str(payload.get("need") or "").strip()
        if not need:
            continue
        out.append(Turn(
            actor_id=event.actor_id,
            round=int(payload.get("round") or 0),
            need=need,
            outcome=str(payload.get("outcome") or ""),
            amount=int(payload.get("amount") or 0),
        ))
    return out


def label(needs, provider):
    """Group the merchants' own phrasings into named campaigns.

    Nine merchants asked for cold brew concentrate in nine different
    sentences. Keyword matching would either split them into nine campaigns
    or need a hand-written synonym list — which is a place to hide a thumb on
    the scale. One model call over the distinct phrases does the grouping,
    and the mapping it returns is written into the event so a reader can
    disagree with it.

    Returns `(mapping, fallbacks)`. The second value is the count of phrases
    the model did not label, which the caller must be able to see: a
    fallback name is derived from the phrase itself, so a run where the model
    returned nothing produces a full, plausible-looking board of one-phrase
    campaigns. That is a failure wearing the costume of a result, and this
    project has now shipped that same shape of bug often enough to stop
    returning it silently.
    """
    phrases = sorted({n.strip() for n in needs if n.strip()})
    if not phrases:
        return {}, 0

    listing = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(phrases))
    reply = provider.complete(
        [LLMMessage("user", listing)],
        system=LABEL_PROMPT,
        # GROUPING IS THE EXPENSIVE THOUGHT, NOT THE ANSWER. The reply is
        # twenty-odd short lines, but the model reads every phrase against
        # every other before writing the first one, and at 1600 and 4000 it
        # spent the whole budget reasoning and returned "" — then the
        # fallback named each phrase after itself, producing a board of
        # twenty-two campaigns that looked like a model's opinion rather
        # than a failure. Cheap to overpay for once per run.
        max_tokens=12000,
        reasoning_effort="low",
    )

    mapping: dict[str, str] = {}
    for line in reply.text.splitlines():
        match = re.match(r"^\s*(\d+)\s*[:.)\-]\s*(.+?)\s*$", line)
        if not match:
            continue
        index = int(match.group(1)) - 1
        if 0 <= index < len(phrases):
            mapping[phrases[index]] = _clean_name(match.group(2))

    # A PHRASE THE MODEL SKIPPED IS NOT DROPPED. Losing it would quietly
    # shrink a campaign's value and merchant count, which is the one thing
    # the board must never do — an undercount looks exactly like a real
    # decline. It becomes its own campaign, and it is counted.
    fallbacks = 0
    for phrase in phrases:
        if phrase not in mapping:
            mapping[phrase] = _clean_name(phrase)
            fallbacks += 1
    return mapping, fallbacks


def _clean_name(text: str) -> str:
    words = re.sub(r"[^A-Za-z0-9 ]", " ", text).split()
    return " ".join(w.capitalize() for w in words[:4]) or "Unnamed"


def rank(turns, labels, floor: int = CAMPAIGN_FLOOR_K):
    """(ranked campaigns, refusals) — arithmetic only, no model, no press.

    Ordered by movement first and value second. Movement is what makes a
    board worth reading: the largest category is usually the largest for
    boring reasons, and the one climbing fastest is the one a merchant would
    pay to know about.
    """
    rounds = sorted({t.round for t in turns if t.round})
    if not rounds:
        return [], []
    midpoint = rounds[len(rounds) // 2]

    grouped: dict[str, Campaign] = {}
    for turn in turns:
        name = labels.get(turn.need) or _clean_name(turn.need)
        campaign = grouped.setdefault(name, Campaign(name=name))
        campaign.needs = tuple(sorted(set(campaign.needs) | {turn.need}))
        campaign.merchants = campaign.merchants + (turn.actor_id,)
        campaign.attempts += 1
        if turn.outcome == "settled":
            campaign.settled += 1
            campaign.value_paise += turn.amount
            if turn.round < midpoint:
                campaign.early_paise += turn.amount
            else:
                campaign.late_paise += turn.amount

    ranked, refused = [], []
    for campaign in grouped.values():
        if campaign.k < floor:
            refused.append(Refusal(
                name=campaign.name, k=campaign.k,
                reason=f"ranked from {campaign.k} merchants, "
                       f"below the board floor of {floor}",
            ))
            continue
        ranked.append(campaign)

    ranked.sort(key=lambda c: (c.movement, c.value_paise), reverse=True)
    return ranked, refused


def fetch_news(query: str, limit: int = 6, opener=urllib.request.urlopen):
    """Real, dated, public pages — no key, no scraping, no login.

    Google News' RSS endpoint is a deliberate choice over a paid search API:
    it is free, it needs no credential in a repository that must ship without
    one, and every item it returns carries the publisher and the publication
    date, which are the two things that make a citation checkable.
    """
    url = (f"{NEWS_RSS}?q={urllib.parse.quote(query)}"
           f"&hl=en-IN&gl=IN&ceid=IN:en")
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        body = opener(request, timeout=20).read()
        root = ET.fromstring(body)
    except Exception:
        # A NEWS OUTAGE IS NOT A MARKET EVENT. The ranking stands without
        # it; the row simply carries no explanation, and says so.
        return []

    out = []
    for item in root.findall(".//item")[:limit]:
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        out.append(Source(
            title=title,
            url=(item.findtext("link") or "").strip(),
            published=(item.findtext("pubDate") or "").strip(),
            publisher=(item.findtext("source") or "").strip(),
        ))
    return out


def research(campaign: Campaign, provider, fetcher=fetch_news) -> Campaign:
    """Attach the press to a row whose numbers are already fixed.

    Deliberately last, and deliberately incapable of changing the ranking.
    """
    # THE QUERY DECIDES THE ANSWER. "brand marketing campaign" returned
    # corporate restructuring and funding rounds, and the model correctly
    # refused to call that a marketing motion. Asking about demand and
    # launches returns pages that are actually about the category moving.
    sources = fetcher(f"{campaign.name} India demand launch trend brands")
    campaign.sources = list(sources)
    if not sources:
        campaign.driver = "no public coverage found for this category"
        return campaign

    headlines = "\n".join(f"- {s.title} ({s.publisher}, {s.published})"
                          for s in sources)
    reply = provider.complete(
        [LLMMessage("user", f"Category: {campaign.name}\n\n{headlines}")],
        system=DRIVER_PROMPT,
        max_tokens=900,
        reasoning_effort="low",
    )
    campaign.driver = reply.text.strip()
    return campaign


def publish(log, campaigns, refusals, correlation_id: str) -> None:
    """Write the board to the log, ranked rows and refused rows alike."""
    for refusal in refusals:
        log.append(HOUSE_ACTOR_ID, ev.PRIVACY_REFUSED, {
            "scope": "campaign_board",
            "campaign": refusal.name,
            "k": refusal.k,
            "floor": CAMPAIGN_FLOOR_K,
            "reason": refusal.reason,
        }, correlation_id=correlation_id)

    for position, campaign in enumerate(campaigns, start=1):
        log.append(HOUSE_ACTOR_ID, ev.CAMPAIGN_RANKED, {
            "audience": "razorpay_internal",
            "rank": position,
            "campaign": campaign.name,
            "movement": round(campaign.movement, 3),
            "value_paise": campaign.value_paise,
            "settled": campaign.settled,
            "attempts": campaign.attempts,
            "merchants": campaign.k,
            "floor": CAMPAIGN_FLOOR_K,
            "early_paise": campaign.early_paise,
            "late_paise": campaign.late_paise,
            "needs": list(campaign.needs),
            "driver": campaign.driver,
            "sources": [{"title": s.title, "url": s.url,
                         "published": s.published, "publisher": s.publisher}
                        for s in campaign.sources],
        }, correlation_id=correlation_id)
