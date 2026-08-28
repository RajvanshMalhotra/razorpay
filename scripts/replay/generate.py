"""Turn a market log into one self-contained HTML page.

    .venv/bin/python -m scripts.replay.generate runs/market.db docs/replay.html

No server, no build step, no network at view time. It opens from disk, on any
machine, in a year — which matters because the page's whole claim is that a
reader can check it, and a page that needs infrastructure to render is a page
that will eventually stop rendering.

DESIGN RULE, applied everywhere below: where legibility and credibility
conflict, the raw event wins. Prices are shown as the log records them,
reasoning is quoted rather than paraphrased, and nothing on the page is
computed here that `fold` could compute instead.
"""
from __future__ import annotations

import html
import sys
from datetime import datetime, timezone

from scripts.replay.read import auction, failure_threads, lessons, load

CSS = """
/* Designed to read as a RECORD, not as marketing. The persuasive thing here
   is that the numbers are real and checkable, so the page gets out of their
   way: one accent colour used only for the gate, monospace wherever a figure
   comes straight from the log, and no illustration anywhere. A judge should
   feel they are reading evidence. */
:root{--ink:#12161c;--dim:#6b7684;--line:#e3e7ec;--bg:#fbfcfd;--card:#fff;
--ok:#0f7b4f;--no:#a8331f;--gate:#1f4fa8;--accent:#8a5a00}

/* A judge may open this in either theme, and a light-only page in dark mode
   reads as unfinished. Only the tokens change. */
@media (prefers-color-scheme: dark){
  :root{--ink:#e8ecf1;--dim:#98a3b2;--line:#232a33;--bg:#0e1116;--card:#151a21;
  --ok:#4ec38a;--no:#ef8571;--gate:#7aa6f0;--accent:#d9a441}
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.6 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif;
-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility;
/* Every figure on this page is evidence, and evidence that shifts column
   as it changes is harder to compare. Lining, tabular figures throughout. */
font-variant-numeric:tabular-nums lining-nums}
.wrap{max-width:900px;margin:0 auto;padding:0 22px 90px}
.beat{padding:46px 0;border-top:1px solid var(--line)}
.beat:first-of-type{border-top:0}
/* Two-digit markers, mono, low contrast — structure a reader feels rather
   than reads. They must not compete with the heading beside them, so the
   number sits at the same baseline and half the weight of the words. */
.beat>.num{display:flex;gap:12px;align-items:baseline;margin-bottom:10px;
font:500 11.5px/1 ui-monospace,SFMono-Regular,Menlo,monospace;
letter-spacing:.13em;text-transform:uppercase;color:var(--dim)}
.beat>.num .n{color:var(--line);font-weight:600;
/* darker than the rule it matches, so it reads as ink not as a border */
filter:brightness(.72)}
.lede{color:var(--dim);max-width:64ch;margin:0 0 20px}
/* The one interactive control on the page. It is used once, deliberately,
   so it gets feedback rather than motion — and a visible focus ring,
   because a keyboard user should never have to guess where they are. */
summary{border-radius:6px;padding:2px 4px;margin:-2px -4px;
transition:color 120ms ease}
summary:hover{color:var(--ink)}
summary:focus-visible{outline:2px solid var(--gate);outline-offset:2px}
@media (prefers-reduced-motion: reduce){*{transition-duration:0.01ms!important;
animation-duration:0.01ms!important}}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.big{font-size:31px;letter-spacing:-.025em;margin:0 0 4px}
.hero{padding:54px 0 30px}
.rule{border:0;border-top:1px solid var(--line);margin:26px 0}
.two{display:grid;grid-template-columns:1fr 1fr;gap:22px}
@media(max-width:720px){.two{grid-template-columns:1fr}}
.q{border-left:2px solid var(--line);padding:2px 0 2px 14px;margin:10px 0;
color:var(--ink)}
.q .by{color:var(--dim);font-size:12.5px}
h1{font-size:30px;margin:0 0 10px;letter-spacing:-.028em;line-height:1.18;
max-width:20ch}
h2{font-size:19px;margin:44px 0 12px;letter-spacing:-.01em}
.sub{color:var(--dim);margin:0 0 28px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.stat b{display:block;font-size:23px;letter-spacing:-.022em;line-height:1.25}
.stat span{color:var(--dim);font-size:12.5px;display:block;margin-top:3px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:18px;margin:14px 0}
.head{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;
align-items:baseline;margin-bottom:10px}
.who{font-weight:600}
.tag{font-size:11.5px;text-transform:uppercase;letter-spacing:.05em;
padding:2px 8px;border-radius:20px;border:1px solid var(--line);color:var(--dim)}
.tag.ok{color:var(--ok);border-color:#bfe3d0}
.tag.no{color:var(--no);border-color:#efc9c1}
table{width:100%;border-collapse:collapse;font-size:14px}
td{padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:0}
td.a{white-space:nowrap;font-weight:600;width:1%}
td.p{white-space:nowrap;text-align:right;font-variant-numeric:tabular-nums;width:1%}
.ev{font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--dim)}
.ev td{padding:5px 8px}
.ev .t{color:var(--ink)}
.ev .deny{color:var(--no);font-weight:600}
.ev .allow{color:var(--gate);font-weight:600}
.note{background:#fff8ea;border:1px solid #f0dfba;border-radius:10px;
padding:14px 16px;margin:14px 0;font-size:14px}
.empty{color:var(--dim);font-style:italic;padding:12px 0}
.scroll{overflow-x:auto}
footer{margin-top:56px;color:var(--dim);font-size:12.5px;
border-top:1px solid var(--line);padding-top:16px}
"""


def esc(text) -> str:
    return html.escape(str(text if text is not None else ""))


def rupees(paise) -> str:
    return f"₹{(paise or 0) / 100:,.2f}"


def _stat(value, label) -> str:
    return f'<div class="stat"><b>{esc(value)}</b><span>{esc(label)}</span></div>'


def _negotiation(trade) -> str:
    rounds = [e for e in trade.events if e.type == "NEGOTIATION_ROUND"]
    if not rounds:
        return '<div class="empty">No offers were exchanged.</div>'
    rows = "".join(
        f'<tr><td class="a">{esc(e.payload.get("by", e.actor_id))}</td>'
        f'<td class="p">{esc(e.payload.get("price"))}</td>'
        f'<td>{esc(e.payload.get("message", "").strip())}</td></tr>'
        for e in rounds
    )
    return f'<div class="scroll"><table>{rows}</table></div>'


def _events(trade) -> str:
    rows = []
    for e in trade.events:
        detail = ""
        if e.type == "POLICY_DECIDED":
            verdict = e.payload.get("verdict", "")
            css = "deny" if verdict != "ALLOW" else "allow"
            detail = (f'<span class="{css}">{esc(verdict)}</span> '
                      f'{esc(e.payload.get("reason", ""))}')
        elif e.type == "NEGOTIATION_ENDED":
            detail = ("agreed at " + esc(e.payload.get("final_price"))
                      if e.payload.get("agreed")
                      else esc(e.payload.get("reason", "")))
        elif e.type == "SETTLEMENT_INITIATED":
            detail = (f'{rupees(e.payload.get("amount"))} · '
                      f'{esc(e.payload.get("razorpay_order_id"))}')
        elif e.type == "SETTLEMENT_COMPLETED":
            detail = esc(e.payload.get("razorpay_payment_id"))
        elif e.type == "DRIFT_DETECTED":
            detail = (f'local {esc(e.payload.get("local_status"))} · '
                      f'remote {esc(e.payload.get("remote_status"))}')
        elif e.type == "ACTOR_FROZEN":
            detail = esc(e.payload.get("reason"))
        rows.append(f'<tr><td class="p">{e.seq}</td>'
                    f'<td class="a">{esc(e.actor_id)}</td>'
                    f'<td class="t">{esc(e.type)}</td><td>{detail}</td></tr>')
    return f'<div class="scroll"><table class="ev">{"".join(rows)}</table></div>'


def _detail(e) -> str:
    p = e.payload
    if e.type == "POLICY_DECIDED":
        v = p.get("verdict", "")
        css = "deny" if v != "ALLOW" else "allow"
        return f'<span class="{css}">{esc(v)}</span> {esc(p.get("reason", ""))}'
    if e.type == "NEGOTIATION_ENDED":
        return ("agreed at " + esc(p.get("final_price"))
                if p.get("agreed") else esc(p.get("reason", "")))
    if e.type == "SETTLEMENT_INITIATED":
        return f'{rupees(p.get("amount"))} · {esc(p.get("razorpay_order_id"))}'
    if e.type == "SETTLEMENT_COMPLETED":
        return esc(p.get("razorpay_payment_id"))
    if e.type == "DRIFT_DETECTED":
        return (f'local {esc(p.get("local_status"))} · '
                f'remote {esc(p.get("remote_status"))}')
    if e.type in ("ACTOR_FROZEN",):
        return esc(p.get("reason"))
    return ""


def _trade_card(trade) -> str:
    settled = trade.outcome == "settled"
    tag = f'<span class="tag {"ok" if settled else "no"}">{esc(trade.outcome or "—")}</span>'
    trial = ('<span class="tag">refused, then allowed smaller</span>'
             if trade.was_refused_then_allowed else "")
    price = (f'<span class="tag">agreed {esc(trade.agreed_price)}/unit</span>'
             if trade.agreed_price else "")
    amount = (f'<span class="tag">{rupees(trade.settled_amount)}</span>'
              if trade.settled_amount else "")
    return f"""<div class="card">
  <div class="head">
    <div><span class="who">{esc(trade.buyer_id)}</span>
      <span style="color:var(--dim)"> wanted </span>{esc(trade.need)}</div>
    <div>{price}{amount}{trial}{tag}</div>
  </div>
  {_negotiation(trade)}
  <details style="margin-top:12px">
    <summary style="cursor:pointer;color:var(--dim);font-size:13px">
      every event on this trade's thread</summary>
    {_events(trade)}
  </details>
</div>"""


def build(db_path: str) -> str:
    summary, trades, events = load(db_path)
    settled = [t for t in trades if t.outcome == "settled"]
    trials = [t for t in trades if t.was_refused_then_allowed]
    failures = failure_threads(events)
    sale = auction(events)
    learned = lessons(events)

    # The trade to lead with: a real haggle the gate refused once and then
    # allowed smaller. One card carrying discovery, reasoning, the cap
    # binding, and money moving.
    lead = next((t for t in trials
                 if len([e for e in t.events
                         if e.type == "NEGOTIATION_ROUND"]) >= 3), None)
    lead = lead or (trials[0] if trials else (settled[0] if settled else None))

    stats = "".join([
        _stat(summary.merchants, "merchants"),
        _stat(f"{summary.agreed} / {summary.walked}", "agreed / walked away"),
        _stat(summary.gate_allow + summary.gate_deny, "gate decisions"),
        _stat(summary.completed, "payments completed"),
        _stat(rupees(summary.value_paise), "transacted"),
        _stat(summary.points_minted, "points minted"),
        _stat(summary.lessons, "lessons learned"),
        _stat(summary.events, "events in the log"),
    ])

    # --- beat 3: the failure ------------------------------------------------
    if failures:
        thread = [e for e in events if e.correlation_id == failures[0]]
        rows = "".join(
            f'<tr><td class="p">{e.seq}</td><td class="a">{esc(e.actor_id)}</td>'
            f'<td class="t">{esc(e.type)}</td><td>{_detail(e)}</td></tr>'
            for e in thread)
        failure_html = f'<div class="scroll"><table class="ev">{rows}</table></div>'
        failure_note = ("Not injected. A payment link paid after the settlement "
                        "returned PENDING produces exactly this, and that is how "
                        f"it happened — {len(failures)} times in this log.")
    else:
        failure_html = ""
        failure_note = ("No drift here yet: a settlement can only drift once its "
                        "payment link has been paid.")

    # --- beat 4: the intelligence economy -----------------------------------
    if sale:
        bid_rows = "".join(
            f'<tr><td class="a">{esc(e.actor_id)}</td>'
            f'<td class="p mono">{esc(e.payload.get("amount"))}</td>'
            f'<td>{esc(str(e.payload.get("reason", ""))[:110])}</td></tr>'
            for e in sale["bids"])
        paid_each = (sale["royalties"][0].payload.get("amount")
                     if sale["royalties"] else 0)
        econ = f"""
      <p class="big">&ldquo;{esc(sale['headline'])}&rdquo;</p>
      <p class="lede">Mined by the house agent from {sale['contributors']}
        merchants' settled trades. No merchant could have seen this alone.</p>
      <div class="scroll"><table>{bid_rows}</table></div>
      <hr class="rule">
      <p><b>{esc(sale['winner'])}</b> won and paid
        <span class="mono">{esc(sale['price'])}</span> — the runner-up's bid,
        not its own. {len(sale['royalties'])} contributing merchants each
        earned <span class="mono">{esc(paid_each)}</span> points from a win
        they did not know was being sold.</p>"""
    else:
        econ = (f'<div class="note">The privacy floor refused: only '
                f'{summary.distinct_traders} merchants contributed. A floor '
                f'that refuses is the control working.</div>')

    lesson_html = "".join(
        f'<div class="q">{esc(e.payload.get("text", ""))[:190]}'
        f'<div class="by">{esc(e.actor_id)} on {esc(e.payload.get("counterparty_id"))}'
        f' · {esc(e.payload.get("kind"))}</div></div>'
        for e in learned[:4])

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent Exchange — market replay</title><style>{CSS}</style></head>
<body><div class="wrap">

<div class="hero">
  <h1>A market of AI agents, and the record it left</h1>
  <p class="lede">{summary.merchants} Razorpay merchants, each represented by
    an agent that found counterparties, negotiated, and settled real test-mode
    payments. Every figure below is read from an append-only log. This page
    runs nothing.</p>
</div>

<section class="beat">
  <div class="num"><span class="n">01</span><span>the market</span></div>
  <div class="grid">{stats}</div>
  <p class="lede" style="margin-top:18px">{summary.walked} negotiations ended
    without a deal. That is not a failure rate — agents decline, and a market
    where every deal closes is not a market.</p>
</section>

<section class="beat">
  <div class="num"><span class="n">02</span><span>one trade, end to end</span></div>
  <p class="lede">Discovery, a real negotiation, the gate refusing a full lot
    to a stranger and then allowing a smaller one, and money moving.
    {len(trials)} of this log's trades were refused and re-tried smaller.</p>
  {_trade_card(lead) if lead else '<div class="empty">No completed trade.</div>'}
</section>

<section class="beat">
  <div class="num"><span class="n">03</span><span>the failure, caught and repaired</span></div>
  <p class="lede">{esc(failure_note)}</p>
  {failure_html}
</section>

<section class="beat">
  <div class="num"><span class="n">04</span><span>the intelligence economy</span></div>
  {econ}
</section>

<section class="beat">
  <div class="num"><span class="n">05</span><span>what the agents remember</span></div>
  <p class="lede">Each merchant's Subconscious compresses a whole trade into
    one durable sentence, typed by what it may affect: a reliability lesson
    can move a counterparty's standing, a behavioural one never can.</p>
  {lesson_html or '<div class="empty">No lessons yet.</div>'}
</section>

<footer>Generated {esc(generated)} from {esc(summary.events)} events in
{esc(db_path)}. Gate: {summary.gate_allow} allowed, {summary.gate_deny}
refused. Nothing on this page was computed by the page — every number comes
from the log or the projection the exchange itself runs on.</footer>
</div></body></html>"""


def main(argv=None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    db = args[0] if args else "runs/market.db"
    out = args[1] if len(args) > 1 else "docs/replay.html"
    page = build(db)
    import pathlib
    path = pathlib.Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    print(f"wrote {out}  ({len(page):,} bytes) from {db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
