# Replay — Design

**Date:** 2026-08-26
**Plan:** 5 of 5 (schedule days 11–12)
**Status:** design

## What this is for

The judges do not read SQLite. Everything this project claims — that agents
reason, that every money action is gated, that a failure was caught and
repaired — is currently true only in a database. The replay is the surface
that makes the log legible, and it is the thing that gets recorded.

The bar, again, because it decides every choice below:

> Every money action explainable, bounded and gated. **Show the audit trail
> and one failure handled gracefully.**

The replay is not a dashboard. A dashboard shows a market's *state*; this has
to show a market's *reasoning*, because the reasoning is the product.

## The one design decision

**It replays a log. It never runs a market.**

The engine and the replay share nothing but the event file. Consequences,
all of them wanted:

- Nothing on screen can be a live call, so nothing can fail on camera, and
  no answer can differ between a rehearsal and the take.
- The claim "this is a real run" is checkable: the same file the replay reads
  is the file the accountant reconciled and Razorpay's dashboard corroborates.
- The replay cannot be accused of dramatising, because it has no state of its
  own to dramatise with. If it shows a price, that price is in the log.

## What it shows

Four views, in the order the video uses them.

### 1. The market — one screen of what happened

Thirty-two merchants, what they traded, what moved. Enough to establish that
this is a market and not a demo of two agents. Not interactive; it is the
establishing shot.

### 2. A trade, followed end to end

The centrepiece. One `correlation_id`, every event on it, in order:

```
ORDER_POSTED         a descriptive bid: "cold brew concentrate, unsweetened"
COUNTERPARTY_CHOSEN  the Diplomat's shortlist and its reason
NEGOTIATION_ROUND    both sides, alternating, with what each said
NEGOTIATION_ENDED    agreed at 21,560 — or walked, and why
MATCH_PROPOSED       the terms
POLICY_DECIDED       DENY: exceeds unknown counterparty cap
MATCH_PROPOSED       the same counterparty, smaller
POLICY_DECIDED       ALLOW
SETTLEMENT_INITIATED a real Razorpay order and payment link
```

The gate's refusal-then-allowance is the most valuable thing on this screen
and must not be summarised away: it is the anti-incumbency mechanism visible
in one trade, and it is the difference between a cap that exists and a cap
that binds.

### 3. The failure

The graded requirement, on its own thread:

```
SETTLEMENT_INITIATED
DRIFT_DETECTED        local=PENDING  remote=captured
ACTOR_FROZEN          books disagree on stl_...
SETTLEMENT_COMPLETED  pay_... (recovered from Razorpay)
ACTOR_RESUMED
```

Worth stating on screen, because it is the strongest fact available: **this
drift was not injected.** A payment link paid after `settle()` returned
PENDING produces exactly this state, and that is how it occurred.

### 4. The intelligence economy

Where the product idea lands: a headline mined from aggregate settled
activity, brokers valuing it in their own words, a second-price auction, and
royalties reaching the merchants whose activity made the lot. The privacy
refusal below the floor is shown too — a control that is visible is worth
more than one that is merely present.

## Architecture

**A static HTML file, generated from a log.** One command reads
`runs/market.db` and writes a self-contained page: no server, no build step,
no network at run time. It opens from disk, on any machine, in a year.

Rejected alternatives and why:

- **A live web app.** Needs a server running during the recording and can
  fail on camera. Buys interactivity the video does not use.
- **A notebook.** Executes on open, so what a viewer sees depends on their
  environment, and the claim "the log says this" stops being checkable.
- **A terminal replay.** Cheapest, and genuinely fine for the reasoning
  transcripts, but cannot show the market view or hold a judge's attention
  for four minutes.

**Every number on the page comes from the log**, computed at generation time
by the same projection code the exchange uses (`fold`), never re-derived by
hand in the template. A total on screen that a projection disagrees with
would be exactly the drift the accountant exists to catch.

## What it will not do

- **No live agent calls.** See the design decision above.
- **No editing or filtering that changes meaning.** Truncating a long
  transcript is fine; choosing which offers to show is not.
- **No invented visuals for absent data.** If lessons were never
  consolidated because nothing settled, the screen says so rather than
  showing an empty pane that implies a bug.

## Risks

**The log may be thin where the story needs to be thick.** If few merchants
traded or no insight was minted, no amount of presentation fixes it. The
replay is built against a real log from the start so this is discovered
early rather than on the last day.

**Too much on screen.** 1,172 events is not a video. The default view is one
trade; the market view is an establishing shot; everything else is reachable
but not shown by default.

**Prettiness competing with credibility.** The persuasive thing here is that
the numbers are real and checkable, not that the page is handsome. Where the
two conflict, the raw event wins — the design should look like a record,
not like marketing.
