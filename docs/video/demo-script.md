# Agent Exchange — demo video script

**Runtime 2:55 · stickman animation · Track 01, AI Growth & Agentic Commerce**

---

## The style block

Paste this into **every** Runway or Kling prompt, before the shot
description. Consistency across shots is the whole difficulty with generated
animation, and a fixed style block is what buys it.

> `2D hand-drawn stick figure animation. Plain white background. Black ink
> lines, uneven hand-drawn weight, slight boil between frames. Simple round
> heads, no faces except dot eyes. Flat, no shading, no gradient, no 3D. One
> accent colour only: warm yellow #F5C518. Locked-off camera, no zoom, no
> parallax. 12 frames per second. No text, no letters, no numbers, no
> watermark, no logos.`

**Say "no text" in every prompt.** Generated video renders letterforms as
garbage; every number and word in this film is added afterwards as a clean
overlay, or is real screen capture.

---

## The one judgment call

The animation carries the **story**. The product screens carry the **proof**.

I would not re-draw the dashboards as stickman. The whole submission rests on
*this actually ran and you can check it* — 968 recorded events, real Razorpay
payment ids. A cartoon of a dashboard proves nothing, and a judge who notices
the substitution will discount everything around it.

So: stickman for every idea, real capture for every claim. The cuts between
them are the rhythm of the film.

Every spoken figure is read from `runs/market.db`. The merchant followed
throughout, **bl_thirdwave**, is the same one whose payment broke and was
repaired — one thread, not a tour.

---

## 1 · Alone — 0:00–0:16

**[ANIM · Kling, 5s]**
> Style block. One stick figure stands alone behind a small counter, drawn
> with a few lines. It looks left, looks right. Nobody comes. It shrugs. Hold
> on the empty frame.

**[ANIM · Runway, 5s]**
> Style block. Wide shot: forty tiny stick figures, each alone behind its own
> small counter, evenly spaced across the white frame. None of them face each
> other. Completely still except a slow blink.

**VO**
> Every business on Razorpay is already connected to every other one. Money
> moves between them all day.
>
> Nobody has ever used that network to introduce them.

---

## 2 · Switching it on — 0:16–0:34

**[ANIM · Runway, 6s]**
> Style block. Same grid of forty lone stick figures. A single warm yellow
> line draws itself from one figure to another across the frame. Then a
> second, then several more, until a sparse web of yellow lines connects most
> of them. Lines draw on, they do not fade in. Figures stay still.

**VO**
> So we gave every merchant an agent. It finds who to deal with, argues the
> price down, settles on real Razorpay payments, and keeps the books.

**[SCREEN]** Cut to `docs/index.html`. Let the yellow highlighter finish
drawing across *a seat on the exchange*. Hold two seconds.

**VO**
> This is a real run. Thirty-two businesses. Nine hundred and sixty-eight
> recorded events. Not one figure on screen was typed by hand.

---

## 3 · The two who dealt — 0:34–1:04

**[ANIM · Kling, 6s]**
> Style block. Two stick figures face each other in profile, one on each side
> of the frame. Empty speech bubbles pop above them in turn — left, right,
> left, right — each bubble slightly smaller than the last as the gap between
> them narrows. Bubbles are outlined in black and empty inside. No text.

*In post: drop the real prices into the empty bubbles — 200, 205, 190, 195,
180, 190, 170, 185 — timed to each pop.*

**VO**
> This is what that looks like. Two agents, arguing about price, in plain
> English.

**[SCREEN]** Merchant page → **How your agent argued for you**. Let the real
offers scroll.

**VO**
> And these are their actual words, quoted from the log. Five rounds, closing
> from two hundred rupees a unit to a hundred and eighty-five.

**[SCREEN]** Scroll to the network ring. Let it tour once, then hover
`bl thirdwave`, hold, then `packmate`.

**VO**
> Thirty-two businesses. Thirty-six trading relationships — and nobody made a
> single introduction.

---

## 4 · One trade, end to end — 1:04–1:46

*The graded core. Do not rush it. This is the segment a judge replays.*

**[ANIM · Runway, 5s]** — plays under the first VO line, then cut away
> Style block. A stick figure hands a small yellow parcel to another stick
> figure. Between them stands a third figure holding one arm straight out
> like a barrier. The parcel stops at the arm. Hold two beats. The arm lowers.
> The parcel passes.

**[SCREEN]** Merchant page → **Where the money went** → the cold brew deal.
Rest four seconds on each station as you name it.

**VO**
> Every step carries the number of the event it came from, so you can look any
> of it up.
>
> Event 205 — the merchant wants a hundred and sixty units of cold brew.
>
> Event 217 — its agent shortlisted three sellers and argued for one.
>
> Event 256 — agreed at a hundred and ninety-five rupees a unit.
>
> Event 258 — and here is the part that matters. **The gate refused it.**
> Powerbank was an unknown supplier and the amount was over the cap for a
> first deal. So the agent came back smaller, and that was allowed.
>
> The refusal is recorded. Not the outcome — the ruling, before any money
> moved.
>
> Event 261 — four thousand eight hundred and seventy-five rupees, on a real
> Razorpay order.

---

## 5 · The failure, caught and repaired — 1:46–2:14

*The track asks for one failure handled gracefully. This is it, on the same
trade.*

**[ANIM · Kling, 6s]**
> Style block. A stick figure holds a ledger. Beside it a second stick figure
> holds an identical ledger. The two ledgers show different scribbles. The
> first figure notices, freezes mid-step with one foot raised. A third figure
> walks in, takes both ledgers, makes one mark, hands them back matching. The
> frozen figure puts its foot down and walks on.

**[SCREEN]** Same rail, stations six through nine. Rest on each.

**VO**
> Then it broke.
>
> Event 763 — Razorpay said that payment was captured. Our books said pending.
> Nobody was watching. The accountant found it on a routine sweep.
>
> Event 936 — it froze that one merchant. Not the market. One business's
> disagreement must not stop everybody else.
>
> Event 937 — it repaired the settlement using the payment id Razorpay gave
> back. Not one we invented. A repair that invents an id is a machine for
> asserting payments that never happened.
>
> Event 938 — and it let the merchant trade again. A freeze that never lifts
> is a ban, not a hold.
>
> Seventeen mismatches were caught and repaired this way. No human touched
> any of them.

---

## 6 · While you sleep — 2:14–2:36

**[ANIM · Runway, 5s]**
> Style block. Left half: a stick figure lies asleep, a small "z" shape above
> its head. Right half, separated by a thin vertical line: the same figure
> drawn in yellow moves briskly — walking, handing over a parcel, shaking
> hands with another figure. The sleeping half stays perfectly still.

**VO**
> You can only be in one meeting at a time. Your agent cannot.

**[SCREEN]** Merchant page → **My agents** — the four role cards. Then scroll
to **How your agent behaves** and tap two setting chips so the brief updates
live on camera.

**VO**
> It is four parts with separate memories. Three act; the fourth only watches,
> and remembers.
>
> And you tell it how to behave, in plain words. That sets its priorities. It
> can never set its permissions — no brief here can raise a spending limit.

**[SCREEN]** Cut to the Google Sheet, `bl_thirdwave` tab. Hold on the six
coloured cards, then scroll to **Your deals** and rest on *You saved*.

**VO**
> The books keep themselves, into the merchant's own Google Sheet.
>
> Eleven thousand five hundred and seventy spent — and the number only the
> agent knows: a thousand and fifty saved, because the supplier listed at two
> hundred and ten and the agent settled at a hundred and ninety-five.

---

## 7 · What only Razorpay can see — 2:36–2:50

**[ANIM · Kling, 5s]**
> Style block. Many small stick figures at the bottom of the frame, each
> looking down at its own tiny counter. High above them, one stick figure
> stands on a raised line and looks out across all of them. Yellow lines
> connect the figures below; only the raised one can see the whole shape.

**[SCREEN]** `desk.html` — type the passcode, enter, let the live floor run
three seconds, switch to **The board**.

**VO**
> The processor sees what no single merchant can. Which campaigns are climbing
> across the whole client base, ranked from the log, with the press that
> explains each one.
>
> That intelligence sells by sealed auction. Eight agents bid. The winner paid
> the runner-up's price. And the sixteen businesses whose trading produced the
> insight were each paid a royalty — without ever being named.

---

## 8 · Close — 2:50–3:02

**[ANIM · Runway, 6s]**
> Style block. The full grid of forty stick figures from the opening, now
> densely connected by steady yellow lines. Every figure turns to face the
> centre together, in one motion. Hold. Fade to white.

**VO**
> Payments told you a transaction happened.
>
> This tells you who should be doing business — and proves every rupee of it.

**[OVERLAY]** Held three seconds on white, black type:
> **968 events · 74 rulings recorded · 17 repairs · 0 humans**

---

## Production notes

**Generate every animated shot with the same style block and the same seed
where the tool allows it.** Stickman drifts badly between prompts otherwise —
line weight and head size change and the film stops feeling like one piece.

**Never let the model render text.** Speech bubbles are generated empty and
the numbers are added in post. Every attempt to make these tools draw a digit
produces something that looks like a forgery, which is the last impression
this submission wants.

**Capture the screens at 1600×1000 or wider.** Below about 1000px the network
ring and the desk collapse to the mobile layout and lose the density that
makes the point.

**Let the animations finish.** The highlighter takes 0.9s, the negotiation
replays over about 12s, the ring tours every 1.15s. Cutting mid-motion reads
as a glitch rather than as a cut.

**Do not speed up the rail.** It is the segment being graded and its entire
claim is that a person can follow it.

**The passcode is `razorpay`,** and the gate screen says out loud that it is a
stage prop rather than access control. Leave that line on screen for a beat.
A judge noticing you were honest about it is worth more than the second it
costs.

**If a number on screen disagrees with this script, the screen is right.**
Re-run `scripts.replay.generate`, re-read the figures, and correct the
narration. The log is the source; this document is only a copy of it.
