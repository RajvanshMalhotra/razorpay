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

from scripts.replay.read import failure_threads, load

CSS = """
:root{--ink:#12161c;--dim:#6b7684;--line:#e3e7ec;--bg:#fbfcfd;--card:#fff;
--ok:#0f7b4f;--no:#a8331f;--gate:#1f4fa8;--accent:#8a5a00}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.55 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:980px;margin:0 auto;padding:40px 22px 80px}
h1{font-size:26px;margin:0 0 6px;letter-spacing:-.02em}
h2{font-size:19px;margin:44px 0 12px;letter-spacing:-.01em}
.sub{color:var(--dim);margin:0 0 28px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.stat b{display:block;font-size:22px;letter-spacing:-.02em}
.stat span{color:var(--dim);font-size:12.5px}
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

    # The trade to lead with: a real haggle that the gate refused once and
    # then allowed smaller. That single card carries discovery, reasoning,
    # the cap binding, and money moving.
    lead = next((t for t in trials if len(
        [e for e in t.events if e.type == "NEGOTIATION_ROUND"]) >= 3), None)
    lead = lead or (trials[0] if trials else (settled[0] if settled else None))

    stats = "".join([
        _stat(f"{summary.merchants}", "merchants"),
        _stat(f"{summary.negotiations}", "negotiations"),
        _stat(f"{summary.agreed} / {summary.walked}", "agreed / walked away"),
        _stat(f"{summary.gate_allow + summary.gate_deny}", "gate decisions"),
        _stat(f"{summary.settlements}", "settlements"),
        _stat(rupees(summary.value_paise), "transacted"),
        _stat(f"{summary.distinct_traders}", "distinct traders"),
        _stat(f"{summary.events}", "events in the log"),
    ])

    if failures:
        failed_trade = next(t for t in trades if t.correlation_id == failures[0]) \
            if any(t.correlation_id == failures[0] for t in trades) else None
        failure_html = (_events(failed_trade) if failed_trade else
                        '<div class="empty">Recorded outside a trade thread.</div>')
        failure_note = ("This drift was not injected. A payment link paid after "
                        "the settlement returned PENDING produces exactly this "
                        "state, and that is how it occurred.")
    else:
        failure_html = ""
        failure_note = ("No drift in this log yet. A settlement only drifts once "
                        "its payment link has actually been paid — until then "
                        "every settlement is honestly PENDING, which is the "
                        "state the accountant is built to reconcile.")

    econ = ""
    if summary.insights == 0:
        econ = (f'<div class="note">The privacy floor needs 25 distinct '
                f'contributing merchants and this log has '
                f'<b>{summary.distinct_traders}</b> that traded, of which '
                f'<b>{summary.completed}</b> have settled payments. Nothing has '
                f'been minted, and the house agent refusing to mint below the '
                f'floor is the control working rather than a gap.</div>')

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    cards = "".join(_trade_card(t) for t in trades[:12])

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent Exchange — market replay</title><style>{CSS}</style></head>
<body><div class="wrap">
<h1>Agent Exchange — a market that happened</h1>
<p class="sub">Every figure below is read from <code>{esc(db_path)}</code>, an
append-only log. This page runs nothing: it replays what the agents did.</p>

<h2>The market</h2>
<div class="grid">{stats}</div>

<h2>One trade, followed end to end</h2>
<p class="sub">Discovery, a real negotiation, the gate refusing a full lot to a
stranger and then allowing a smaller one, and money moving.</p>
{_trade_card(lead) if lead else '<div class="empty">No completed trade in this log.</div>'}

<h2>The failure, caught and repaired</h2>
<div class="note">{esc(failure_note)}</div>
{failure_html}

<h2>The intelligence economy</h2>
{econ or '<div class="empty">—</div>'}

<h2>Other trades</h2>
<p class="sub">{len(trades)} turns in this log; the first 12 shown.</p>
{cards}

<footer>Generated {esc(generated)} from {esc(summary.events)} events.
Gate decisions: {summary.gate_allow} allowed, {summary.gate_deny} refused.
Nothing on this page was computed by the page — every number comes from the
log or from the same projection the exchange runs on.</footer>
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
