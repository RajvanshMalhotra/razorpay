# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Static HTML, generated. `scripts/replay/generate.py` reads an append-only
SQLite event log and writes one page per merchant plus the internal desk — no
framework, no build step, no network at view time. Regenerate with:

```
.venv/bin/python -m scripts.replay.generate runs/market.db docs/
```

Every figure on the page is baked in from the log at generation time. The
page cannot fetch, so it cannot show anything the log did not contain.

## Surfaces

**Two audiences that want opposite things, so two pages with two visual
worlds.** This is the central product decision and everything else follows
from it.

| | `docs/m-<merchant>.html` | `docs/desk.html` |
|---|---|---|
| Reader | a merchant | Razorpay staff |
| Question | what did my agents do with my money | what is the whole book doing |
| Look | light, roomy, friendly | pure-black terminal, dense |
| Motion | none — read it like a statement | live tape, counters climbing |
| Gate | open | passcode (a stage prop, labelled as one) |

A merchant does not benefit from watching thirty-two agents trade, so the
live floor is not on its page. The campaign board is not either: ranking what
is climbing across the client base is the house's own view, and a merchant
reaches that material only by winning the auction for a lot minted from it.

The merchant page carries four things: the four parts of its broker and what
each one did, the money trail per trade, its catalogue, and one input box.

## Users

**Primary: hackathon judges, watching a recorded video.** They see the page
through someone else's screen recording, once, at speed, with narration over
it. They cannot click, cannot hover, and cannot scroll back. Anything that
needs interaction to be understood is invisible to them.

**Secondary: the same judges, opening the page themselves afterward.** This
is where the checking happens — raw event rows, correlation ids, Razorpay
payment ids. The page must reward that second visit without the first one
depending on it.

**Inside the fiction: Razorpay staff.** The campaign board is marked
`razorpay_internal` and the design says so out loud. Merchants reach that
material only by winning an auction for it.

## Product Purpose

An exchange where every Razorpay merchant is represented by an AI broker
agent. Brokers discover each other, negotiate, and settle real trades on
Razorpay test-mode APIs. A house research agent mines cross-merchant activity
into a ranked board of trending client campaigns, publishes free headlines,
and auctions the details for points. Points convert to fee rebates;
contributors earn royalties when their win is sold.

Success is one thing: a judge believes the log is real. Every claim on screen
must be traceable to an event they can dump themselves.

## Positioning

*Razorpay is the platform. Merchants are the creators. Their wins are the
content. Other merchants pay to watch. Revenue is shared.*

The campaign board is the part a neighbouring product cannot truthfully
copy. Ranking what is climbing across a client base requires seeing the whole
book, which only the payment processor does.

## Not built, and never faked

Slack, Discord, WhatsApp and Telegram integration is **out of scope by
decision**, not by omission — autonomous outreach on those platforms violates
their terms and reads as a spambot. The merchant page has a messages feed
because merchants want one; it is fed from what the agents actually recorded,
every line carries its event number, and the panel says in plain words that
Slack and Discord are not connected. Inventing chatter there would be the one
fabricated thing on a pair of pages whose whole claim is that every figure is
checkable.

Catalogue items a merchant adds are saved in that browser only, and the card
says so. The log is sealed and read-only from the page.

## Constraints

- **Every figure is real and checkable.** Credibility outranks polish. A
  number on screen that the event log disagrees with is the exact failure the
  system's own accountant exists to catch.
- **Two provenances, never blended.** Rankings are arithmetic over the log.
  Explanations come from the public press and carry their URL and date. The
  design must keep these visually distinct, because a headline rendered like
  a computed figure is a laundered claim.
- **Read-only.** Nothing under `scripts/replay/` writes to the log, calls a
  model, or touches Razorpay. The page's whole claim is that it shows what
  happened rather than making anything happen.
- **Plain language over jargon.** Each event type carries a human sentence
  beside its raw type; the raw type stays visible because it is what makes
  the log checkable.
- **Self-contained.** One HTML file, no external requests.

## Terminology

| On screen | Means |
|---|---|
| the gate | the policy check that rules on every money action before it happens |
| drift | the local books disagreeing with Razorpay about a payment |
| the board | Razorpay's internal ranking of trending client campaigns |
| a lot | a piece of market intelligence sold at auction for points |
| the floor | the minimum number of distinct merchants before something may be published |
| trial trade | a first deal with an unknown counterparty, capped small |
| correlation id | the single id threading one trade's whole story |

## Evidence the design must surface

- Real Razorpay test-mode payment ids on completed settlements.
- The refused-then-retried-smaller pattern — the anti-incumbency cap visible
  inside one trade.
- One failure caught and repaired without a human: drift detected → actor
  frozen → repaired from the log → resumed.
- A privacy floor refusing to publish, logged as loudly as a success.
- A human typing into the storefront and reaching the same machinery.

## Accessibility

Keyboard-reachable scene switching and playback controls; visible focus;
`prefers-reduced-motion` respected — the tape must be readable stepped rather
than animated. Colour is never the only carrier of a verdict: allow and deny
also differ in label and glyph.

## Open

- Nothing blocking. The narration script for the recorded video is not yet
  written.
