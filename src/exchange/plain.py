"""Money and rulings as a merchant reads them, not as the log stores them.

THE SAME MISTAKE TWICE IS WHY THIS IS A MODULE. Prices are paise everywhere
inside the exchange, which is correct — integers do not drift. But paise
reaching a person is a defect, and it has been fixed twice in two places: on
the replay pages, where a station read "agreed 19500", and in a merchant's
own Google Sheet, where its agent's written verdict said "they paid the
agreed 19500 per unit" long after the pages were clean.

Anything a merchant reads goes through here, so there is one place to fix and
no second copy to forget.
"""
from __future__ import annotations

import re


def rupees(paise) -> str:
    """A figure as a person writes it: ₹4,875."""
    try:
        return f"₹{int(paise) / 100:,.0f}"
    except (TypeError, ValueError):
        return str(paise)


def money_words(text) -> str:
    """Rewrite paise inside written text into rupees.

    An agent files its lessons in the units it was handed: "They paid the
    full 308000 paise at the agreed 1540 per unit." Both figures are money
    and a merchant reading its own agent should see money.

    "per unit" is what marks a bare number as a price rather than a quantity,
    so it is the one place a three-digit figure is safe to convert. "160
    units" is left alone.
    """
    def inr(m):
        return f"₹{int(m.group(1)) / 100:,.2f}".replace(".00", "")

    # A COMMA HID PAISE FROM EVERY CHECK. An agent comparing two offers
    # wrote "(12,000 vs 24,500)" — ₹120 against ₹245 — and neither the
    # rewriter nor the page scanner saw it, because both keyed on a run of
    # digits and a comma breaks the run. The ₹ lookbehind is what keeps this
    # from re-dividing a figure that has already been converted.
    def grouped(m):
        return inr(re.match(r"(\d+)", m.group(1).replace(",", "")))

    out = re.sub(r"(?<![₹\d,.])(\d{1,3}(?:,\d{3})+)(?!\s*(?:units?|events?|"
                 r"posts?|threads?|merchants?|trades?|points?))",
                 grouped, str(text or ""))
    out = re.sub(r"\b(\d{3,})\s*paise\b", inr, out)
    # A FIGURE ALREADY IN RUPEES MUST NOT BE DIVIDED AGAIN. "agreed ₹195 a
    # unit" came back as "agreed ₹₹1.95 a unit": the per-unit rule had no
    # lookbehind, so it converted a number the rail had already formatted.
    out = re.sub(r"(?<![₹\d,.])(\d{3,})(?=\s*(?:per unit|a unit|/unit|each))",
                 inr, out)
    # A COUNT IS NOT A PRICE. A four-figure number followed by its own noun
    # is a quantity — "2,600 units" became "₹26 units" the first time this
    # ran over a station head.
    return re.sub(r"(?<![₹\d,.])\b(\d{4,})\b(?!\s*(?:units?|events?|posts?|"
                  r"threads?|merchants?|trades?|points?))", inr, out)


_ACTOR = re.compile(r"\bm_[a-z0-9_]+")


def people(text, who=None) -> str:
    """Rewrite actor ids inside written text into business names.

    An agent writes its verdict about a counterparty the way it holds it:
    "m_reelco paid the full ₹4,968 at the agreed ₹46 per unit." Correct, and
    the one thing a merchant reading its own books should never meet. The
    figures were cleaned up long before the name beside them was.

    `who` resolves an id to a name; without one the id is tidied up, so a
    business that joined after the roster was written still reads as a name.
    """
    name = who or (lambda a: str(a)[2:].replace("_", " ").title())
    return _ACTOR.sub(lambda m: name(m.group(0)), str(text or ""))


def gate_reason(reason) -> str:
    """The gate's ruling in money and in English.

    "Amount 3120000 exceeds per-transaction cap 2000000" is the log's own
    wording, and it is the log's to keep. What a merchant needs is the rule
    that bound and the two figures it bound on.

    Accepts either the reason text or the ruling's payload, because callers
    hold one or the other and neither should have to unwrap it.
    """
    if isinstance(reason, dict):
        reason = reason.get("reason", "")
    text = re.sub(r"\b(\d{4,})\b",
                  lambda m: rupees(m.group(1)), str(reason or ""))
    text = text.replace("Amount ", "").replace("exceeds", "is over the")
    text = text.replace("per-transaction cap", "cap on a single payment of")
    text = text.replace("unknown counterparty cap",
                        "cap for a supplier with no track record of")
    return text[:110] or "within every limit"


def humanise(text, who=None) -> str:
    """Everything at once: ids into names, paise into rupees.

    Quoted agent text needs both, always, and every place that forgot one of
    them shipped the other half of the problem.
    """
    return people(money_words(text), who)


_PREFIX = re.compile(r"^\s*(?:BID|PRICE|ASK)\s*:\s*[\d,.]*\s*", re.I)


def offer_text(message, who=None) -> str:
    """What an agent said, without the price it already said in numbers.

    An offer is filed as "PRICE: 24500\nThis is within our budget." The
    figure is already the row's own price column, so repeating it in the
    sentence is noise — and it is the one figure in the sentence that is
    definitely paise.
    """
    said = _PREFIX.sub("", str(message or ""))
    return " ".join(humanise(said, who).split())


_RANK = re.compile(r"^\s*\d+[.)]\s+")


def reasoning(text, who=None) -> str:
    """An agent's stated reason, without the shape of the list it came from.

    The Diplomat ranks its options, so the log holds "3. The price is
    dramatically below your threshold…". The rank is real and belongs in the
    log; on a station it opens mid-list and reads like a fragment.
    """
    return humanise(_RANK.sub("", str(text or "")), who)
