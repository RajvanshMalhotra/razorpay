"""Turn a market log into two self-contained HTML pages.

    .venv/bin/python -m scripts.replay.generate runs/market.db docs/

    docs/replay.html   the exchange — follow any trade end to end
    docs/desk.html     Razorpay internal — the live floor and the board

No server, no build step, no network at view time. They open from disk, on any
machine, in a year — which matters because the pages' whole claim is that a
reader can check them, and a page that needs infrastructure to render is a
page that will eventually stop rendering.

TWO PAGES, BECAUSE THERE ARE TWO AUDIENCES AND THEY WANT OPPOSITE THINGS.

A merchant does not care to watch thirty-two agents trade. It cares about one
question — what did my agent do with my money, and can I check it — so
`replay.html` is a single trade's trail, still, readable, picked by hand. No
clock, no ticker, nothing moving. You read it like a statement.

Razorpay's own staff want the opposite: everything at once, live. The floor,
the ledger, the agents, and the campaign board that ranks what is climbing
across the client base. That is `desk.html`, behind a gate, because none of it
is a merchant's to see.

THE GATE IS A STAGE PROP AND THE PAGE SAYS SO. These are static files; a
client-side check is not access control, and dressing one up as if it were
would be the only dishonest thing on an otherwise checkable pair of pages.

ONE LAYOUT, LEARNED ONCE. Every trade travels the same rail — wants, picked,
negotiated, the gate, paid, remembered. The interesting cases are not
different screens: a trade that breaks grows three more stations out of the
fifth one; a trade a person typed differs only at the first.

EVERY STATION IS STAMPED WITH ITS EVENT NUMBER. That number is the argument in
its smallest form — look it up in the log and you find exactly what the
station says.

TYPE CARRIES PROVENANCE. Mono is what the system recorded: numbers, ids, event
types, verdicts. Serif is what somebody said: an agent's reasoning, a lesson,
a headline from the press.
"""
from __future__ import annotations

import html
import json
import math
import pathlib
import sys
from datetime import datetime, timezone

from exchange.books import COLUMNS, entries_for
from scripts.replay.read import (
    auction,
    board,
    brief_for,
    catalogue,
    failure_threads,
    load,
    benchmarks,
    benchmarks,
    merchant_view,
    performance,
    radar,
    rails,
    storefront,
    tape,
)


def who(actor_id) -> str:
    """A merchant's name as a person would write it.

    Actor ids are `m_bl_thirdwave` — a prefix so the log can tell a merchant
    from the house, the gate and the accountant at a glance. That prefix is
    plumbing. On screen it reads as a variable name and makes a page of real
    businesses look like a database dump, so it comes off everywhere a reader
    looks. The raw id still travels in the data attributes and the JSON, which
    is where anything checking the log against the page needs it.
    """
    name = str(actor_id or "")
    if name.startswith("m_"):
        name = name[2:]
    return name.replace("_", " ")


def esc(text) -> str:
    return html.escape(str(text if text is not None else ""))


def rupees(paise) -> str:
    return f"₹{(paise or 0) / 100:,.0f}"


def state_actors(events):
    return {e.actor_id for e in events if e.actor_id.startswith("m_")}


# --- the visual world --------------------------------------------------------
#
# A Bloomberg terminal, taken seriously rather than referenced. That means
# pure black — not charcoal, not near-black — because the whole legibility
# model depends on bright text sitting on nothing. Amber is the primary
# reading colour; blue heads every panel the way function keys do; green and
# red mean settled and refused and nothing else.
#
# Density is the point. Labels are small, uppercase and monospaced; data is
# large and bright; there is no gradient and no rounded corner, because a
# terminal earns trust by looking built to be read for eight hours rather than
# screenshotted once.
#
# Violet is reserved for the internal board and appears on no other surface,
# so the one screen merchants cannot see is the one that does not look like
# the others.
#
# Fonts are system stacks on purpose. The pages are recorded to video, often
# offline, and a webfont that fails to load mid-take is a re-shoot. The
# pairing still does real work: mono for what was recorded, serif for what was
# said.

CSS = """
:root{
  --bg:#000; --panel:#0A0A0A; --lift:#161616; --rule:#282828; --edge:#3A3A3A;
  --amber:#FFA028; --white:#EDEDED; --dim:#8E8E8E; --faint:#5C5C5C;
  --blue:#4D9EFF; --green:#26D07C; --red:#FF4B3E;
  --violet:oklch(80% .145 80);
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
  --serif:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
}
*{box-sizing:border-box}
/* The other half of the landing page's door: arriving here cross-fades in
   rather than cutting, so entering the desk reads as continuous. */
@view-transition{navigation:auto}
::view-transition-old(root),::view-transition-new(root){
  animation-duration:.34s;animation-timing-function:cubic-bezier(.16,1,.3,1)}
html,body{margin:0;height:100%}
body{background:var(--bg);color:var(--white);font-family:var(--mono);
  font-size:13px;line-height:1.45;-webkit-font-smoothing:antialiased;
  display:flex;flex-direction:column;overflow:hidden}
@media(max-width:1000px){body{overflow:auto;height:auto}}

/* --- the head ------------------------------------------------------------ */
.bar{display:flex;align-items:center;flex:none;background:var(--panel);
  border-bottom:1px solid var(--rule);flex-wrap:wrap}
.mark{background:var(--amber);color:#000;font-weight:700;letter-spacing:.16em;
  font-size:11px;padding:7px 13px;white-space:nowrap}
.stat{padding:7px 14px;border-right:1px solid var(--rule);white-space:nowrap;
  font-size:10.5px;color:var(--faint);letter-spacing:.1em;
  text-transform:uppercase}
.stat b{font-size:14px;font-weight:600;color:var(--white);letter-spacing:0;
  margin-right:6px}
.stat b.amber{color:var(--amber)}
.stat b.green{color:var(--green)}
.stat b.red{color:var(--red)}
.navs{margin-left:auto;display:flex}
.nav{appearance:none;background:none;border:0;border-left:1px solid var(--rule);
  color:var(--dim);font-family:var(--mono);font-size:11px;letter-spacing:.13em;
  text-transform:uppercase;padding:8px 16px;cursor:pointer}
.nav:hover{color:var(--white);background:var(--lift)}
.nav[aria-selected=true]{background:var(--amber);color:#000;font-weight:700}
.nav.house[aria-selected=true]{background:var(--violet)}
.nav:focus-visible{outline:2px solid var(--blue);outline-offset:-2px}
a.nav{text-decoration:none;display:inline-block}

/* Blue panel heads, the way a terminal labels a window. */
.head{background:var(--lift);border-bottom:1px solid var(--rule);
  padding:5px 12px;font-size:10px;letter-spacing:.17em;text-transform:uppercase;
  color:var(--blue);display:flex;justify-content:space-between;gap:12px;
  align-items:center;flex:none}
.head em{font-style:normal;color:var(--faint);letter-spacing:.1em}

/* --- shell --------------------------------------------------------------- */
main{flex:1;min-height:0;position:relative}
.vw{position:absolute;inset:0;display:none;flex-direction:column;overflow:auto;
  padding:14px 16px}
.vw[data-on]{display:flex}
@media(max-width:1000px){main{position:static}.vw{position:static}}

.panel{border:1px solid var(--rule);background:var(--panel);display:flex;
  flex-direction:column;min-height:0;overflow:hidden}
.panel .body{overflow:auto;padding:10px 12px;flex:1;min-height:0}
.panel .body.flush{padding:0}
@media(max-width:1000px){.panel .body{max-height:42vh}}

/* --- the picker ---------------------------------------------------------- */
.picker{display:flex;align-items:center;gap:7px;flex-wrap:wrap;
  padding:0 0 13px;border-bottom:1px solid var(--rule);margin-bottom:14px}
.picker .lab{font-size:10px;letter-spacing:.17em;text-transform:uppercase;
  color:var(--faint);margin-right:3px}
.pick,select.pick{appearance:none;background:var(--lift);color:var(--dim);
  border:1px solid var(--rule);font-family:var(--mono);font-size:11.5px;
  padding:6px 12px;cursor:pointer;letter-spacing:.05em}
.pick:hover{color:var(--white);border-color:var(--edge)}
.pick[aria-pressed=true]{background:var(--amber);color:#000;
  border-color:var(--amber);font-weight:700}
.pick:focus-visible{outline:2px solid var(--blue);outline-offset:1px}
select.pick{max-width:330px}

/* --- THE RAIL: the signature -------------------------------------------
   Every trade travels the same stations in the same order, and the order is
   the argument: the gate rules before money moves, and a lesson is filed
   after it. Each card's top edge takes the colour of what happened there, so
   the shape of a trade reads before a single word does. */
.who{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;
  padding-bottom:11px}
.who .agent{color:var(--amber);font-size:14px}
.who .verb{color:var(--faint);font-size:11px;letter-spacing:.13em;
  text-transform:uppercase}
.who .need{font-family:var(--serif);font-size:23px;color:var(--white);
  line-height:1.2}
.who .cid{color:var(--faint);font-size:10.5px;letter-spacing:.04em}
.human-tag{background:var(--amber);color:#000;font-size:9.5px;font-weight:700;
  letter-spacing:.12em;text-transform:uppercase;padding:2px 7px}

.rail{display:flex;gap:1px;flex-wrap:wrap;background:var(--rule);
  border:1px solid var(--rule)}
.stn{flex:1 1 148px;min-width:148px;max-width:260px;background:var(--panel);
  border-top:3px solid var(--edge);padding:9px 12px 13px;position:relative}
.stn[data-tone=allow]{border-top-color:var(--green)}
.stn[data-tone=deny]{border-top-color:var(--red)}
.stn[data-tone=mixed]{border-top-color:var(--amber)}
.stn .gl{position:absolute;top:8px;right:11px;font-size:15px;color:var(--faint)}
.stn[data-tone=allow] .gl{color:var(--green)}
.stn[data-tone=deny] .gl{color:var(--red)}
.stn[data-tone=mixed] .gl{color:var(--amber)}
.stn .cap{font-size:10px;letter-spacing:.16em;text-transform:uppercase;
  color:var(--blue)}
.stn .cap b{background:var(--blue);color:#000;font-weight:700;padding:0 4px;
  margin-right:6px}
/* The stamp: the event this station is accountable to. */
.stn .seq{font-size:10.5px;color:var(--amber);margin-top:3px;
  letter-spacing:.05em}
.stn .hd{font-size:16px;margin-top:8px;line-height:1.25;color:var(--white)}
.stn[data-tone=allow] .hd{color:var(--green)}
.stn[data-tone=deny] .hd{color:var(--red)}
.stn[data-tone=mixed] .hd{color:var(--amber)}
.stn .ln{font-family:var(--serif);font-size:13.5px;color:var(--dim);
  margin-top:6px;line-height:1.4;overflow-wrap:anywhere}
.stn .ln.mono{font-family:var(--mono);font-size:10.5px;color:var(--faint)}

/* --- what they said ------------------------------------------------------ */
.said{display:grid;grid-template-columns:158px 66px 1fr;gap:12px;
  padding:5px 0;align-items:baseline;border-bottom:1px solid var(--rule)}
.said .w{font-size:11px;color:var(--faint)}
.said .p{font-size:13px;color:var(--amber);text-align:right}
.said .m{font-family:var(--serif);font-size:14.5px;color:var(--white)}
@media(max-width:1000px){.said{grid-template-columns:1fr;gap:1px}}

/* --- the floor (internal) ------------------------------------------------ */
.floor{display:grid;grid-template-columns:minmax(215px,1fr) minmax(0,2.1fr);
  gap:12px;flex:1;min-height:0}
@media(max-width:1000px){.floor{grid-template-columns:1fr}}
.mgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(116px,1fr));
  gap:1px}
.m{font-size:10.5px;color:var(--faint);padding:3px 5px;display:flex;gap:6px;
  align-items:center;overflow:hidden;white-space:nowrap}
.m b{font-weight:inherit;min-width:0;overflow:hidden;text-overflow:ellipsis}
.m i{width:5px;height:5px;background:var(--rule);flex:none}
.m.act{color:var(--white);background:var(--lift)}
.m.act i{background:var(--green)}
.m.buy{color:var(--amber);background:var(--lift)}
.m.buy i{background:var(--amber)}
.m.frz{color:var(--red)}
.m.frz i{background:var(--red)}

.lrow{display:grid;grid-template-columns:46px 138px 1fr;gap:10px;
  padding:2px 11px;border-left:2px solid transparent;align-items:baseline;
  font-size:12px}
.lrow .s{color:var(--faint);font-size:10.5px}
.lrow .a{color:var(--dim);overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap}
.lrow .d{color:var(--faint);overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap}
.lrow .d b{color:var(--white);font-weight:400}
.lrow.allow{border-left-color:var(--green)}
.lrow.deny{border-left-color:var(--red)}
.lrow.amber{border-left-color:var(--amber)}
.lrow.new{background:var(--lift)}
@media(prefers-reduced-motion:no-preference){
  .lrow.new{transition:background .6s}}

.trans{display:flex;align-items:center;gap:6px;padding:0 0 11px;
  flex-wrap:wrap}
.progress{height:2px;background:var(--rule);position:relative;
  margin-bottom:12px}
.progress i{position:absolute;inset:0 auto 0 0;width:0;background:var(--amber);
  display:block;transition:width .18s linear}
.clock{margin-left:auto;font-size:11px;color:var(--faint);letter-spacing:.09em;
  text-transform:uppercase}
.clock b{color:var(--amber);font-weight:600}

/* --- the board ----------------------------------------------------------- */
.internal{border:1px solid var(--violet);margin-bottom:16px}
.internal>.hdr{background:color-mix(in oklab,var(--violet) 15%,transparent);padding:7px 13px;font-size:10px;
  letter-spacing:.16em;text-transform:uppercase;color:var(--violet);
  display:flex;gap:12px;justify-content:space-between;flex-wrap:wrap}
.internal>.hdr b{font-weight:700}
.internal>.hdr span{color:var(--faint);letter-spacing:.09em}
.crow{display:grid;grid-template-columns:30px 1fr 66px 92px 100px;gap:12px;
  padding:11px 13px;border-top:1px solid var(--rule);align-items:baseline}
.crow .rk{font-size:17px;color:var(--violet)}
.crow .nm{font-size:16px;color:var(--white)}
.crow .mv,.crow .mc,.crow .vl{font-size:13px;text-align:right}
.crow .mv{color:var(--amber)}
.crow .mc,.crow .vl{color:var(--dim)}
.crow .perf{grid-column:1/-1;margin:0 0 9px;padding:6px 10px;
  background:rgba(255,255,255,.045);border-left:2px solid var(--green);
  font-family:var(--mono);font-size:11.5px;color:var(--dim);
  letter-spacing:.01em}
.crow .perf b{color:var(--green);font-size:13px}
.crow .perf i{display:block;margin-top:3px;font-style:normal;
  font-size:10px;color:var(--faint);letter-spacing:.02em}
.crow .deriv{grid-column:2/-1;margin-top:5px;font-family:var(--mono);
  font-size:11px;color:var(--faint);letter-spacing:.01em}
.crow .deriv .evn{color:var(--amber)}
.won{margin:0 0 14px;padding:11px 14px;border-left:3px solid var(--green);
  background:rgba(38,208,124,.08);font-size:15px;color:var(--white)}
.won b{color:var(--green)}
.won i{display:block;margin-top:4px;font-style:normal;font-size:12px;
  color:var(--dim);font-family:var(--mono)}
.crow .why{grid-column:2/-1;color:var(--white);font-size:14.5px;margin-top:6px;
  line-height:1.45;font-family:var(--serif)}
.crow .src{grid-column:2/-1;margin-top:8px;display:flex;gap:5px;flex-wrap:wrap}
.crow .src a{font-size:10px;color:var(--faint);border:1px solid var(--rule);
  padding:2px 7px;text-decoration:none;max-width:230px;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.crow .src a:hover{color:var(--violet);border-color:var(--violet)}
/* What operators said, kept visibly apart from what the press said. The rule
   down the left is the whole device: it marks the sentence as a reading of
   somebody else's words rather than the desk's own finding. */
.crow .talk{grid-column:2/-1;margin-top:10px;padding-left:11px;
  border-left:2px solid var(--rule);color:var(--dim);font-size:13.5px;
  line-height:1.5}
.crow .talk b{display:block;font-size:9.5px;letter-spacing:.14em;
  text-transform:uppercase;color:var(--faint);margin-bottom:4px;
  font-weight:600;font-family:var(--mono)}
.crow .talk.blocked{color:var(--faint);font-style:italic}
.crow .thr{grid-column:2/-1;margin-top:7px;display:flex;gap:5px;
  flex-wrap:wrap;padding-left:13px}
.crow .thr a{font-size:10px;color:var(--faint);border:1px solid var(--rule);
  padding:2px 7px;text-decoration:none;max-width:250px;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.crow .thr a:hover{color:var(--amber);border-color:var(--amber)}
/* The radar. Same row shape so the eye reads it as a ranking, one clear
   band above it so nobody mistakes a count of strangers for a rupee. */
.scanband{grid-column:1/-1;margin:26px 0 10px;padding:7px 11px;
  font-family:var(--mono);font-size:10px;letter-spacing:.12em;
  text-transform:uppercase;color:#111;background:var(--amber);
  display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap}
.scanband i{font-style:normal;opacity:.72}
.crow.scan .mv{color:var(--white)}
.crow.scan .vl{font-family:var(--mono);font-size:10px;color:var(--faint)}
@media(max-width:1000px){
  .crow{grid-template-columns:24px 1fr 56px}
  .crow .mc,.crow .vl{display:none}}
.refused{padding:9px 13px;border-top:1px solid var(--rule);color:var(--red);
  font-size:12.5px}

/* --- the gate screen ----------------------------------------------------- */
.lock{position:fixed;inset:0;background:var(--bg);z-index:50;display:flex;
  align-items:center;justify-content:center;padding:24px;overflow:auto}
.lock[data-open]{display:none}
.lock .card{max-width:470px;width:100%;border:1px solid var(--rule);
  background:var(--panel)}
.lock .top{background:var(--violet);color:#000;padding:8px 14px;font-size:11px;
  letter-spacing:.16em;text-transform:uppercase;font-weight:700}
.lock .in{padding:24px}
.lock h1{font-family:var(--serif);font-size:26px;font-weight:400;margin:0 0 8px;
  color:var(--white)}
.lock p{color:var(--dim);font-size:13px;margin:0 0 18px;line-height:1.6}
.lock .row{display:flex;gap:8px}
.lock input{flex:1;background:var(--bg);border:1px solid var(--edge);
  color:var(--white);font-family:var(--mono);font-size:15px;padding:10px 12px;
  letter-spacing:.14em}
.lock input:focus{outline:none;border-color:var(--violet)}
.lock button{background:var(--violet);border:0;color:#000;
  font-family:var(--mono);font-size:11px;font-weight:700;letter-spacing:.14em;
  text-transform:uppercase;padding:0 20px;cursor:pointer}
.lock .warn{margin:18px 0 0;padding-top:14px;border-top:1px solid var(--rule);
  color:var(--faint);font-size:12px;line-height:1.6}
.lock .warn b{color:var(--amber);font-weight:600}
.lock .bad{color:var(--red);font-size:12px;margin-top:10px;min-height:16px}

/* --- try it -------------------------------------------------------------- */
.ask{display:flex;gap:8px;margin-bottom:14px;max-width:640px}
.ask input{flex:1;background:var(--panel);border:1px solid var(--rule);
  color:var(--white);font-family:var(--serif);font-size:16px;padding:10px 13px}
.ask input:focus{outline:none;border-color:var(--amber)}
.ask button{background:var(--amber);border:0;color:#000;font-family:var(--mono);
  font-size:11px;font-weight:700;letter-spacing:.13em;text-transform:uppercase;
  padding:0 20px;cursor:pointer}
#hits{min-height:132px}
.hit{display:grid;grid-template-columns:1fr auto auto;gap:14px;padding:7px 0;
  border-bottom:1px solid var(--rule);align-items:baseline}
.hit .tt{font-family:var(--serif);font-size:15px}
.hit .sl{font-size:11px;color:var(--dim)}
.hit .pr{color:var(--amber)}

/* --- shared -------------------------------------------------------------- */
h3.lede{font-family:var(--serif);font-size:23px;font-weight:400;margin:0 0 6px;
  line-height:1.3;max-width:60ch;color:var(--white)}
p.sub{margin:0 0 16px;color:var(--dim);font-size:13.5px;max-width:78ch;
  line-height:1.6}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th{text-align:left;font-size:10px;letter-spacing:.14em;text-transform:uppercase;
  color:var(--blue);font-weight:500;padding:6px 10px 6px 0;
  border-bottom:1px solid var(--rule)}
td{padding:5px 10px 5px 0;border-bottom:1px solid var(--rule);
  vertical-align:top}
td.a{color:var(--dim);font-size:11.5px;white-space:nowrap}
td.q{font-family:var(--serif);font-size:14px}
.empty{color:var(--faint);font-size:13.5px;padding:12px 0;
  font-family:var(--serif);font-style:italic}
.note{color:var(--dim);font-size:14px;border-left:2px solid var(--rule);
  padding-left:12px;margin:14px 0;font-family:var(--serif);line-height:1.55}
.note b{color:var(--white);font-weight:600}
footer{padding:10px 16px;border-top:1px solid var(--rule);color:var(--faint);
  font-size:10.5px;line-height:1.7;flex:none;background:var(--panel)}
a{color:var(--dim)}
@media(prefers-reduced-motion:reduce){*{animation:none!important;
  transition:none!important}}
"""


def _panel(title, body, note="", body_id="", flush=False) -> str:
    ident = f' id="{body_id}"' if body_id else ""
    klass = "body flush" if flush else "body"
    return (f'<section class="panel"><div class="head">{esc(title)}'
            f'<em>{esc(note)}</em></div>'
            f'<div class="{klass}"{ident}>{body}</div></section>')


def _stat(value, label, tone="") -> str:
    """A head figure. `value` may be markup — the desk's counters are spans
    the engine writes into, and every call site here supplies its own text."""
    cls = f' class="{tone}"' if tone else ""
    return f'<span class="stat"><b{cls}>{value}</b>{esc(label)}</span>'


def _footer(db_path, summary) -> str:
    """The desk footer. Names what the numbers are, not where they are stored.

    It used to print the path to the SQLite file, which reads as a demo
    artefact rather than a product — the audit claim is carried by the event
    number on every row, and that is on screen everywhere already.
    """
    return (f'<footer>Gate {summary.gate_allow} allowed / '
            f'{summary.gate_deny} refused &middot; {summary.completed} '
            f'payments completed against Razorpay &middot; '
            f'{summary.events} events on the audit trail.<br>'
            f'Every row and every station carries the event number it came '
            f'from.</footer>')


# --- the shared rail renderer ------------------------------------------------
#
# Both pages draw the same trail from the same data. The merchant's page draws
# it once and leaves it still; the desk redraws it as the tape moves.

RAIL_JS = r"""
var CAPS={wants:'wants',picked:'picked',haggled:'negotiated',gate:'the gate',
  paid:'paid',broke:'books disagree',froze:'froze',repaired:'repaired',
  remembered:'remembered'};
var GLYPH={wants:'○',picked:'→',haggled:'⇄',gate:'✓',
  paid:'₹',broke:'≠',froze:'⏸',repaired:'✓',
  remembered:'★'};
function glyph(s){
  if(s.key==='gate')return s.tone==='deny'?'✕':
    (s.tone==='mixed'?'✕→✓':'✓');
  if(s.key==='haggled'&&s.tone==='deny')return '✕';
  return GLYPH[s.key]||'';
}
function esc(t){var d=document.createElement('div');
  d.textContent=t==null?'':t;return d.innerHTML}
function drawRail(r,upto,ids){
  var stations=r.stations.filter(function(s){return s.seq<=upto});
  if(!stations.length)return false;
  document.getElementById(ids.who).innerHTML=
    '<span class="agent">'+esc(r.buyer)+'</span>'+
    '<span class="verb">'+(r.human?'typed':'wants')+'</span>'+
    '<span class="need">'+esc(r.need||'—')+'</span>'+
    (r.human?'<span class="human-tag">a person typed this</span>':'')+
    '<span class="cid">'+esc(r.corr)+'</span>';
  document.getElementById(ids.rail).innerHTML=stations.map(function(s,n){
    return '<div class="stn" data-tone="'+esc(s.tone||'')+'">'+
      '<div class="gl">'+glyph(s)+'</div>'+
      '<div class="cap"><b>'+(n+1)+'</b>'+esc(CAPS[s.key]||s.key)+'</div>'+
      '<div class="seq">event '+s.seq+'</div>'+
      '<div class="hd">'+esc(s.head)+'</div>'+
      s.lines.map(function(l,k){
        return '<div class="ln'+(k?' mono':'')+'">'+esc(l)+'</div>'}).join('')+
      '</div>'}).join('');
  if(ids.talk){
    document.getElementById(ids.talk).innerHTML=r.talk.length?
      r.talk.map(function(t){
        return '<div class="said"><span class="w">'+esc(t.who)+
          '</span><span class="p">'+esc(t.price)+'</span><span class="m">'+
          esc(t.said)+'</span></div>'}).join('')
      :'<div class="empty">No offers on this thread — the match cleared '+
       'at the asking price.</div>';
  }
  return true;
}
function wireViews(){
  [].forEach.call(document.querySelectorAll('.nav[data-view]'),function(v){
    v.addEventListener('click',function(){
      [].forEach.call(document.querySelectorAll('.nav[data-view]'),
        function(o){
          var on=o===v;
          o.setAttribute('aria-selected',on?'true':'false');
          var p=document.getElementById('vw-'+o.dataset.view);
          if(!p)return;
          if(on){p.setAttribute('data-on','');p.scrollTop=0}
          else p.removeAttribute('data-on')})})});
}
"""


def _cases(rail_map):
    """The four trades worth naming, found in the log rather than chosen.

    Found by shape so the pages still work against a different run — a
    hard-coded correlation id would make this a slideshow of one recording.
    """
    def has(rail, key, tone=None):
        return any(s["key"] == key and (tone is None or s["tone"] == tone)
                   for s in rail["stations"])

    values = list(rail_map.values())
    broke = next((r for r in values if has(r, "repaired")), None)
    capped = next((r for r in values
                   if has(r, "gate", "mixed") and has(r, "paid")), None)
    # A trade with nothing unusual about it. Asking for one with no drift AND
    # no capped gate AND a lesson finds nothing in some logs, and a missing
    # button is worse than a slightly less pristine example: the plain case is
    # the baseline every other button is read against.
    def clean(r):
        return (has(r, "paid") and not has(r, "broke")
                and not has(r, "gate", "mixed"))

    plain = (next((r for r in values if clean(r) and has(r, "remembered")),
                  None)
             or next((r for r in values if clean(r)), None)
             or next((r for r in values
                      if has(r, "paid") and not has(r, "broke")), None))
    human = (next((r for r in values if r["human"] and has(r, "paid")), None)
             or next((r for r in values if r["human"]), None))

    return [(plain, "a trade that just worked"),
            (capped, "a stranger, capped"),
            (broke, "the one that broke"),
            (human, "the one a person typed")]


# =============================================================================
#  PAGE ONE — the merchant's own dashboard
# =============================================================================
#
# A DIFFERENT WORLD ON PURPOSE. The desk is a black terminal because the people
# reading it stare at it all day and want density. A merchant opens this once a
# week to answer one question — what did my agents do with my money — and a
# trading terminal is the wrong instrument for that. So this page is light,
# roomy, and organised around the four parts of your broker rather than around
# the market.
#
# What survives from the terminal is the rule that matters: mono for what the
# system recorded, serif for what somebody said, and an event number beside
# every claim.

LIGHT_CSS = """
:root{
  /* Same OKLCH system as the front door, one world apart in lightness.
     Every soft tint is mixed from its own signal rather than eyeballed. */
  --paper:oklch(97.5% .003 255); --card:#fff;
  --line:oklch(92% .007 268); --edge:oklch(86% .01 268);
  --ink:oklch(20% .014 255); --body:oklch(42% .014 255);
  --mute:oklch(56% .022 268); --pale:oklch(68% .02 268);
  --brand:oklch(52% .17 250);
  --brandsoft:color-mix(in oklab,var(--brand) 9%,#fff);
  --money:oklch(52% .13 165);
  --moneysoft:color-mix(in oklab,var(--money) 9%,#fff);
  --warn:oklch(58% .16 55);
  --warnsoft:color-mix(in oklab,var(--warn) 9%,#fff);
  --stop:oklch(56% .19 25);
  --stopsoft:color-mix(in oklab,var(--stop) 9%,#fff);
  --violet:oklch(58% .14 78);
  --violetsoft:color-mix(in oklab,var(--violet) 9%,#fff);
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",
    sans-serif;
  --serif:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
  --shadow:0 1px 2px rgba(17,24,38,.05),0 4px 14px rgba(17,24,38,.05);
}
*{box-sizing:border-box}
@view-transition{navigation:auto}
::view-transition-old(root),::view-transition-new(root){
  animation-duration:.34s;animation-timing-function:cubic-bezier(.16,1,.3,1)}
html,body{margin:0}
body{background:var(--paper);color:var(--ink);font-family:var(--sans);
  font-size:15px;line-height:1.55;-webkit-font-smoothing:antialiased}

/* --- header -------------------------------------------------------------- */
.top{background:var(--card);border-bottom:1px solid var(--line)}
.top .in{max-width:1240px;margin:0 auto;padding:16px 22px;display:flex;
  align-items:center;gap:14px;flex-wrap:wrap}
.avatar{width:38px;height:38px;border-radius:9px;background:var(--brand);
  color:#fff;display:grid;place-items:center;font-weight:700;font-size:15px;
  flex:none;text-decoration:none}
.avatar:hover{filter:brightness(1.1)}
.whoami h1{margin:0;font-size:19px;font-weight:650;letter-spacing:-.01em;
  text-transform:capitalize}
.whoami p{margin:0;color:var(--mute);font-size:12.5px}
.switch{margin-left:auto;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
select.sw{appearance:none;background:var(--card);border:1px solid var(--edge);
  border-radius:8px;color:var(--body);font-family:var(--sans);font-size:13px;
  padding:8px 30px 8px 12px;cursor:pointer;max-width:250px;
  background-image:linear-gradient(45deg,transparent 50%,var(--mute) 50%),
    linear-gradient(135deg,var(--mute) 50%,transparent 50%);
  background-position:calc(100% - 16px) 50%,calc(100% - 11px) 50%;
  background-size:5px 5px,5px 5px;background-repeat:no-repeat}
select.sw:focus{outline:2px solid var(--brand);outline-offset:1px}
.deskbtn{background:var(--violet);color:#fff;border-radius:8px;padding:8px 14px;
  font-size:12.5px;font-weight:600;text-decoration:none;white-space:nowrap}
.deskbtn:hover{filter:brightness(1.08)}

/* --- the four figures ---------------------------------------------------- */
.figures{max-width:1240px;margin:0 auto;padding:20px 22px 0;display:grid;
  grid-template-columns:repeat(auto-fit,minmax(158px,1fr));gap:12px}
.fig{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:14px 16px;box-shadow:var(--shadow)}
.fig .v{font-family:var(--mono);font-size:25px;letter-spacing:-.02em;
  line-height:1.15;color:var(--ink)}
.fig.money .v{color:var(--money)}
.fig.warn .v{color:var(--warn)}
.fig.brand .v{color:var(--brand)}
.fig .l{font-size:12px;color:var(--mute);margin-top:4px}

/* --- tabs ---------------------------------------------------------------- */
.tabs{max-width:1240px;margin:20px auto 0;padding:0 22px;display:flex;gap:4px;
  border-bottom:1px solid var(--line);flex-wrap:wrap}
.tab{appearance:none;background:none;border:0;border-bottom:2px solid
  transparent;color:var(--mute);font-family:var(--sans);font-size:14px;
  font-weight:600;padding:11px 14px;cursor:pointer;margin-bottom:-1px}
.tab:hover{color:var(--ink)}
.tab[aria-selected=true]{color:var(--brand);border-bottom-color:var(--brand)}
.tab:focus-visible{outline:2px solid var(--brand);outline-offset:-2px;
  border-radius:6px}

.wrap{max-width:1240px;margin:0 auto;padding:22px}
.pane{display:none}
.pane[data-on]{display:block}
h2.sec{font-size:13px;font-weight:700;letter-spacing:.05em;
  text-transform:uppercase;color:var(--pale);margin:0 0 12px}
p.lede{font-size:15px;color:var(--body);margin:0 0 20px;max-width:74ch}

/* --- THE CREW: the signature -----------------------------------------------
   Your broker is not one agent, it is four parts with separate memories, and
   this is the only place a merchant ever sees that. Each card names the part,
   says in one plain line what it is for, shows what it last did IN ITS OWN
   WORDS, and ends with the one number that says whether it did its job.
   The event types feeding each card are printed on it, so the attribution can
   be checked rather than taken on trust. */
.crew{display:grid;grid-template-columns:repeat(auto-fit,minmax(258px,1fr));
  gap:14px;margin-bottom:26px}
.role{background:var(--card);border:1px solid var(--line);border-radius:14px;
  overflow:hidden;box-shadow:var(--shadow);display:flex;flex-direction:column}
.role .rh{padding:13px 16px 11px;border-bottom:1px solid var(--line);
  display:flex;align-items:center;gap:10px}
.role .dot{width:30px;height:30px;border-radius:8px;display:grid;
  place-items:center;font-size:14px;flex:none}
.role h3{margin:0;font-size:15.5px;font-weight:650;text-transform:capitalize}
.role .cnt{margin-left:auto;font-family:var(--mono);font-size:12px;
  color:var(--mute)}
.role .rb{padding:13px 16px;flex:1}
.role .job{font-size:13.5px;color:var(--mute);margin:0 0 12px}
.role .did{font-family:var(--serif);font-size:15px;color:var(--ink);
  line-height:1.45;margin:0}
.role .rf{padding:10px 16px;border-top:1px solid var(--line);
  background:var(--paper);display:flex;justify-content:space-between;gap:10px;
  align-items:center;flex-wrap:wrap}
.role .res{font-weight:650;font-size:13.5px}
.role .src{font-family:var(--mono);font-size:10px;color:var(--pale)}
.role.trader .dot{background:var(--brandsoft);color:var(--brand)}
.role.trader .res{color:var(--brand)}
.role.scout .dot{background:var(--warnsoft);color:var(--warn)}
.role.scout .res{color:var(--warn)}
.role.diplomat .dot{background:var(--moneysoft);color:var(--money)}
.role.diplomat .res{color:var(--money)}
.role.subconscious .dot{background:var(--violetsoft);color:var(--violet)}
.role.subconscious .res{color:var(--violet)}

/* --- two-up -------------------------------------------------------------- */
.duo{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(0,1fr);gap:16px}
@media(max-width:940px){.duo{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;
  box-shadow:var(--shadow);overflow:hidden}
.card>.ch{padding:12px 16px;border-bottom:1px solid var(--line);display:flex;
  justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}
.card>.ch h3{margin:0;font-size:14.5px;font-weight:650}
.card>.ch .meta{font-size:12px;color:var(--pale)}
.card>.cb{padding:14px 16px}
.card>.cb.tight{padding:6px 16px 12px}

/* --- messages ------------------------------------------------------------ */
.msg{display:grid;grid-template-columns:auto 1fr;gap:11px;padding:11px 0;
  border-bottom:1px solid var(--line);align-items:start}
.msg:last-child{border-bottom:0}
.msg .pip{width:8px;height:8px;border-radius:50%;background:var(--pale);
  margin-top:7px}
.msg.green .pip{background:var(--money)}
.msg.red .pip{background:var(--stop)}
.msg.amber .pip{background:var(--warn)}
.msg>span:last-child{display:flex;flex-direction:column;min-width:0}
.msg .kind{font-size:11px;font-weight:700;letter-spacing:.06em;
  text-transform:uppercase;color:var(--pale)}
.msg.green .kind{color:var(--money)}
.msg.red .kind{color:var(--stop)}
.msg.amber .kind{color:var(--warn)}
.msg .txt{font-family:var(--serif);font-size:14.5px;color:var(--body);
  margin-top:2px;overflow-wrap:anywhere}
.msg .ev{font-family:var(--mono);font-size:10.5px;color:var(--pale);
  margin-top:3px}
.connect{margin-top:14px;padding:12px 14px;border:1px dashed var(--edge);
  border-radius:10px;background:var(--paper);font-size:12.5px;color:var(--mute)}
.connect b{color:var(--body)}
.connect .chips{display:flex;gap:7px;margin-top:9px;flex-wrap:wrap}
.connect.synced{border-style:solid;border-color:var(--moneysoft);
  background:var(--moneysoft)}
.sheetbtn{display:inline-block;margin-top:11px;background:var(--money);
  color:#fff;border-radius:8px;padding:9px 15px;font-size:13.5px;
  font-weight:650;text-decoration:none}
.sheetbtn:hover{filter:brightness(1.08)}
.chip{background:var(--card);border:1px solid var(--edge);border-radius:7px;
  padding:5px 11px;font-size:12px;color:var(--pale);cursor:not-allowed}

/* --- catalogue ----------------------------------------------------------- */
.brief{font-family:var(--serif);font-size:16px;line-height:1.5;resize:vertical;
  min-height:76px}
.kws{display:flex;flex-wrap:wrap;gap:6px;margin-top:11px}
.kw{appearance:none;background:var(--paper);border:1px solid var(--edge);
  border-radius:999px;color:var(--body);font-family:var(--mono);
  font-size:11.5px;padding:5px 11px;cursor:pointer}
.kw:hover{border-color:var(--brand);color:var(--brand)}
.kw[aria-pressed=true]{background:var(--brand);border-color:var(--brand);
  color:#fff}
.kw:focus-visible{outline:2px solid var(--brand);outline-offset:2px}
.bkgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));
  gap:10px;margin-bottom:16px}
.bk{background:var(--paper);border-radius:9px;padding:10px 12px}
.bk .bl{display:block;font-size:11.5px;color:var(--mute)}
.bk .bv{display:block;font-family:var(--mono);font-size:17px;
  font-weight:600;margin-top:2px}
.bk:first-child .bv{color:var(--warn)}
.bk:nth-child(2) .bv{color:var(--money)}
.bk:nth-child(4) .bv{color:var(--money)}
.scrollx{overflow-x:auto;margin:0 -16px;padding:0 16px}
.scrollx table{min-width:760px}
.scrollx td{white-space:nowrap;max-width:250px;overflow:hidden;
  text-overflow:ellipsis}
.connect code{font-family:var(--mono);font-size:11.5px;background:var(--card);
  border:1px solid var(--edge);border-radius:5px;padding:1px 6px;
  overflow-wrap:anywhere}
.item{display:grid;grid-template-columns:1fr auto auto;gap:12px;padding:10px 0;
  border-bottom:1px solid var(--line);align-items:baseline}
.item:last-of-type{border-bottom:0}
.item .t{font-size:14.5px}
.item .q{font-family:var(--mono);font-size:12px;color:var(--mute)}
.item .p{font-family:var(--mono);font-size:14px;color:var(--money);
  font-weight:600}
.item .mine{font-size:10.5px;background:var(--brandsoft);color:var(--brand);
  border-radius:5px;padding:1px 7px;font-weight:700;margin-left:7px}
.addrow{display:grid;grid-template-columns:1fr 88px 96px auto;gap:8px;
  margin-top:14px}
@media(max-width:640px){.addrow{grid-template-columns:1fr}}
input.f,.ask input{font-family:var(--sans);font-size:14px;padding:9px 12px;
  border:1px solid var(--edge);border-radius:9px;background:var(--card);
  color:var(--ink);width:100%}
input.f:focus,.ask input:focus{outline:2px solid var(--brand);
  outline-offset:-1px;border-color:var(--brand)}
button.go{background:var(--brand);color:#fff;border:0;border-radius:9px;
  font-family:var(--sans);font-size:13.5px;font-weight:650;padding:9px 18px;
  cursor:pointer;white-space:nowrap}
button.go:hover{filter:brightness(1.07)}
button.go:focus-visible{outline:2px solid var(--ink);outline-offset:2px}

/* --- the money trail ----------------------------------------------------- */
.picker{display:flex;gap:8px;align-items:center;flex-wrap:wrap;
  margin-bottom:18px}
.trade{appearance:none;background:var(--card);border:1px solid var(--edge);
  border-radius:9px;color:var(--body);font-family:var(--sans);font-size:13px;
  padding:8px 13px;cursor:pointer}
.trade:hover{border-color:var(--brand);color:var(--brand)}
.trade[aria-pressed=true]{background:var(--brand);border-color:var(--brand);
  color:#fff;font-weight:600}
.trade:focus-visible{outline:2px solid var(--brand);outline-offset:2px}
.trade b{font-family:var(--mono);font-weight:600;margin-left:9px;
  color:var(--money)}
.trade[aria-pressed=true] b{color:#fff}

.who{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;
  margin-bottom:14px}
.who .agent{font-family:var(--mono);font-size:13.5px;color:var(--brand)}
.who .verb{font-size:11.5px;letter-spacing:.11em;text-transform:uppercase;
  color:var(--pale)}
.who .need{font-family:var(--serif);font-size:23px;line-height:1.2}
.who .cid{font-family:var(--mono);font-size:10.5px;color:var(--pale)}
.human-tag{background:var(--brand);color:#fff;font-size:10px;font-weight:700;
  letter-spacing:.08em;text-transform:uppercase;padding:2px 8px;
  border-radius:5px}

.rail{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));
  gap:12px}
.stn{background:var(--card);border:1px solid var(--line);border-radius:12px;
  border-top:3px solid var(--edge);padding:13px 15px 15px;position:relative;
  box-shadow:var(--shadow)}
.stn[data-tone=allow]{border-top-color:var(--money)}
.stn[data-tone=deny]{border-top-color:var(--stop)}
.stn[data-tone=mixed]{border-top-color:var(--warn)}
.stn .gl{position:absolute;top:11px;right:14px;font-size:16px;color:var(--pale)}
.stn[data-tone=allow] .gl{color:var(--money)}
.stn[data-tone=deny] .gl{color:var(--stop)}
.stn[data-tone=mixed] .gl{color:var(--warn)}
.stn .cap{font-size:11px;font-weight:700;letter-spacing:.08em;
  text-transform:uppercase;color:var(--pale)}
.stn .cap b{background:var(--pale);color:#fff;border-radius:4px;padding:0 5px;
  margin-right:7px;font-weight:700}
/* The stamp: the event this station is accountable to. */
.stn .seq{font-family:var(--mono);font-size:11px;color:var(--brand);
  margin-top:4px}
.stn .hd{font-family:var(--mono);font-size:17px;margin-top:9px;line-height:1.25}
.stn[data-tone=allow] .hd{color:var(--money)}
.stn[data-tone=deny] .hd{color:var(--stop)}
.stn[data-tone=mixed] .hd{color:var(--warn)}
.stn .ln{font-family:var(--serif);font-size:14px;color:var(--mute);
  margin-top:7px;line-height:1.4;overflow-wrap:anywhere}
.stn .ln.mono{font-family:var(--mono);font-size:11px;color:var(--pale)}

.said{display:grid;grid-template-columns:160px 68px 1fr;gap:12px;padding:9px 0;
  border-bottom:1px solid var(--line);align-items:baseline}
.said:last-child{border-bottom:0}
.said .w{font-family:var(--mono);font-size:11.5px;color:var(--pale)}
.said .p{font-family:var(--mono);font-size:14px;color:var(--money);
  text-align:right;font-weight:600}
.said .m{font-family:var(--serif);font-size:15px;color:var(--body)}
@media(max-width:820px){.said{grid-template-columns:1fr;gap:2px}}

/* --- shared -------------------------------------------------------------- */
.hit{display:grid;grid-template-columns:1fr auto auto;gap:14px;padding:10px 0;
  border-bottom:1px solid var(--line);align-items:baseline}
.hit:last-child{border-bottom:0}
.hit .tt{font-family:var(--serif);font-size:15px}
.hit .sl{font-family:var(--mono);font-size:11.5px;color:var(--mute)}
.hit .pr{font-family:var(--mono);font-size:14px;color:var(--money);
  font-weight:600}
.ask{display:flex;gap:9px;margin-bottom:16px;max-width:660px}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{text-align:left;font-size:11px;letter-spacing:.06em;text-transform:uppercase;
  color:var(--pale);font-weight:700;padding:7px 10px 7px 0;
  border-bottom:1px solid var(--line)}
td{padding:7px 10px 7px 0;border-bottom:1px solid var(--line);
  vertical-align:top;color:var(--body)}
td.a{font-family:var(--mono);font-size:11.5px;color:var(--mute);
  white-space:nowrap}
td.q{font-family:var(--serif);font-size:14.5px;color:var(--ink)}
.empty{color:var(--pale);font-family:var(--serif);font-style:italic;
  font-size:14.5px;padding:10px 0}
.note{background:var(--brandsoft);border-radius:10px;padding:12px 15px;
  font-size:13.5px;color:var(--body);margin:16px 0;line-height:1.55}
.note b{color:var(--ink)}
footer{max-width:1240px;margin:0 auto;padding:22px;color:var(--pale);
  font-size:11.5px;font-family:var(--mono);line-height:1.75;
  border-top:1px solid var(--line)}
a{color:var(--brand)}
@media(prefers-reduced-motion:reduce){*{transition:none!important}}
"""


MERCHANT_JS = r"""<script>
(function(){
var M=JSON.parse(document.getElementById('mkt').textContent);
""" + RAIL_JS + r"""
var IDS={who:'who',rail:'rail',talk:'talk'};

/* --- tabs ------------------------------------------------------------- */
[].forEach.call(document.querySelectorAll('.tab'),function(t){
  t.addEventListener('click',function(){
    [].forEach.call(document.querySelectorAll('.tab'),function(o){
      var on=o===t;
      o.setAttribute('aria-selected',on?'true':'false');
      var p=document.getElementById('pn-'+o.dataset.pane);
      if(!p)return;
      if(on)p.setAttribute('data-on','');else p.removeAttribute('data-on')})})});

/* --- the money trail -------------------------------------------------- */
function show(corr){
  if(!M.rails[corr])return;
  drawRail(M.rails[corr],1e9,IDS);
  [].forEach.call(document.querySelectorAll('.trade'),function(b){
    b.setAttribute('aria-pressed',b.dataset.corr===corr?'true':'false')});
}
[].forEach.call(document.querySelectorAll('.trade'),function(b){
  b.addEventListener('click',function(){show(b.dataset.corr)})});
var first=document.querySelector('.trade');
if(first)show(first.dataset.corr);

/* --- switching merchant ------------------------------------------------
   Each merchant is a separate generated page, so this is a plain
   navigation. Nothing is filtered client-side, which means what you see
   was scoped to your actor before it ever reached the browser. */
var sw=document.getElementById('sw');
if(sw)sw.addEventListener('change',function(){location.href=sw.value});

/* --- ask for something ------------------------------------------------
   Searches the REAL catalogue read out of the log. It does not pretend to
   run an agent in the browser: the honest claim is that a person reaches
   the same order book, and the evidence is the recorded thread below. */
var q=document.getElementById('q'),hits=document.getElementById('hits');
if(q){
  var search=function(){
    var terms=(q.value||q.placeholder).toLowerCase().split(/\s+/)
      .filter(function(w){return w.length>3});
    var found=M.cat.map(function(c){
      var t=c.title.toLowerCase(),n=0;
      terms.forEach(function(w){if(t.indexOf(w)>=0)n++});
      return[c,n]}).filter(function(p){return p[1]>0})
      .sort(function(a,b){return b[1]-a[1]}).slice(0,6);
    hits.innerHTML=found.length?found.map(function(p){var c=p[0];
      return '<div class="hit"><span class="tt">'+esc(c.title)+
        '</span><span class="sl">'+esc(c.seller)+'</span><span class="pr">₹'+
        (c.price/100).toFixed(2)+'</span></div>'}).join('')
      :'<div class="empty">Nothing on the book matches that. The agents only '+
       'stock what merchants have actually listed.</div>';
  };
  document.getElementById('go').addEventListener('click',search);
  q.addEventListener('keydown',function(e){if(e.key==='Enter')search()});
}

/* --- your agent's brief ------------------------------------------------
   Tapping a setting appends the word the agent actually reads, not a label
   for it — so what is in the box is exactly what reaches the prompt. */
var brief=document.getElementById('brief');
if(brief){
  var KEY_B='brief:'+M.actor;
  try{var saved=localStorage.getItem(KEY_B); if(saved!==null)brief.value=saved}
  catch(e){}
  var sync=function(){
    var have=brief.value.toLowerCase();
    [].forEach.call(document.querySelectorAll('.kw'),function(b){
      b.setAttribute('aria-pressed',
        have.indexOf(b.dataset.kw)>=0?'true':'false')});
    try{localStorage.setItem(KEY_B,brief.value)}catch(e){}
  };
  brief.addEventListener('input',sync);
  [].forEach.call(document.querySelectorAll('.kw'),function(b){
    b.addEventListener('click',function(){
      var kw=b.dataset.kw, v=brief.value.trim();
      if(v.toLowerCase().indexOf(kw)>=0){
        brief.value=v.replace(new RegExp('\\s*,?\\s*'+kw,'i'),'')
                     .replace(/^\s*,\s*/,'').trim();
      }else{
        brief.value=v?v+', '+kw:kw;
      }
      sync(); brief.focus();
    })});
  sync();
}

/* --- your catalogue ---------------------------------------------------
   Added items live in THIS BROWSER only, and the card says so. The event
   log is sealed and read-only from here; a real listing posts an ASK to
   the order book through the exchange, which a static page cannot do and
   should not pretend to. */
var KEY='catalogue:'+M.actor;
function saved(){
  try{return JSON.parse(localStorage.getItem(KEY)||'[]')}catch(e){return[]}
}
function store(rows){
  try{localStorage.setItem(KEY,JSON.stringify(rows))}catch(e){}
}
function renderAdded(){
  var box=document.getElementById('added');
  if(!box)return;
  var rows=saved();
  box.innerHTML=rows.map(function(r,n){
    return '<div class="item"><span class="t">'+esc(r.title)+
      '<span class="mine">added by you</span></span>'+
      '<span class="q">'+esc(r.qty)+' units</span>'+
      '<span class="p">₹'+esc(r.price)+'</span></div>'}).join('');
}
var add=document.getElementById('add');
if(add){
  renderAdded();
  add.addEventListener('click',function(){
    var t=document.getElementById('i-title').value.trim();
    var qy=document.getElementById('i-qty').value.trim();
    var pr=document.getElementById('i-price').value.trim();
    if(!t)return document.getElementById('i-title').focus();
    var rows=saved();
    rows.push({title:t,qty:qy||'—',price:pr||'—'});
    store(rows); renderAdded();
    document.getElementById('i-title').value='';
    document.getElementById('i-qty').value='';
    document.getElementById('i-price').value='';
  });
}
})();
</script>"""


def _crew(view) -> str:
    marks = {"trader": "₹", "scout": "◎", "diplomat": "◇", "subconscious": "✦"}
    out = ""
    for role in ("trader", "scout", "diplomat", "subconscious"):
        info = view["roles"][role]
        out += (
            f'<article class="role {role}"><div class="rh">'
            f'<span class="dot">{marks[role]}</span>'
            f"<h3>{esc(role)}</h3>"
            f'<span class="cnt">{info["count"]} '
            f'{"action" if info["count"] == 1 else "actions"}</span></div>'
            f'<div class="rb"><p class="job">{esc(info["blurb"])}</p>'
            f'<p class="did">{esc(info["last"])}</p></div>'
            f'<div class="rf"><span class="res">{esc(info["result"])}</span>'
            f'<span class="src">{esc(" ".join(info["types"][:3]))}</span>'
            f"</div></article>")
    return out


def build_merchant(db_path: str, actor_id: str, roster) -> str:
    summary, _trades, events = load(db_path)
    rail_map = rails(events)
    view = merchant_view(events, actor_id, rail_map)
    books = entries_for(events, actor_id)

    mine = [rail_map[c] for c in view["corrs"] if c in rail_map]
    payload = json.dumps({
        "rails": {r["corr"]: r for r in mine},
        "cat": catalogue(events),
        "actor": actor_id,
    }, separators=(",", ":"))

    options = "".join(
        f'<option value="{esc(_page_name(a))}"'
        f'{" selected" if a == actor_id else ""}>'
        f'{esc(who(a).title())}</option>'
        for a in roster)

    # TWO TRADES FOR THE SAME THING NEED TELLING APART. Three buttons all
    # reading "cold brew concentrate for a twelve store" name the need and
    # hide the only thing that differs — what happened to the money.
    mine = sorted(mine, key=lambda r: len(r["stations"]), reverse=True)
    trades = "".join(
        f'<button class="trade" data-corr="{esc(r["corr"])}" '
        f'aria-pressed="false">{esc(_clip_words(r["need"], 34))}'
        f'<b>{rupees(r["amount"]) if r.get("amount") else "no deal"}</b>'
        f"</button>"
        for r in mine)

    initial = (view["name"][:1] or "?").upper()

    figures = (
        '<div class="figures">'
        f'<div class="fig money"><div class="v">'
        f'{rupees(view["spent_paise"])}</div>'
        f'<div class="l">committed by your agents</div></div>'
        f'<div class="fig"><div class="v">{len(mine)}</div>'
        f'<div class="l">trades on your book</div></div>'
        f'<div class="fig warn"><div class="v">{view["refused"]}</div>'
        f'<div class="l">refused by the gate</div></div>'
        f'<div class="fig brand"><div class="v">'
        f'{view["points"]:,}</div><div class="l">points earned</div></div>'
        "</div>")

    agents_pane = (
        '<p class="lede">Your broker is four parts with separate memories. '
        'Three of them act; the fourth only watches and remembers. Each card '
        'shows what that part last did, in its own words, and the event types '
        'it was read from.</p>'
        f'<div class="crew">{_crew(view)}</div>'
        '<div class="duo">'
        + _brief_card(brief_for(events, actor_id))
        + _books_card(books)
        + _catalogue_card(view) + "</div>")

    money_pane = (
        '<p class="lede">Every trade your agents made, from what you needed to '
        'the payment id Razorpay gave back. Each step carries the event number '
        'it came from, so you can look any of it up in the log yourself.</p>'
        f'<div class="picker">{trades}</div>'
        '<div class="who" id="who"></div>'
        '<div class="rail" id="rail"></div>'
        '<div style="height:20px"></div>'
        '<section class="card"><div class="ch"><h3>What they said to each '
        'other</h3><span class="meta">every offer, quoted</span></div>'
        '<div class="cb" id="talk"></div></section>'
    ) if mine else (
        '<div class="empty">This merchant has no trades on its book in this '
        'run.</div>')

    ask_pane = (
        '<p class="lede">Type what you need in plain words. This writes a '
        'descriptive bid to the same order book your agents use &mdash; you '
        'approve it, and the gate still decides. A person saying yes is '
        'consent, not permission, and it does not raise a spending cap.</p>'
        '<div class="ask"><input id="q" type="text" class="f" '
        'aria-label="what do you need" '
        'placeholder="biodegradable mailers under 22 rupees a unit">'
        '<button class="go" id="go">Search the book</button></div>'
        '<section class="card"><div class="ch"><h3>What is actually on the '
        'book</h3><span class="meta">read from the log</span></div>'
        '<div class="cb" id="hits"><div class="empty">Type a need and press '
        'search. This reads the real catalogue and refuses when nothing '
        'matches.</div></div></section>'
        '<div style="height:16px"></div>'
        + _human_thread(events))

    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{esc(view["name"].title())} — your agents</title>'
        f"<style>{LIGHT_CSS}</style></head><body>"

        '<header class="top"><div class="in">'
        f'<a class="avatar" href="index.html" '
        f'aria-label="back to the front">{esc(initial)}</a>'
        f'<div class="whoami"><h1>{esc(view["name"])}</h1>'
        f'<p>{esc(who(actor_id))} &middot; {esc(view["plan"])} plan &middot; '
        f'represented by four agents</p></div>'
        '<div class="switch">'
        f'<select class="sw" id="sw" aria-label="view another merchant">'
        f"{options}</select></div></div></header>"

        + figures

        + '<div class="tabs" role="tablist">'
          '<button class="tab" data-pane="agents" aria-selected="true">'
          'My agents</button>'
          '<button class="tab" data-pane="money" aria-selected="false">'
          'Where the money went</button>'
          '<button class="tab" data-pane="ask" aria-selected="false">'
          'Ask for something</button></div>'

        + f'<div class="wrap">'
          f'<section class="pane" id="pn-agents" data-on>{agents_pane}</section>'
          f'<section class="pane" id="pn-money">{money_pane}</section>'
          f'<section class="pane" id="pn-ask">{ask_pane}</section></div>'

        + f'<footer>Every figure on this page belongs to '
          f'{esc(view["name"])} alone, and carries the event number behind '
          f'it. Your books sync to your own Google Sheet.</footer>'

        + f'<script type="application/json" id="mkt">{payload}</script>'
        + MERCHANT_JS + "</body></html>"
    )


def _sheet_link(actor_id: str):
    """The live link to this merchant's own tab, when the sync has run.

    Two things have to be true: a sheet id in the environment, and a tab id
    recorded by the last push. Without both there is no honest link to draw,
    so the card explains how to get one instead of rendering a dead button.

    The sheet id is read from the environment at build time and never stored
    beside the pages.
    """
    import json
    import os
    import pathlib as _p

    # Load .env here rather than relying on the caller's environment. A
    # plain rebuild would otherwise silently drop every sheet link and the
    # pages would quietly regress to the setup instructions.
    try:
        from dotenv import load_dotenv
        load_dotenv(_p.Path(__file__).resolve().parents[2] / ".env")
    except ImportError:
        pass
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        return None
    tabs_file = _p.Path("runs/books/sheet-tabs.json")
    if not tabs_file.exists():
        return None
    try:
        gid = json.loads(tabs_file.read_text()).get(actor_id)
    except (ValueError, OSError):
        return None
    if gid is None:
        return None
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={gid}"


BRIEF_KEYWORDS = (
    ("aggressive", "Open well below the ask and concede slowly"),
    ("fair", "Open near a realistic price and expect the same back"),
    ("patient", "Take the time to reach a good price"),
    ("decisive", "Close quickly when terms are fair"),
    ("price_first", "Price matters most"),
    ("quality_first", "Specification matters more than price"),
    ("delivery_first", "Delivery certainty matters more than the last few percent"),
    ("walk_early", "Walk away readily when the gap is not closing"),
    ("persistent", "Keep working a deal while there is any prospect"),
    ("loyal", "Prefer counterparties we have dealt with well before"),
    ("explorer", "Actively seek out counterparties we have never traded with"),
    ("cautious", "Start small with anyone unproven, and scale on evidence"),
)


def _brief_card(brief) -> str:
    """How this merchant told its agent to behave.

    The words on the left are the ones that actually reached the Trader, the
    Scout and the Diplomat as standing instructions — the same string, run
    through `exchange.agents.mandate`. The note under the box is the part that
    matters: a brief sets priorities, never permissions.
    """
    chips = "".join(
        f'<button class="kw" type="button" data-kw="{esc(k)}" '
        f'title="{esc(why)}">{esc(k.replace("_", " "))}</button>'
        for k, why in BRIEF_KEYWORDS)
    current = brief or ""
    state = ("Your agent ran on this brief."
             if brief else
             "This run predates briefs, so your agent used its defaults.")
    return (
        '<section class="card"><div class="ch"><h3>How your agent '
        'behaves</h3>'
        f'<span class="meta">{esc(state)}</span></div><div class="cb">'
        '<textarea id="brief" class="f brief" rows="3" '
        'aria-label="your standing brief" '
        'placeholder="patient, quality_first, and we would rather walk than '
        'deal with anyone who has missed a date">'
        f'{esc(current)}</textarea>'
        f'<div class="kws">{chips}</div>'
        '<div class="connect">Type in plain words, or tap a setting to add '
        'it. Your brief reaches the three parts of your agent that act, as '
        'standing instructions. <b>It sets priorities, never permissions</b> '
        '&mdash; no wording here can raise a spending limit, and an agent '
        'that asks for more than it is allowed is refused and the refusal is '
        'recorded. Edits are kept in this browser; the run above is '
        'sealed.</div></div></section>')


def _books_card(books) -> str:
    """The books, kept automatically, in the columns an accountant reads.

    This is the same grid `scripts.market.sheets` pushes to Google Sheets —
    one function produces both, so the page and the spreadsheet can never
    disagree about what a merchant bought.
    """
    # FORMATTED HERE AND NOWHERE ELSE. `summary()` yields real numbers
    # because that is what a spreadsheet needs in a cell; a page that wants
    # "11,570" must not push "11,570" into the sheet, where it stops being
    # something you can sum.
    def money(value):
        if isinstance(value, (int, float)):
            return f"{'-' if value < 0 else ''}₹{abs(value):,.0f}"
        return str(value)

    figures = "".join(
        f'<div class="bk"><span class="bl">'
        f'{esc(label.replace(" (₹)", ""))}</span>'
        f'<span class="bv">{esc(money(value))}</span></div>'
        for label, value in books.summary()[1:6])

    if books.entries:
        head = "".join(f"<th>{esc(c.replace('_inr', ' ₹').replace('_', ' '))}"
                       f"</th>" for c in COLUMNS[:8])
        rows = "".join(
            "<tr>" + "".join(
                # Column 2 is the counterparty, which is an actor id in the
                # ledger and a business name on screen.
                f'<td class="{"a" if n in (0, 7) else ""}">'
                f'{esc(who(cell) if n == 2 else cell)}</td>'
                for n, cell in enumerate(entry.row()[:8]))
            + "</tr>"
            for entry in books.entries)
        table = f'<div class="scrollx"><table><tr>{head}</tr>{rows}</table></div>'
    else:
        table = ('<div class="empty">No trades on your books yet. Every buy '
                 'and sell your agents make lands here on its own.</div>')

    return (
        '<section class="card"><div class="ch"><h3>Your books</h3>'
        f'<span class="meta">{len(books.entries)} entries &middot; kept '
        f'automatically</span></div><div class="cb">'
        f'<div class="bkgrid">{figures}</div>{table}'
        + _sheet_sync(books.actor_id)
        + "</div></section>")


def _sheet_sync(actor_id: str) -> str:
    """Either the live link, or exactly what is missing. Never a dead button."""
    link = _sheet_link(actor_id)
    if link:
        return (
            '<div class="connect synced">'
            '<b>These books are synced.</b> The grid above is exactly what is '
            'in the sheet &mdash; your own tab, replaced on every run, because '
            'the books are a projection of a log that is the only thing '
            'allowed to accumulate.'
            f'<a class="sheetbtn" href="{esc(link)}" target="_blank" '
            f'rel="noopener">Open your tab in Google Sheets &rarr;</a></div>')
    return (
        '<div class="connect"><b>These books are ready to sync to a Google '
        'Sheet.</b> The grid above is exactly what gets pushed &mdash; one tab '
        'per merchant, replaced on every run. Run '
        '<code>python -m scripts.market.sheets --sheet</code> with a '
        'service-account key in <code>.env</code> and this card becomes a link '
        'straight to your tab. Without one, the same command writes CSVs that '
        'open in Sheets as they are.</div>')


def _catalogue_card(view) -> str:
    rows = "".join(
        f'<div class="item"><span class="t">{esc(row["title"])}</span>'
        f'<span class="q">{esc(row.get("qty") or "—")} units</span>'
        f'<span class="p">'
        f'{rupees(row["price"]) if row.get("price") else "—"}</span></div>'
        for row in view["catalogue"]) or (
        '<div class="empty">You have nothing listed yet.</div>')

    return (
        '<section class="card"><div class="ch"><h3>Your catalogue</h3>'
        f'<span class="meta">{len(view["catalogue"])} listed on the '
        f'exchange</span></div><div class="cb tight">'
        f"{rows}<div id=\"added\"></div>"
        '<div class="addrow">'
        '<input class="f" id="i-title" placeholder="what you sell" '
        'aria-label="what you sell">'
        '<input class="f" id="i-qty" placeholder="qty" aria-label="quantity" '
        'inputmode="numeric">'
        '<input class="f" id="i-price" placeholder="₹ / unit" '
        'aria-label="price per unit" inputmode="numeric">'
        '<button class="go" id="add">Add</button></div>'
        '<div class="connect">Items you add are saved <b>in this browser '
        'only</b>. The event log behind this page is sealed and read-only, so '
        'a static page cannot post to the order book &mdash; in the running '
        'exchange, adding a line here posts an ASK that other merchants&rsquo; '
        'agents can find.</div>'
        "</div></section>")


def _human_thread(events) -> str:
    rows = "".join(
        f'<tr><td class="a">{r["seq"]}</td><td class="a">{esc(r["actor"])}</td>'
        f'<td class="a">{esc(r["type"])}</td>'
        f'<td class="q">{esc(r["says"])}</td></tr>'
        for r in storefront(events)["rows"])
    return (
        '<section class="card"><div class="ch">'
        '<h3>A purchase a person actually made</h3>'
        '<span class="meta">the same events as an agent&rsquo;s trade</span>'
        '</div><div class="cb">'
        + (f"<table>{rows}</table>" if rows
           else '<div class="empty">No human purchase in this log.</div>')
        + "</div></section>")


def _clip_words(text, limit):
    text = str(text or "").strip() or "a purchase"
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def _page_name(actor_id: str) -> str:
    return f"m-{actor_id[2:].replace('_', '-')}.html"


# =============================================================================
#  PAGE TWO — Razorpay's own desk, behind a gate
# =============================================================================

DESK_JS = r"""<script>
(function(){
var M=JSON.parse(document.getElementById('mkt').textContent);
""" + RAIL_JS + r"""
/* ONE SPEED. The floor runs at the rate a person can actually read it;
   a viewer fiddling with a multiplier is a viewer not watching the market. */
var rows=M.rows,rails=M.rails,i=0,timer=null,shown=null;
var TICK=320;
var paid=0,money=0,ok=0,no=0,fixed=0;
var $=function(id){return document.getElementById(id)};
var reduce=matchMedia('(prefers-reduced-motion: reduce)').matches;
var IDS={who:'who',rail:'rail'};
wireViews();

/* --- the floor ------------------------------------------------------- */
function merchant(id,cls){
  var el=document.querySelector('[data-m="'+CSS.escape(id)+'"]');
  if(!el)return;
  el.classList.remove('act','frz','buy');
  el.classList.add(cls);
  if(cls!=='frz')setTimeout(function(){el.classList.remove(cls)},2400);
}
function step(){
  if(i>=rows.length){stop();label('replay',false);return}
  var r=rows[i++];
  var l=$('ledger');
  var el=document.createElement('div');
  el.className='lrow '+(r.tone||'')+' new';
  el.innerHTML='<span class="s">'+r.seq+'</span><span class="a">'+
    esc(r.actor)+'</span><span class="d"><b>'+esc(r.says)+'</b> '+
    esc(r.detail)+'</span>';
  l.appendChild(el);
  setTimeout(function(){el.classList.remove('new')},480);
  while(l.children.length>160)l.removeChild(l.firstChild);
  l.scrollTop=l.scrollHeight;
  $('seq').textContent=i;
  $('bar').style.width=(100*i/rows.length).toFixed(2)+'%';

  /* THE HEAD ADDS UP AS YOU WATCH. Every counter is a running total of
     events already played, so it agrees with the ledger beside it at every
     instant — not a final figure parked at the top pretending to be live. */
  if(r.type==='SETTLEMENT_COMPLETED'){
    paid++; $('n-paid').textContent=paid;
    if(r.actor==='accountant'){fixed++;$('n-fixed').textContent=fixed}
  }else if(r.type==='SETTLEMENT_INITIATED'){
    var m=/^([\d,]+\.\d\d)/.exec(r.detail);
    if(m){money+=parseFloat(m[1].replace(/,/g,''));
      $('n-money').textContent='₹'+Math.round(money).toLocaleString()}
  }else if(r.type==='POLICY_DECIDED'){
    if(/^ALLOW/.test(r.detail)){ok++;$('n-ok').textContent=ok}
    else{no++;$('n-no').textContent=no}
  }

  if(r.type==='ACTOR_FROZEN')merchant(r.actor,'frz');
  else if(r.actor&&r.actor.indexOf('m_')===0)merchant(r.actor,'act');

  /* The rail follows the tape: it shows whichever trade is happening now. */
  var rl=rails[r.corr];
  if(rl){
    var key=r.corr+':'+r.seq;
    if(key!==shown&&drawRail(rl,r.seq,IDS)){
      shown=key;
      if(rl.buyer)merchant(rl.buyer,'buy');
    }
  }
}
function label(t,p){var b=$('pp');b.textContent=t;
  b.setAttribute('aria-pressed',p?'true':'false')}
function play(){stop();timer=setInterval(step,TICK);label('pause',true)}
function stop(){if(timer)clearInterval(timer);timer=null}
function toggle(){if(timer){stop();label('play',false)}
  else if(i>=rows.length)restart();else play()}
function restart(){
  stop();$('ledger').innerHTML='';shown=null;
  paid=money=ok=no=fixed=0;
  ['n-paid','n-ok','n-no','n-fixed'].forEach(function(k){
    $(k).textContent='0'});
  $('n-money').textContent='₹0';
  i=0;$('seq').textContent=0;$('bar').style.width='0%';play();
}
$('pp').addEventListener('click',toggle);
$('rs').addEventListener('click',restart);
document.addEventListener('keydown',function(e){
  if(/^(INPUT|TEXTAREA)$/.test(e.target.tagName||''))return;
  if(e.key===' '){e.preventDefault();toggle()}});

/* --- the gate ---------------------------------------------------------
   A stage prop, and the screen says so. These are static files; a
   client-side check is not access control, and dressing one up as if it
   were would be the only dishonest thing on the page. */
var opened=false;
function unlock(e){
  if(e&&e.isTrusted===false)return;
  if($('code').value.trim().toLowerCase()!=='razorpay'){
    $('bad').textContent='Not that one. The passcode is on this screen.';
    return;
  }
  if(opened)return;
  opened=true;
  $('lock').setAttribute('data-open','');
  if(reduce){while(i<rows.length)step();stop();label('replay',false)}
  else play();
}
$('enter').addEventListener('click',unlock);
$('code').addEventListener('keydown',function(e){
  if(e.key==='Enter'&&e.isTrusted!==false)unlock(e)});
})();
</script>"""


def build_desk(db_path: str) -> str:
    summary, _trades, events = load(db_path)
    rail_map = rails(events)
    merchants = sorted(state_actors(events))
    rows = tape(events)

    payload = json.dumps({"rows": rows, "rails": rail_map},
                         separators=(",", ":"))

    mgrid = "".join(f'<div class="m" data-m="{esc(m)}"><i></i><b>{esc(who(m))}</b></div>'
                    for m in merchants)
    live = (
        '<div class="trans">'
        '<button class="pick" id="pp" aria-pressed="true">pause</button>'
        '<button class="pick" id="rs">restart</button>'
        f'<span class="clock">event <b id="seq">0</b> of {len(rows)}</span>'
        "</div>"
        '<div class="progress"><i id="bar"></i></div>'
        '<div class="who" id="who"></div>'
        '<div class="rail" id="rail"></div>'
        '<div style="height:12px"></div>'
        '<div class="floor">'
        + _panel("the agents", f'<div class="mgrid">{mgrid}</div>',
                 note=f"{summary.merchants} trading")
        + _panel("the ledger", "", note="every event, in order",
                 body_id="ledger", flush=True)
        + "</div>")

    boardv = (
        '<h3 class="lede">Only the payment processor sees the whole book.</h3>'
        '<p class="sub">A merchant knows its own sales. Razorpay knows which '
        'campaigns are climbing across every client, and can rank them before '
        'any one client could. The ranking is arithmetic over this log; the '
        'sentence under each row comes from the press and carries its '
        'source.</p>'
        + _board_html(board(events), auction(events), summary,
                      radar(events), performance(events)))

    lock = (
        '<div class="lock" id="lock"><div class="card">'
        '<div class="top">Razorpay internal &middot; staff only</div>'
        '<div class="in">'
        "<h1>The desk</h1>"
        '<p>The live floor and the campaign board. Neither is a '
        'merchant&rsquo;s to see: the board ranks what is climbing across the '
        'whole client base, which is the one thing only the payment processor '
        'can know.</p>'
        '<div class="row">'
        '<input id="code" type="text" aria-label="passcode" '
        'placeholder="razorpay" autocomplete="off" spellcheck="false" '
        'data-lpignore="true" data-1p-ignore data-form-type="other">'
        '<button id="enter">enter</button></div>'
        '<div class="bad" id="bad"></div>'
        '<p class="warn">This gate is a <b>stage prop</b>. The page is a '
        'static file and the check runs in your browser, so anyone can read '
        'past it with the developer tools &mdash; it marks an audience, it '
        'does not enforce one. Real access control belongs on a server, and '
        'saying so is cheaper than pretending otherwise on a page whose whole '
        'claim is that you can check it.<br><br>'
        'The passcode is <b>razorpay</b>.</p>'
        "</div></div></div>")

    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Razorpay desk — internal</title>'
        f"<style>{CSS}</style></head><body>"

        + lock

        + '<div class="bar"><span class="mark">RAZORPAY DESK</span>'
        + _stat(summary.merchants, "agents")
        + _stat('<span id="n-paid">0</span>', "confirmed", "green")
        + _stat('<span id="n-money">₹0</span>', "committed", "amber")
        + _stat('<span id="n-ok">0</span>', "allowed", "green")
        + _stat('<span id="n-no">0</span>', "refused", "red")
        + _stat('<span id="n-fixed">0</span>', "repaired")
        + '<span class="navs">'
          '<button class="nav" data-view="live" aria-selected="true">'
          'live floor</button>'
          '<button class="nav house" data-view="board" aria-selected="false">'
          'the board</button>'
          '<a class="nav" href="how-to.html">how to read this</a>'
          '<a class="nav" href="replay.html">&larr; exchange</a>'
          "</span></div>"

        + f'<main><section class="vw" id="vw-live" data-on>{live}</section>'
          f'<section class="vw" id="vw-board">{boardv}</section></main>'

        + _footer(db_path, summary)
        + f'<script type="application/json" id="mkt">{payload}</script>'
        + DESK_JS + "</body></html>"
    )


def _derivation(row, perf=None) -> str:
    """The arithmetic behind the multiple, and the event it was published as.

    It also carries what the campaign actually collected, which used to be a
    two-line green strip of its own. That strip spent most of its space on a
    caveat about the payment harness — "the run pays one per merchant" — which
    is a fact about how the demo was run and not about the market, and it
    printed an average order that simply repeated the total whenever there
    was one payment. One number survived that block and it now sits on this
    line.

    Every other figure on this desk carries the event number it came from.
    These rows did not, which left the most load-bearing numbers on the page
    as the only ones a reader had to take on trust. And "2.0x" alone says
    nothing about what doubled, so the two amounts it was computed from are
    printed beside it and anyone can do the division.
    """
    early, late = row.get("early_paise", 0), row.get("late_paise", 0)
    if early > 0:
        sum_ = f'grew from {rupees(early)} to {rupees(late)}'

    else:
        sum_ = 'started at nothing, so there is no multiple to report'

    cash = perf or {}
    money = (f' &middot; {rupees(cash["revenue_paise"])} collected'
             if cash.get("revenue_paise") else "")
    stopped = (f' &middot; {cash["stopped"]} stopped by the gate'
               if cash.get("stopped") else "")
    return (
        f'<div class="deriv">{sum_} &middot; '
        f'{row.get("settled", 0)} of {row.get("attempts", 0)} attempts '
        f'settled{money}{stopped} &middot; '
        f'<span class="evn">event {row.get("seq", "?")}</span></div>')


def _radar_html(scan) -> str:
    """Campaigns the outside world is reacting to, kept visibly apart.

    Under the same heading these would read as one list, and they are not one
    kind of thing: the rows above are arithmetic over settled trades, these
    are a count of strangers talking. The band across the top says which is
    which, because a reader who cannot tell them apart has been handed a
    tweet dressed as a payment.
    """
    if not scan or not scan.get("rows"):
        return ""
    rows = ""
    for row in scan["rows"]:
        evidence = "".join(
            f'<a href="{esc(e["url"])}" target="_blank" rel="noopener">'
            f'{esc(("r/" + e["community"]) if e["source"] == "reddit" else "x")}'
            f'{" &middot; " + str(e["score"]) + "&#9650;" if e.get("score") else ""}'
            f'</a>'
            for e in row.get("evidence", [])[:6] if e.get("url"))
        rows += (
            f'<div class="crow scan"><span class="rk">{row["rank"]}</span>'
            f'<span class="nm">{esc(row["campaign"])}</span>'
            f'<span class="mv">{row["heat"]}</span>'
            f'<span class="mc">{row["threads"]} in {row["spread"]}</span>'
            f'<span class="vl">{esc("/".join(row.get("sources", [])))}</span>'
            f'<div class="thr">{evidence}</div></div>')
    return (
        '<div class="scanband">what the outside world is reacting to '
        '&mdash; counted from public posts, never from a settled trade'
        '<i>heat &middot; threads in communities &middot; where</i></div>'
        + rows)


def _talk_html(row) -> str:
    """The Reddit reading, or an honest note about why there isn't one.

    A row with nothing here would read as "we looked and the category is
    quiet", which is a claim. The desk only gets to make that claim when it
    actually looked, so a blocked read says so in its own words.
    """
    note = (row.get("discussion") or "").strip()
    if not note:
        return ""
    label = ("could not be read" if row.get("discussion_blocked")
             else "what operators are saying")
    cls = " blocked" if row.get("discussion_blocked") else ""
    threads = "".join(
        f'<a href="{esc(t["url"])}" target="_blank" rel="noopener">'
        f'r/{esc(t["subreddit"])} &middot; {esc(t["title"][:52])}</a>'
        for t in row.get("threads", []) if t.get("url"))
    return (f'<div class="talk{cls}"><b>{label}</b>{esc(note)}</div>'
            + (f'<div class="thr">{threads}</div>' if threads else ""))


def _board_html(desk, sale, summary, scan=None, perf=None) -> str:
    """Razorpay's internal board, then the auction that sells a piece of it.

    Violet appears on no other surface, and the header says who may read
    this, because the separation between what the house sees and what a
    merchant may buy is the product — not a detail of the presentation.
    """
    if desk:
        rows = ""
        for row in desk["rows"]:
            movement = (f"{row['movement']:.1f}&times;" if row["movement"]
                        else "new")
            sources = "".join(
                f'<a href="{esc(s["url"])}" target="_blank" rel="noopener">'
                f'{esc(s["publisher"] or s["title"][:40])}</a>'
                for s in row.get("sources", [])[:6])
            rows += (
                f'<div class="crow">'
                f'<span class="rk">{row["rank"]}</span>'
                f'<span class="nm">{esc(row["campaign"])}</span>'
                f'<span class="mv">{movement}</span>'
                f'<span class="mc">{row["merchants"]} merchants</span>'
                f'<span class="vl">{rupees(row["value_paise"])}</span>'
                + _derivation(row, (perf or {}).get(row['campaign']))
                + f'<div class="why">{esc(row.get("driver", ""))}</div>'
                f'<div class="src">{sources}</div>'
                + _talk_html(row) + '</div>')
        # The refusal note belongs to the board it refused FROM. Appended
        # after the radar it read as though two of the radar's campaigns had
        # been kept off, which is a different claim about a different set of
        # facts.
        refused = desk["refused"]
        rows += (
            f'<div class="refused">{len(refused)} campaigns refused a place on '
            f'this board — fewer distinct merchants than the floor of '
            f'{refused[0].get("floor")} allows. A floor nobody can see is '
            f'indistinguishable from no floor.</div>') if refused else ""
        rows += _radar_html(scan)
        refusal = ""
        board_html = (
            '<div class="internal"><div class="hdr">'
            "<b>Trending client campaigns</b>"
            # Three provenances, named, because the whole claim of this board
            # is that you can tell which figure came from where. Saying only
            # "the public press" stopped being true once the discussion was
            # attached, and an unnamed source is the one a reader assumes was
            # invented.
            '<span>ranking computed from the log &middot; '
            'press and merchant discussion sourced separately</span></div>'
            f"{rows}{refusal}</div>")
    else:
        board_html = ('<div class="empty">No campaign board published. Run '
                      'scripts.market.research over this log.</div>')

    # NO AUCTION. It was a mechanism looking for a use: a coffee roaster has
    # no reason to bid against a clothing brand for apparel benchmarks. The
    # board is published to every business on the plan that includes it.
    return board_html + (
        '<div class="note">Published to every business on a plan that '
        'includes the market layer, and refreshed as the market trades. No '
        'figure appears unless at least three businesses stand behind '
        'it.</div>')


# =============================================================================
#  THE FRONT DOOR
# =============================================================================
#
# THIS PAGE SELLS THE LEVERAGE, NOT THE MACHINERY. An earlier version proved
# the technology worked — machines negotiate, money moves, here is the audit
# trail — and a merchant reading it learned nothing about what it would get.
# The proof still has to be here, because the claim is extraordinary, but it
# is evidence underneath an argument rather than the argument itself.
#
# THE ARGUMENT. A shop trading alone is one shop. Every other business on
# Razorpay is already a supplier, a buyer, or a lesson, and Razorpay already
# sits between all of them — the network exists, nobody has switched it on.
# An agent that can reach it buys better, finds counterparties nobody
# introduced, keeps the books that fall out of the trading, and bids for what
# is working elsewhere. And when a merchant's own win is the thing being
# sold, that merchant is paid for it.
#
# EVERY FIGURE ON THIS PAGE IS READ FROM THE LOG. There is no illustrative
# number and no rounded-up claim: a marketing page for a product whose entire
# proposition is "you can check this" cannot be the one surface that invents
# something. Where a number would be persuasive but is not in the log, the
# sentence is written without it.
#
# THE PAGE IS SPLIT INTO THE TWO WORLDS IT LEADS TO. Dark is the machine and
# house side, light is the merchant side, and each door sits inside its own
# world so clicking through is continuous rather than a cut.


def _font_data_uri() -> str:
    """Fraunces, instanced and subset, inlined so the page needs no network."""
    import base64
    import pathlib

    path = (pathlib.Path(__file__).resolve().parents[2]
            / "assets" / "fonts" / "fraunces-display.woff2")
    if not path.exists():
        return ""
    return ("data:font/woff2;base64,"
            + base64.b64encode(path.read_bytes()).decode())


# Icons are drawn, one stroke weight, no glyph substitutes.
ICONS = {
    "ledger": ('<path d="M4 3h11a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H4z"/>'
               '<path d="M4 3v18"/><path d="M8 8h6M8 12h6M8 16h3"/>'),
    "shield": ('<path d="M12 3l7 3v6c0 4.4-2.9 8.3-7 9.5C7.9 20.3 5 16.4 5 12'
               'V6z"/><path d="M9 12l2 2 4-4"/>'),
    "chart": '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    "arrow": '<path d="M5 12h13M12 6l6 6-6 6"/>',
    "swap": ('<path d="M4 8h13l-3-3M20 16H7l3 3"/>'),
    "link": ('<path d="M9.5 14.5l5-5"/>'
             '<path d="M11 6.5l1.6-1.6a4 4 0 0 1 5.7 5.7L16.6 12"/>'
             '<path d="M13 17.5l-1.6 1.6a4 4 0 0 1-5.7-5.7L7.4 12"/>'),
    "gavel": ('<path d="M3 21h9"/><path d="M6.5 17.5l7-7"/>'
              '<path d="M11 6l7 7"/><path d="M13.5 3.5l7 7"/>'
              '<path d="M9.5 8.5l6 6"/>'),
}


def _icon(name: str, size: int = 20) -> str:
    return (f'<svg class="ic" width="{size}" height="{size}" viewBox="0 0 24 24"'
            f' fill="none" stroke="currentColor" stroke-width="1.5"'
            f' stroke-linecap="round" stroke-linejoin="round"'
            f' aria-hidden="true">{ICONS[name]}</svg>')


def _short(name: str, limit: int = 17) -> str:
    """A label the ring can hold without leaving the frame."""
    return name if len(name) <= limit else name[:limit - 1] + "\u2026"


def _network_data(events, rail_map, roster):
    """The real trading graph: who found whom, and what they paid.

    THE LEVERAGE, MADE TOUCHABLE. A merchant reading a sentence about network
    effects learns nothing. A merchant that clicks its own name and watches
    lines fire out to counterparties it never introduced itself to has
    understood the product. Every edge below is a trade that happened; there
    are no illustrative links.

    Positions are computed here rather than in the browser so the layout is
    identical on every render and in every screenshot.
    """
    from exchange.books import entries_for

    edges = sorted({(r["buyer"], s["head"])
                    for r in rail_map.values()
                    for s in r["stations"]
                    if s["key"] == "picked" and s["head"].startswith("m_")})
    degree: dict[str, int] = {}
    for a, b in edges:
        degree[a] = degree.get(a, 0) + 1
        degree[b] = degree.get(b, 0) + 1

    # Alphabetical order puts the Bangalore cafés beside each other and the
    # suppliers beside each other, so the chords read as a market rather than
    # as noise. It is also deterministic, which a random layout is not.
    ring = [m for m in roster]
    n = len(ring)
    cx = cy = 300.0
    # THE LABELS ARE PART OF THE DRAWING. At radius 232 a name like
    # "bl electronic city" anchored at 549 and ran to roughly 650 — well past
    # the 600 viewBox — so it spilled off the plate. The ring is sized so the
    # longest label still lands inside the frame.
    radius = 166.0
    nodes = []
    at = {}
    for i, actor in enumerate(ring):
        angle = (i / n) * math.tau - math.pi / 2
        x = cx + radius * math.cos(angle)
        y = cy + radius * math.sin(angle)
        at[actor] = (x, y)
        lx = cx + (radius + 13) * math.cos(angle)
        ly = cy + (radius + 13) * math.sin(angle)
        nodes.append({
            "id": actor,
            "label": _short(who(actor)),
            "x": round(x, 1), "y": round(y, 1),
            "lx": round(lx, 1), "ly": round(ly, 1),
            "rot": round(math.degrees(angle) + (180 if math.cos(angle) < 0
                                                else 0), 2),
            "anchor": "end" if math.cos(angle) < 0 else "start",
            "deg": degree.get(actor, 0),
        })

    # Chords bend toward the middle, so the eye follows a relationship across
    # the market instead of around its edge.
    links = []
    for a, b in edges:
        (x1, y1), (x2, y2) = at[a], at[b]
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        qx, qy = cx + (mx - cx) * 0.28, cy + (my - cy) * 0.28
        links.append({"a": a, "b": b,
                      "d": f"M{x1:.1f},{y1:.1f} Q{qx:.1f},{qy:.1f} "
                           f"{x2:.1f},{y2:.1f}"})

    trades = {}
    for actor in roster:
        rows = entries_for(events, actor).entries
        if not rows:
            continue
        trades[actor] = [{
            "with": who(e.counterparty),
            "dir": e.direction,
            "item": _clip_words(e.item, 40),
            "amt": f"₹{e.amount_inr:,.0f}",
        } for e in rows[:4]]

    return {"nodes": nodes, "links": links, "trades": trades,
            "busiest": max(degree, key=degree.get) if degree else None}


def _network_html(net) -> str:
    if not net["nodes"]:
        return ""
    links = "".join(
        f'<path class="lk" data-a="{esc(l["a"])}" data-b="{esc(l["b"])}" '
        f'd="{l["d"]}"/>' for l in net["links"])
    nodes = "".join(
        f'<g class="nd" data-id="{esc(n["id"])}" tabindex="0" role="button" '
        f'aria-label="{esc(n["label"])}, {n["deg"]} trading partners">'
        f'<circle cx="{n["x"]}" cy="{n["y"]}" r="{3.6 + min(n["deg"], 6) * .7:.1f}"/>'
        f'<circle class="hit" cx="{n["x"]}" cy="{n["y"]}" r="15"/>'
        f'<text x="{n["lx"]}" y="{n["ly"]}" text-anchor="{n["anchor"]}" '
        f'transform="rotate({n["rot"]} {n["lx"]} {n["ly"]})">'
        f'{esc(n["label"])}</text></g>'
        for n in net["nodes"])
    return (
        '<div class="net">'
        '<svg viewBox="0 0 600 600" class="graph" role="img" '
        'aria-label="Trading relationships between merchants">'
        f'<g class="links">{links}</g><g class="nodes">{nodes}</g></svg>'
        '<aside class="dossier" id="dossier">'
        '<div class="dh"><span class="who" id="dwho">the whole market</span>'
        '<span class="deg" id="ddeg"></span></div>'
        '<div class="db" id="dbody"></div>'
        '<p class="hint" id="dhint">Hover any merchant. These are real '
        'counterparties, found by agents with no introduction.</p>'
        "</aside></div>")


NET_CSS = """
/* --- THE SIGNATURE: the network, touchable ------------------------------- */
/* The instruments sit in one dark plate, so the machinery reads as a thing
   set into the page rather than as a second page. */
.net{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(0,1fr);
  gap:clamp(18px,2.4vw,34px);align-items:center;background:var(--void);
  border:1px solid var(--line);border-radius:18px;
  padding:clamp(18px,2.4vw,30px);
  box-shadow:0 26px 60px -30px color-mix(in oklab,var(--ink) 50%,transparent)}
@media(max-width:900px){.net{grid-template-columns:1fr}}
.graph{width:100%;height:auto;overflow:visible;display:block}
.graph .lk{fill:none;stroke:oklch(31% .017 255);stroke-width:1;
  transition:stroke .17s,stroke-width .17s,opacity .17s}
.graph.on .lk{opacity:.28}
.graph .lk.hot{stroke:var(--ember);stroke-width:1.7;opacity:1}
.graph .nd circle{fill:oklch(42% .018 255);transition:fill .17s,r .17s}
.graph .nd .hit{fill:transparent;cursor:pointer}
.graph .nd text{font-family:var(--mono);font-size:9.6px;fill:oklch(70% .015 255);
  transition:fill .17s;pointer-events:none}
.graph.on .nd{opacity:.38;transition:opacity .17s}
.graph .nd.hot,.graph .nd.near{opacity:1}
.graph .nd.hot circle{fill:var(--ember)}
.graph .nd.hot text{fill:var(--bright)}
.graph .nd.near circle{fill:var(--mint)}
.graph .nd.near text{fill:var(--mid)}
.graph .nd:focus{outline:none}
.graph .nd:focus-visible circle{stroke:var(--bright);stroke-width:2}

.dossier{border:1px solid var(--line);border-radius:12px;background:var(--deep);
  min-height:274px;display:flex;flex-direction:column}
.dossier .dh{display:flex;align-items:baseline;gap:10px;padding:13px 16px;
  border-bottom:1px solid var(--line)}
.dossier .who{font-family:var(--mono);font-size:14px;color:var(--ember)}
.dossier .deg{margin-left:auto;font-family:var(--mono);font-size:11px;
  color:var(--low)}
.dossier .db{padding:6px 16px;flex:1}
.dossier .hint{margin:0;padding:12px 16px 16px;font-size:13px;
  color:var(--low);line-height:1.5;border-top:1px solid var(--line)}
.tr{display:grid;grid-template-columns:auto 1fr auto;gap:11px;padding:9px 0;
  border-bottom:1px solid var(--line);align-items:baseline;font-size:13px}
.tr:last-child{border-bottom:0}
.tr .d{font-family:var(--mono);font-size:10px;letter-spacing:.09em;
  text-transform:uppercase;color:var(--low)}
.tr .n{color:var(--mid);overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap}
.tr .a{font-family:var(--mono);color:var(--ember);
  font-variant-numeric:tabular-nums}

/* --- the playable auction ------------------------------------------------ */
.auction{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.15fr);
  gap:clamp(18px,2.4vw,34px);align-items:stretch;background:var(--void);
  border:1px solid var(--line);border-radius:18px;
  padding:clamp(18px,2.4vw,30px);
  box-shadow:0 26px 60px -30px color-mix(in oklab,var(--ink) 50%,transparent)}
@media(max-width:900px){.auction{grid-template-columns:1fr}}
.lot{border:1px solid color-mix(in oklab,var(--iris) 45%,transparent);
  border-radius:12px;padding:20px 22px 22px;background:var(--iris-wash)}
.lot .lb{font-family:var(--mono);font-size:10px;letter-spacing:.16em;
  text-transform:uppercase;color:var(--iris)}
.lot p{font-family:var(--disp);font-size:20px;line-height:1.32;margin:12px 0 0;
  letter-spacing:-.018em;color:var(--bright)}
.lot .src{display:block;margin-top:14px;font-family:var(--mono);font-size:11px;
  color:var(--low);line-height:1.6}
.play label{display:block;font-size:13px;color:var(--mid);margin-bottom:9px}
.bidrow{display:flex;gap:9px}
.bidrow input{flex:1;background:var(--deep);border:1px solid var(--edge);
  border-radius:10px;color:var(--bright);font-family:var(--mono);font-size:17px;
  padding:12px 14px;min-width:0}
.bidrow input:focus{outline:none;border-color:var(--iris)}
.bidrow button{background:var(--iris);border:0;border-radius:10px;color:var(--void);
  font-family:var(--sans);font-size:14px;font-weight:650;padding:0 20px;
  cursor:pointer;white-space:nowrap;
  transition:transform .2s cubic-bezier(.16,1,.3,1),filter .2s}
.bidrow button:hover{filter:brightness(1.08)}
.bidrow button:active{transform:scale(.985);transition-duration:.09s}
.verdict{min-height:22px;margin:13px 0 0;font-size:15px;line-height:1.5;
  color:var(--mid)}
.verdict b{color:var(--mint)}
.verdict i{font-style:normal;color:var(--flare)}
.envs{list-style:none;margin:14px 0 0;padding:0;display:grid;gap:1px;
  background:var(--line);border:1px solid var(--line);border-radius:11px;
  overflow:hidden}
.env{display:grid;grid-template-columns:auto 1fr auto;gap:12px;
  padding:10px 14px;background:var(--deep);align-items:center;
  font-family:var(--mono);font-size:13px}
.env .seal{width:7px;height:7px;border-radius:1px;background:var(--edge);
  transition:background .3s}
.env .bw,.env .ba{opacity:0;transition:opacity .35s}
.env .bw{color:var(--mid)}
.env .ba{color:var(--bright);font-variant-numeric:tabular-nums}
.env.open .bw,.env.open .ba{opacity:1}
.env.open .seal{background:var(--low)}
.env.won{background:var(--mint-wash)}
.env.won .seal{background:var(--mint)}
.env.won .ba{color:var(--mint)}
.env.mine{background:var(--iris-wash)}
.env.mine .seal{background:var(--iris)}
.env.mine .bw{color:var(--iris)}
.env.mine.won{background:linear-gradient(90deg,var(--iris-wash),
  var(--mint-wash))}
.env.mine.won .ba{color:var(--mint)}
@media(prefers-reduced-motion:reduce){
  .env .bw,.env .ba,.graph .lk,.graph .nd{transition-duration:.01ms!important}}
"""


NET_JS = r"""
/* --- the network ------------------------------------------------------- */
var NET=JSON.parse(document.getElementById('net').textContent);
var graph=document.querySelector('.graph');
if(graph){
  var byId={}; NET.nodes.forEach(function(n){byId[n.id]=n});
  var partners={};
  NET.links.forEach(function(l){
    (partners[l.a]=partners[l.a]||[]).push(l.b);
    (partners[l.b]=partners[l.b]||[]).push(l.a);
  });
  var dwho=document.getElementById('dwho'),ddeg=document.getElementById('ddeg'),
      dbody=document.getElementById('dbody'),dhint=document.getElementById('dhint');
  var tour=null,taken=false;

  function clear(){
    graph.classList.remove('on');
    [].forEach.call(graph.querySelectorAll('.hot,.near'),function(el){
      el.classList.remove('hot','near')});
  }
  function focusOn(id){
    clear();
    graph.classList.add('on');
    var mates=partners[id]||[];
    [].forEach.call(graph.querySelectorAll('.lk'),function(p){
      if(p.dataset.a===id||p.dataset.b===id)p.classList.add('hot')});
    [].forEach.call(graph.querySelectorAll('.nd'),function(g){
      if(g.dataset.id===id)g.classList.add('hot');
      else if(mates.indexOf(g.dataset.id)>=0)g.classList.add('near');
    });
    var node=byId[id];
    dwho.textContent=node.label;
    ddeg.textContent=mates.length+(mates.length===1?' partner':' partners');
    var rows=NET.trades[id]||[];
    dbody.innerHTML=rows.length?rows.map(function(t){
      return '<div class="tr"><span class="d">'+esc(t.dir)+'</span>'+
        '<span class="n">'+esc(t.item)+'</span>'+
        '<span class="a">'+esc(t.amt)+'</span></div>'}).join('')
      :'<div class="tr"><span class="n">No settled trades on this '+
       'merchant&rsquo;s book yet.</span></div>';
    dhint.textContent=rows.length
      ? 'Counterparties its agent found on its own. Nobody made an introduction.'
      : 'Its agent posted and negotiated, but nothing cleared this run.';
  }
  function stopTour(){if(tour){clearInterval(tour);tour=null}taken=true}

  [].forEach.call(graph.querySelectorAll('.nd'),function(g){
    g.addEventListener('pointerenter',function(){stopTour();focusOn(g.dataset.id)});
    g.addEventListener('focus',function(){stopTour();focusOn(g.dataset.id)});
  });
  graph.addEventListener('pointerleave',function(){if(taken)clear()});

  /* It tours itself until somebody takes over, so the section is alive
     before it is touched — and it never fights a visitor for control. */
  if(!matchMedia('(prefers-reduced-motion: reduce)').matches){
    var linked=NET.nodes.filter(function(n){return n.deg>0}),k=0;
    var start=function(){
      if(taken)return;
      focusOn(linked[k++%linked.length].id);
      tour=setInterval(function(){
        if(taken||document.hidden)return;
        focusOn(linked[k++%linked.length].id);
      },1150);
    };
    var seen=new IntersectionObserver(function(es){
      es.forEach(function(e){if(e.isIntersecting){start();seen.disconnect()}})
    },{threshold:.35});
    seen.observe(graph);
  }else{
    focusOn(NET.busiest);
  }
}

/* --- the sealed auction ------------------------------------------------ */
var place=document.getElementById('place');
if(place){
  var envs=[].slice.call(document.querySelectorAll('.env'));
  var input=document.getElementById('bid'),
      verdict=document.getElementById('verdict'),
      list=document.getElementById('envs');
  place.addEventListener('click',function(){
    var mine=parseInt(input.value,10);
    if(isNaN(mine)||mine<0){input.focus();
      verdict.textContent='Put a number in first — any number of points.';
      return}

    list.innerHTML='';
    var all=envs.map(function(e){
      return {who:e.dataset.who,amt:+e.dataset.amt,mine:false}});
    all.push({who:'you',amt:mine,mine:true});
    all.sort(function(a,b){return b.amt-a.amt});

    /* Second price, applied to the visitor's bid exactly as it was applied
       to the eight real ones: the winner pays what the runner-up bid. */
    var pays=all[1].amt, iWon=all[0].mine;
    all.forEach(function(b,i){
      var li=document.createElement('li');
      li.className='env'+(i===0?' won':'')+(b.mine?' mine':'');
      li.innerHTML='<span class="seal"></span><span class="bw">'+esc(b.who)+
        '</span><span class="ba">'+b.amt+'</span>';
      list.appendChild(li);
      setTimeout(function(){li.classList.add('open')},90+i*85);
    });
    setTimeout(function(){
      verdict.innerHTML=iWon
        ? 'You won it \u2014 and you pay <b>'+pays+'</b>, not your '+mine+
          '. The runner-up sets the price, so bidding what it is genuinely '+
          'worth to you always pays off.'
        : '<i>'+esc(all[0].who)+'</i> took it at '+all[0].amt+
          '. Bid what it is genuinely worth to you and the rule protects '+
          'you either way \u2014 you never overpay to win.';
    },90+all.length*85);
  });
  input.addEventListener('keydown',function(e){
    if(e.key==='Enter')place.click()});
}
"""


LANDING_CSS = """
@font-face{
  font-family:'Fraunces Display';
  src:url(__FONT__) format('woff2');
  font-weight:400 900;font-style:normal;font-display:swap;
}
:root{
  /* THE PAGE IS LIGHT AND THE MACHINES ARE DARK.
     Every rejected version shared one shape — a dark page with a bright
     accent — which is the developer-tool default. This is a payments
     product for shopkeepers, read on a phone in daylight; Razorpay's own
     site is light. So the page is paper and the machinery is inset into it
     in dark panels, which is also what the product actually is: ordinary
     business on top, agents underneath, visibly.

     OKLCH throughout, so every step is perceptually even and a tint is a
     calculation rather than a guess.

     Ground is ledger paper with a blue cast, not cream. Ink is a
     fountain-pen blue-black, not neutral charcoal. */
  /* Manila notepad. Warm, slightly uneven, with the grain of real stock
     rather than a flat wash — the texture is generated, so the page still
     opens from disk with nothing to fetch. */
  --paper:oklch(97.3% .012 86); --card:oklch(98.4% .009 87);
  --pline:oklch(90% .014 84); --pedge:oklch(82% .018 82);
  --ink:oklch(22% .014 72); --body:oklch(41% .017 76);
  --soft:oklch(50% .019 78); --faint:oklch(58% .021 80);

  /* Four signals and no decoration. Blue works, green settles, red
     refuses, gold marks what is internal. */
  --blue:oklch(52% .18 252); --green:oklch(56% .14 155);
  --red:oklch(55% .19 27); --gold:oklch(58% .14 82);

  /* The instruments: dark panels the page sets into itself. */
  --void:oklch(19% .028 255); --deep:oklch(23% .03 255);
  --line:oklch(31% .028 255); --edge:oklch(41% .03 255);
  --bright:oklch(96% .006 255); --mid:oklch(76% .016 255);
  --low:oklch(64% .018 255);
  /* On a dark instrument the signals lift a step to stay legible. */
  --ember:oklch(74% .15 250); --mint:oklch(80% .15 162);
  --flare:oklch(70% .19 25); --iris:oklch(86% .16 92);

  /* Graph paper, and the one thing yellow genuinely does on paper. */
  --hi:oklch(89% .17 96);
  --iris-wash:color-mix(in oklab,var(--iris) 14%,transparent);
  --mint-wash:color-mix(in oklab,var(--mint) 13%,transparent);
  --disp:'Fraunces Display',"Iowan Old Style",Georgia,serif;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
/* Clicking a door cross-fades into the page behind it, so entering the
   merchant world or the desk reads as continuous rather than as a cut. */
@view-transition{navigation:auto}
::view-transition-old(root),::view-transition-new(root){
  animation-duration:.34s;animation-timing-function:cubic-bezier(.16,1,.3,1)}
body{margin:0;color:var(--ink);font-family:var(--sans);font-size:16px;
  line-height:1.6;-webkit-font-smoothing:antialiased;
  background-color:var(--paper);
  background-image:
    url("data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%27%20width%3D%27200%27%20height%3D%27200%27%3E%3Cfilter%20id%3D%27f%27%3E%3CfeTurbulence%20type%3D%27fractalNoise%27%20baseFrequency%3D%27.95%27%20numOctaves%3D%273%27%20stitchTiles%3D%27stitch%27%2F%3E%3CfeColorMatrix%20type%3D%27saturate%27%20values%3D%270%27%2F%3E%3CfeComponentTransfer%3E%3CfeFuncA%20type%3D%27discrete%27%20tableValues%3D%271%27%2F%3E%3C%2FfeComponentTransfer%3E%3C%2Ffilter%3E%3Crect%20width%3D%27200%27%20height%3D%27200%27%20filter%3D%27url%28%2523f%29%27%20opacity%3D%27.4%27%2F%3E%3C%2Fsvg%3E"),
    url("data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%27%20width%3D%27840%27%20height%3D%27840%27%3E%3Cfilter%20id%3D%27c%27%3E%3CfeTurbulence%20type%3D%27fractalNoise%27%20baseFrequency%3D%27.005%20.009%27%20numOctaves%3D%276%27%20seed%3D%2711%27%20stitchTiles%3D%27stitch%27%2F%3E%3CfeColorMatrix%20type%3D%27saturate%27%20values%3D%270%27%2F%3E%3CfeComponentTransfer%3E%3CfeFuncA%20type%3D%27discrete%27%20tableValues%3D%271%27%2F%3E%3C%2FfeComponentTransfer%3E%3C%2Ffilter%3E%3Crect%20width%3D%27840%27%20height%3D%27840%27%20filter%3D%27url%28%2523c%29%27%20opacity%3D%27.78%27%2F%3E%3C%2Fsvg%3E"),
    url("data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%27%20width%3D%271400%27%20height%3D%271400%27%3E%3Cfilter%20id%3D%27d%27%3E%3CfeTurbulence%20type%3D%27fractalNoise%27%20baseFrequency%3D%27.0016%20.0032%27%20numOctaves%3D%274%27%20seed%3D%275%27%20stitchTiles%3D%27stitch%27%2F%3E%3CfeColorMatrix%20type%3D%27saturate%27%20values%3D%270%27%2F%3E%3CfeComponentTransfer%3E%3CfeFuncA%20type%3D%27discrete%27%20tableValues%3D%271%27%2F%3E%3C%2FfeComponentTransfer%3E%3C%2Ffilter%3E%3Crect%20width%3D%271400%27%20height%3D%271400%27%20filter%3D%27url%28%2523d%29%27%20opacity%3D%27.5%27%2F%3E%3C%2Fsvg%3E");
  background-size:200px 200px,840px 840px,1400px 1400px;
  background-blend-mode:soft-light,soft-light,soft-light;
  background-attachment:fixed}
::selection{background:var(--blue);color:#fff}
:focus-visible{outline:2px solid var(--blue);outline-offset:3px}
html{scrollbar-color:var(--pedge) var(--paper)}
h1,h2,h3{font-family:var(--disp);font-weight:600;letter-spacing:-.028em;
  margin:0;text-wrap:balance}
.ic{flex:none}
.num{font-variant-numeric:tabular-nums}

.band{padding:clamp(60px,8vw,110px) clamp(20px,5vw,64px)}
.in{max-width:1180px;margin:0 auto}
.dark,.light{background:transparent;color:var(--ink)}

/* --- hero ----------------------------------------------------------------- */
.hero{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(0,.95fr);
  gap:clamp(30px,4vw,60px);align-items:center;padding-top:clamp(20px,4vw,44px)}
@media(max-width:960px){.hero{grid-template-columns:1fr}}
.hero h1{font-size:clamp(38px,5.1vw,66px);line-height:1.02}
.hero h1 em{font-style:italic;color:var(--ink);position:relative;
  background-image:linear-gradient(transparent 58%,var(--hi) 58%,
    var(--hi) 92%,transparent 92%);
  background-repeat:no-repeat;background-size:0 100%;
  animation:mark .9s .35s cubic-bezier(.16,1,.3,1) forwards}
@keyframes mark{to{background-size:100% 100%}}
.hero .say{margin:24px 0 0;color:var(--body);font-size:clamp(16px,1.4vw,18px);
  max-width:52ch;line-height:1.62}
.hero .say b{color:var(--ink);font-weight:650}
.tape{margin-top:30px;padding-top:18px;border-top:1px solid var(--pline);
  font-family:var(--mono);font-size:12px;color:var(--soft);display:flex;
  flex-wrap:wrap;gap:6px 20px}
.tape b{color:var(--ink);font-weight:650}
.tape .m{color:var(--green)}

/* --- the live negotiation: proof, sitting under the claim ----------------- */
.deal{background:var(--deep);border:1px solid var(--line);border-radius:14px;
  overflow:hidden;
  box-shadow:0 26px 60px -28px color-mix(in oklab,var(--ink) 55%,transparent),
    0 2px 0 rgba(255,255,255,.04) inset}
.deal .dh{display:flex;align-items:center;gap:10px;padding:12px 16px;
  border-bottom:1px solid var(--line);font-family:var(--mono);font-size:10.5px;
  letter-spacing:.15em;text-transform:uppercase;color:var(--low)}
.deal .dh .dot{width:6px;height:6px;border-radius:50%;background:var(--mint)}
.deal .dh .want{margin-left:auto;color:var(--mid);letter-spacing:.04em;
  text-transform:none;font-size:11.5px;overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap;max-width:52%}
/* The gap between the two sides, narrowing in real time. A negotiation IS a
   spread closing, and the offers alone never showed it. */
.spread{display:grid;grid-template-columns:auto 1fr auto;gap:12px;
  align-items:center;padding:11px 16px;border-bottom:1px solid var(--line);
  font-family:var(--mono)}
.spread .lab{font-size:10px;letter-spacing:.15em;text-transform:uppercase;
  color:var(--low)}
.spread .track{height:3px;background:oklch(25% .015 255);border-radius:2px;
  overflow:hidden;position:relative}
.spread .track i{position:absolute;inset:0 auto 0 0;width:100%;display:block;
  background:linear-gradient(90deg,var(--flare),var(--iris));
  transition:width .62s cubic-bezier(.16,1,.3,1),background .4s}
.spread.closed .track i{background:var(--mint)}
.spread .val{font-size:14px;color:var(--ember);min-width:5.4ch;
  text-align:right;transition:color .4s;font-variant-numeric:tabular-nums}
.spread.closed .val{color:var(--mint)}
.feed{padding:14px 16px;height:clamp(288px,40vh,364px);display:flex;
  flex-direction:column;gap:9px;justify-content:flex-end;overflow:hidden}
.line{display:grid;grid-template-columns:1fr auto;gap:14px;align-items:baseline;
  padding:9px 12px;border-radius:9px;background:oklch(23% .015 255);
  border:1px solid transparent}
.line.b{background:oklch(21.5% .015 255)}
.line .who{font-family:var(--mono);font-size:10.5px;color:var(--low);
  display:block;margin-bottom:3px}
.line .said{font-size:13.5px;color:var(--mid);line-height:1.42;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;
  overflow:hidden}
.line .px{font-family:var(--mono);font-size:19px;color:var(--bright);
  font-variant-numeric:tabular-nums;white-space:nowrap}
.line.deal-done{border-color:var(--mint);background:var(--mint-wash)}
.line.deal-done .px{color:var(--mint)}
.stamp{display:grid;grid-template-columns:1fr auto;gap:14px;align-items:center;
  padding:11px 12px;border-radius:9px;border:1px solid var(--line)}
.stamp .lb{font-family:var(--mono);font-size:10px;letter-spacing:.15em;
  text-transform:uppercase;color:var(--low)}
.stamp .vl{font-family:var(--mono);font-size:13px;color:var(--mid);
  overflow-wrap:anywhere}
.stamp.gate{border-color:color-mix(in oklab,var(--mint) 42%,transparent)}
.stamp.gate .vl{color:var(--mint)}
.stamp.paid{border-color:color-mix(in oklab,var(--ember) 42%,transparent)}
.stamp.paid .vl{color:var(--ember);font-size:15px}
@media(prefers-reduced-motion:no-preference){
  .line,.stamp{animation:step .5s cubic-bezier(.16,1,.3,1) both}
  @keyframes step{from{opacity:0;transform:translateY(9px);filter:blur(4px)}
    to{opacity:1;transform:none;filter:blur(0)}}
}

/* --- section headings ----------------------------------------------------- */
.lead{max-width:56ch;margin:0 0 clamp(34px,4vw,52px)}
.lead h2{font-size:clamp(30px,3.9vw,46px);line-height:1.06}
.lead h2 em{font-style:italic;color:var(--ink);
  background-image:linear-gradient(transparent 58%,var(--hi) 58%,
    var(--hi) 92%,transparent 92%);
  background-repeat:no-repeat;background-size:100% 100%}
.lead p{margin:16px 0 0;font-size:17px;line-height:1.62;color:var(--body)}

/* --- what your agent does ------------------------------------------------- */
.jobs{display:grid;grid-template-columns:repeat(auto-fit,minmax(258px,1fr));
  gap:12px}
.job{background:var(--card);border:1px solid var(--pline);border-radius:13px;
  padding:24px 22px 22px}
.job .ic{color:var(--blue)}
.job h3{font-size:21px;margin:16px 0 0;letter-spacing:-.022em}
.job p{margin:10px 0 0;color:var(--soft);font-size:14.5px;line-height:1.55}
.job .ev{margin-top:18px;padding-top:14px;border-top:1px solid var(--pline);
  font-family:var(--mono);font-size:12px;color:var(--ink)}
.job .ev b{color:var(--green);font-size:16px;font-weight:600;margin-right:7px;
  font-variant-numeric:tabular-nums}
.job .ev span{color:var(--soft)}

/* --- the flywheel --------------------------------------------------------- */
.wheel{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;
  margin-bottom:26px}
@media(max-width:860px){.wheel{grid-template-columns:repeat(2,1fr)}}
.wf{background:var(--void);border:1px solid var(--line);border-radius:13px;padding:20px 20px 22px}
.wf .n{font-family:var(--mono);font-size:10.5px;color:var(--iris);
  letter-spacing:.12em}
.wf h3{font-size:18px;margin:9px 0 0;letter-spacing:-.02em}
.wf p{margin:8px 0 0;color:var(--mid);font-size:13.5px;line-height:1.5}
.wf .fig{margin-top:14px;font-family:var(--mono);font-size:15px;
  color:var(--mint);font-variant-numeric:tabular-nums}
.pull{font-family:var(--disp);font-size:clamp(23px,2.7vw,32px);line-height:1.3;
  letter-spacing:-.022em;max-width:30ch;margin:26px 0 0;color:var(--ink)}
.pull span{color:var(--blue)}

/* --- proof panels --------------------------------------------------------- */
.proof{border-radius:13px;overflow:hidden;font-family:var(--mono)}
.proof{background:var(--void);border:1px solid var(--line);
  box-shadow:0 18px 40px -24px color-mix(in oklab,var(--ink) 40%,transparent)}
.proof .ph{padding:9px 14px;font-size:10px;letter-spacing:.15em;
  text-transform:uppercase}
.proof .ph{color:var(--iris);border-bottom:1px solid var(--line)}
.proof .pb{padding:6px 14px 14px}
.pr{display:grid;grid-template-columns:1fr auto;gap:12px;padding:8px 0;
  font-size:12.5px;align-items:baseline}
.pr{border-bottom:1px solid var(--line);color:var(--mid)}
.pr:last-child{border-bottom:0}
.pr .v{font-variant-numeric:tabular-nums;white-space:nowrap}
.pr .v{color:var(--ember)}
.pr .rk{color:var(--iris);margin-right:9px}
.pr .nm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pr.win .v{color:var(--mint)}

/* --- the six steps -------------------------------------------------------- */
.steps{display:grid;grid-template-columns:repeat(6,1fr);
  border-block:1px solid var(--pline)}
.steps-5{grid-template-columns:repeat(5,1fr)}
@media(max-width:960px){.steps,.steps-5{grid-template-columns:repeat(2,1fr)}}
.st{padding:22px 20px 26px;border-left:1px solid var(--pline)}
.st:first-child{border-left:0}
@media(max-width:860px){.st:nth-child(odd){border-left:0}}
.st .n{font-family:var(--mono);font-size:10.5px;color:var(--blue)}
.st .t{font-family:var(--disp);font-size:19px;margin-top:8px;
  letter-spacing:-.02em}
.st .d{font-size:13.5px;color:var(--soft);margin-top:6px;line-height:1.5}
/* Content is visible by default; the reveal is added by script, so a page
   whose JavaScript never runs still reads completely. */
.js .steps,.js .pr,.js .jobs,.js .wheel{opacity:0;transform:translateY(10px)}
.js .steps.in,.js .pr.in,.js .jobs.in,.js .wheel.in{opacity:1;transform:none;
  transition:opacity .5s cubic-bezier(.16,1,.3,1),
             transform .5s cubic-bezier(.16,1,.3,1)}

/* --- what a business types ------------------------------------------------ */
.asks{display:grid;grid-template-columns:repeat(auto-fit,minmax(272px,1fr));
  gap:14px}
.ask-q{margin:0;font-family:var(--disp);font-size:19px;line-height:1.34;
  letter-spacing:-.015em;color:var(--ink);background:var(--card);
  border:1px solid var(--pline);border-radius:13px;
  padding:20px 22px 22px 26px;position:relative;overflow:hidden}
/* A ruled edge marks it as something the business said. The quote glyph it
   replaces was a unicode character used as an ornament, and it landed on
   top of the first line of every card. */
.ask-q::before{content:'';position:absolute;left:0;top:14px;bottom:14px;
  width:3px;background:var(--hi)}

/* --- how it behaves -------------------------------------------------------- */
.modes{display:grid;grid-template-columns:repeat(auto-fit,minmax(238px,1fr));
  gap:12px}
.mode{background:var(--card);border:1px solid var(--pline);border-radius:13px;
  padding:22px 22px 24px}
.mode h3{font-size:20px;letter-spacing:-.02em}
.mode p{margin:9px 0 0;color:var(--soft);font-size:14.5px;line-height:1.55}
.mode:nth-child(1) h3{color:var(--red)}
.mode:nth-child(2) h3{color:var(--blue)}
.mode:nth-child(3) h3{color:var(--green)}
.modes-note{margin:18px 0 0;color:var(--soft);font-size:14.5px;max-width:58ch}

/* --- conversation to transaction ------------------------------------------- */
.flow{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));
  gap:12px}
.fl{background:var(--card);border:1px solid var(--pline);border-radius:13px;
  padding:20px;position:relative}
.fl h3{font-size:17px;letter-spacing:-.018em}
.fl p{margin:8px 0 0;color:var(--soft);font-size:14px;line-height:1.5}
.fl+.fl::before{content:'';position:absolute;left:-13px;top:50%;width:14px;
  height:1px;background:var(--pedge)}
@media(max-width:860px){.fl+.fl::before{display:none}}

.parallel{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);
  gap:clamp(20px,3vw,40px);align-items:center}
@media(max-width:900px){.parallel{grid-template-columns:1fr}}
.parallel h3{font-family:var(--disp);font-size:clamp(23px,2.7vw,31px);
  line-height:1.2;letter-spacing:-.024em}
.parallel p{color:var(--body);font-size:16px;margin:14px 0 0;max-width:46ch}
.par-close{font-family:var(--disp);font-size:20px;line-height:1.4;
  color:var(--ink)}
.par-close b{font-weight:600}

/* --- the audit trail ------------------------------------------------------- */
.audit{display:grid;grid-template-columns:repeat(auto-fit,minmax(286px,1fr));
  gap:12px}
.au{background:var(--card);border:1px solid var(--pline);border-radius:13px;
  padding:20px 22px 22px}
.au h3{font-size:16.5px;letter-spacing:-.015em;line-height:1.3}
.au p{margin:8px 0 0;color:var(--soft);font-size:14px;line-height:1.5}

/* --- the terminal's five views --------------------------------------------- */
.areas{display:grid;grid-template-columns:repeat(auto-fit,minmax(232px,1fr));
  gap:12px}
.ar{background:var(--void);border:1px solid var(--line);border-radius:13px;
  padding:20px 20px 22px}
.ar h3{font-size:11px;letter-spacing:.15em;text-transform:uppercase;
  color:var(--iris);font-family:var(--mono);font-weight:500}
.ar p{margin:11px 0 0;color:var(--mid);font-size:14px;line-height:1.52}

/* --- the closing vision ---------------------------------------------------- */
.vision{background:var(--void);border:1px solid var(--line);border-radius:15px;
  padding:26px 26px 28px}
.vision p{margin:0 0 14px;color:var(--mid);font-size:15.5px;line-height:1.6}
.vision b{color:var(--bright);font-weight:600}
.v-close{font-family:var(--disp);font-size:21px;line-height:1.42;
  color:var(--bright);margin-bottom:0!important;padding-top:16px;
  border-top:1px solid var(--line)}

/* --- the doors ------------------------------------------------------------ */
.door{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,1fr);
  gap:clamp(22px,3vw,44px);align-items:start}
@media(max-width:860px){.door{grid-template-columns:1fr}}
.sell h2{font-size:clamp(30px,4.1vw,48px);line-height:1.04}
.sell p{font-size:17px;line-height:1.62;margin:18px 0 0;max-width:50ch}
.sell p{color:var(--body)}
.pts{list-style:none;margin:24px 0 0;padding:0;display:grid;gap:13px}
.pts li{display:grid;grid-template-columns:auto 1fr;gap:12px;align-items:start;
  font-size:15px;line-height:1.5}
.pts li{color:var(--body)}
.pts .ic{margin-top:3px}
.pts .ic{color:var(--blue)}
.band.light .pts .ic{color:var(--gold)}
.enter{margin-top:30px;display:inline-flex;align-items:center;gap:11px;
  text-decoration:none;font-size:16px;font-weight:600;padding:15px 22px;
  border-radius:11px;
  transition:transform .22s cubic-bezier(.16,1,.3,1),box-shadow .22s}
.enter .ic{transition:transform .22s cubic-bezier(.16,1,.3,1)}
.enter:hover .ic{transform:translateX(4px)}
.enter:active{transform:translateY(0) scale(.985);transition-duration:.09s}
.enter{background:var(--blue);color:#fff;
  box-shadow:0 12px 26px -12px color-mix(in oklab,var(--blue) 60%,transparent)}
.enter:hover{transform:translateY(-2px)}
.enter:active{box-shadow:0 6px 14px -10px
  color-mix(in oklab,var(--blue) 60%,transparent)}
/* The desk is the one place gold appears, because it is the one place a
   merchant cannot go. */
.band.light .enter{background:var(--ink)}
.band.light .enter{box-shadow:0 12px 26px -12px
  color-mix(in oklab,var(--ink) 55%,transparent)}

footer{padding:26px clamp(20px,5vw,64px);border-top:1px solid var(--pline);
  color:var(--soft);font-family:var(--mono);font-size:11px;line-height:1.8;
  background:var(--paper)}
@media(prefers-reduced-motion:reduce){
  *{animation:none!important;scroll-behavior:auto}
  .js .steps,.js .pr,.js .jobs,.js .wheel{opacity:1;transform:none}
  .hero h1 em{background-size:100% 100%}
  .plans{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));
  gap:14px;margin-top:8px}
.plan{border:1px solid var(--line);border-radius:4px;background:var(--card);
  padding:20px 22px;display:flex;flex-direction:column;gap:9px}
.plan.lit{border-color:var(--gold);box-shadow:0 0 0 1px var(--gold)}
.plan h3{margin:0;font-family:var(--disp);font-size:20px;font-weight:600;
  color:var(--ink)}
.plan p{margin:0;color:var(--body);font-size:14.5px;line-height:1.55}
.plan .inc{margin-top:auto;font-family:var(--mono);font-size:10.5px;
  letter-spacing:.1em;text-transform:uppercase;color:var(--soft)}
.plan.lit .inc{color:var(--gold)}
.benches{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));
  gap:10px;margin-top:14px}
.bench{border:1px solid var(--line);border-radius:4px;background:var(--card);
  padding:13px 15px}
.bench b{display:block;color:var(--ink);font-size:14.5px;margin-bottom:3px}
.bench span{display:block;color:var(--body);font-size:13px}
.bench i{display:block;margin-top:5px;font-style:normal;font-family:var(--mono);
  font-size:10.5px;color:var(--soft);line-height:1.5}
.fine{margin-top:14px;font-size:13px;color:var(--soft);max-width:64ch}
@media(prefers-reduced-motion:reduce){
  .enter,.spread .track i,.spread .val{transition-duration:.01ms!important}
}
""" + NET_CSS


LANDING_JS = r"""<script>
(function(){
document.documentElement.classList.add('js');
var D=JSON.parse(document.getElementById('deal').textContent);
var feed=document.getElementById('feed'),n=0;
var gapBar=document.getElementById('gap'),sprd=document.getElementById('sprd');
var spreadBox=document.querySelector('.spread');
function esc(t){var d=document.createElement('div');
  d.textContent=t==null?'':t;return d.innerHTML}
/* The negotiation plays once, at reading speed, and stops. It is the page's
   one authored moment. Every line is quoted from the log — the page does not
   simulate a deal, it replays one. */
function add(){
  if(n>=D.lines.length)return;
  var L=D.lines[n++];
  var el=document.createElement('div');
  if(L.kind==='offer'){
    el.className='line'+(n%2?'':' b')+(L.done?' deal-done':'');
    el.innerHTML='<span><span class="who">'+esc(L.who)+'</span>'+
      '<span class="said">'+esc(L.said)+'</span></span>'+
      '<span class="px">'+esc(L.px)+'</span>';
  }else{
    el.className='stamp '+esc(L.kind);
    el.innerHTML='<span class="lb">'+esc(L.label)+'</span>'+
      '<span class="vl">'+esc(L.value)+'</span>';
  }
  feed.appendChild(el);
  while(feed.children.length>4)feed.removeChild(feed.firstChild);
  if(L.spread!=null){
    sprd.textContent=L.spread;
    gapBar.style.width=(L.gap*100).toFixed(1)+'%';
    spreadBox.classList.toggle('closed',L.gap===0);
  }
  if(n<D.lines.length)setTimeout(add,L.hold||1500);
}
if(matchMedia('(prefers-reduced-motion: reduce)').matches){
  while(n<D.lines.length)add();
}else{setTimeout(add,420)}

/* The steps are a numbered sequence and the panels are lists, so both earn a
   stagger. The delay is capped: a reader who scrolls fast must never wait. */
var reveal=[].slice.call(document.querySelectorAll('.steps,.pr,.jobs,.wheel'));
if(!('IntersectionObserver' in window)){
  reveal.forEach(function(el){el.classList.add('in')});
}else{
  var io=new IntersectionObserver(function(es){
    es.forEach(function(e){
      if(!e.isIntersecting)return;
      var i=[].slice.call(e.target.parentNode.children).indexOf(e.target);
      e.target.style.transitionDelay=Math.min(i*55,330)+'ms';
      e.target.classList.add('in');
      io.unobserve(e.target);
    });
  },{threshold:.12});
  reveal.forEach(function(el){io.observe(el)});
}
""" + NET_JS + """
})();
</script>"""


HOWTO_CSS = """
/* THE DESK IS A FIXED-HEIGHT INSTRUMENT: body is a 100vh flex column with a
   scrolling main and a footer pinned as a flex child. A long document in
   that shell scrolls its content UNDERNEATH the footer, which is what put
   the gate totals on top of the table. These pages are documents, so they
   opt out of the shell and scroll normally. */
html,body{height:auto;overflow:visible}
body{display:block}
footer{flex:initial}
/* The guide. Reads as a document rather than an instrument, so it gets a
   measure and room to breathe while keeping the desk's palette — a reader
   arrives here from the desk and should not feel they have left it. */
.doc{max-width:820px;margin:0 auto;padding:38px 22px 90px}
.doc h1{font-family:var(--serif);font-size:clamp(28px,4vw,40px);
  font-weight:400;color:var(--white);margin:0 0 14px;letter-spacing:-.01em}
.doc h2{font-family:var(--serif);font-size:21px;font-weight:400;
  color:var(--white);margin:44px 0 12px}
.doc h3{font-size:12px;font-family:var(--mono);letter-spacing:.1em;
  text-transform:uppercase;color:var(--amber);margin:0 0 7px}
.doc p{color:var(--dim);line-height:1.62;margin:0 0 12px;font-size:14.5px}
.doc p b{color:var(--white)}
.doc .stand{font-size:16.5px;color:var(--white);max-width:62ch}
.doc code{font-family:var(--mono);font-size:12px;background:var(--lift);
  border:1px solid var(--rule);padding:1px 5px;color:var(--amber)}
.doc em{color:var(--white);font-style:italic}

.layout{border:1px solid var(--rule);margin:16px 0 0;background:var(--panel)}
.lay{display:grid;grid-template-columns:190px minmax(0,1fr) 96px;gap:14px;
  padding:11px 14px;border-bottom:1px solid var(--rule);align-items:baseline}
.lay:last-child{border-bottom:0}
.lay .lb{font-family:var(--mono);font-size:11px;color:var(--faint);
  letter-spacing:.04em}
.lay .bd{color:var(--white);font-size:13.5px;line-height:1.5}
.lay .bd b{color:var(--amber)}
.lay .bd .evn{color:var(--amber);font-family:var(--mono);font-size:12px}
.lay .sr{font-family:var(--mono);font-size:10px;letter-spacing:.08em;
  text-transform:uppercase;color:var(--green);text-align:right}
@media(max-width:720px){
  .lay{grid-template-columns:1fr;gap:4px}
  .lay .sr{text-align:left}}

.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));
  gap:11px;margin-top:16px}
.cd{border:1px solid var(--rule);background:var(--panel);padding:13px 15px}
.cd p{margin:0;font-size:13px}

.won{margin:22px 0 0;padding:14px 17px;border-left:3px solid var(--green);
  background:rgba(38,208,124,.08);font-size:17px;color:var(--white)}
.won b{color:var(--green)}
.won i{display:block;margin-top:5px;font-style:normal;font-size:12px;
  color:var(--dim);font-family:var(--mono)}
.doc .lot{font-family:var(--serif);font-size:19px;color:var(--white);
  line-height:1.45;border-left:2px solid var(--amber);padding-left:14px;
  margin:0 0 14px}
table.bids{width:100%;border-collapse:collapse;margin-top:14px;
  border:1px solid var(--rule)}
table.bids th{font-family:var(--mono);font-size:10px;letter-spacing:.09em;
  text-transform:uppercase;color:var(--faint);text-align:left;
  padding:8px 12px;border-bottom:1px solid var(--rule);background:var(--lift)}
table.bids td{padding:9px 12px;border-bottom:1px solid var(--rule);
  color:var(--dim);font-size:13px;vertical-align:top}
table.bids tr:last-child td{border-bottom:0}
table.bids td.a{color:var(--white);white-space:nowrap}
table.bids td.pt{font-family:var(--mono);color:var(--white);text-align:right;
  white-space:nowrap}
table.bids td.mk{font-family:var(--mono);font-size:10px;color:var(--green);
  text-transform:uppercase;letter-spacing:.07em;white-space:nowrap}
table.bids tr.top td{background:rgba(255,255,255,.035)}
table.bids td.q{font-size:12.5px;line-height:1.5}
"""


def build_howto(db_path: str) -> str:
    """How to read the desk, written against the desk's own current numbers.

    Generated rather than hand-written, and every example is pulled from the
    published board. A guide with numbers typed into it goes stale the first
    time the market is re-run, and a stale guide teaching someone to read a
    live page is worse than no guide.
    """
    summary, _trades, events = load(db_path)
    desk = board(events)
    perf = performance(events)
    scan = radar(events)

    row = (desk["rows"][0] if desk and desk["rows"] else {})
    cash = perf.get(row.get("campaign", ""), {}) if perf else {}
    hot = (scan["rows"][0] if scan and scan["rows"] else {})

    def line(label, body, source):
        return (f'<div class="lay"><span class="lb">{label}</span>'
                f'<span class="bd">{body}</span>'
                f'<span class="sr">{source}</span></div>')

    early = rupees(row.get("early_paise", 0))
    late = rupees(row.get("late_paise", 0))
    anatomy = (
        line("the heading",
             f'<b>{esc(row.get("campaign", ""))}</b> &middot; '
             f'{row.get("movement", 0):.1f}&times; &middot; '
             f'{row.get("merchants", 0)} merchants &middot; '
             f'{rupees(row.get("value_paise", 0))}',
             "the log")
        + line("the arithmetic under it",
               f'{early} in the opening rounds grew to {late} in the closing '
               f'ones &middot; {row.get("settled", 0)} of '
               f'{row.get("attempts", 0)} attempts settled &middot; '
               f'<span class="evn">event {row.get("seq", "?")}</span>',
               "the log")

        + line("the sentence",
               esc(_clip_words(row.get("driver", ""), 150)),
               "the press")
        + line("the grey chips",
               "each one is the outlet that sentence was read from, and a "
               "link to the article",
               "the press")
        + line("what operators are saying",
               esc(_clip_words(row.get("discussion", "") or
                               "not read for this row", 150)),
               "Reddit")
        + line("the r/ chips",
               "the threads that sentence was read from, each a link",
               "Reddit"))

    scanning = (
        line("the heading",
             f'<b>{esc(hot.get("campaign", ""))}</b> &middot; heat '
             f'{hot.get("heat", 0)} &middot; {hot.get("threads", 0)} threads '
             f'in {hot.get("spread", 0)} communities',
             "Reddit / X")
        + line("what heat is",
               "threads multiplied by communities. Forty posts inside one "
               "subreddit is a community with a hobby; eight across five is "
               "a campaign the market noticed",
               "arithmetic")
        + line("the chips",
               "every post the row was counted from, with its upvotes. Click "
               "one and you are reading the same thread the desk read",
               "Reddit / X"))

    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>How to read the desk</title>'
        f"<style>{CSS}{HOWTO_CSS}</style></head><body>"

        + '<div class="bar"><span class="mark">RAZORPAY DESK</span>'
          '<span class="navs">'
          '<a class="nav" href="desk.html">&larr; the desk</a>'
          '<a class="nav" href="index.html">the exchange</a>'
          "</span></div>"

        + '<main class="doc">'

        + '<h1>How to read this desk.</h1>'
          '<p class="stand">Four different kinds of thing sit on one row, and '
          'they are not equally trustworthy. Two are computed from money that '
          'moved. Two are quotations from outside. The page never mixes them, '
          'and this is how to tell which is which.</p>'

        + '<h2>A campaign row, line by line</h2>'
          '<p>Using the row at the top of the board right now.</p>'
          f'<div class="layout">{anatomy}</div>'

        + '<h2>The four sources, and what each is worth</h2>'
          '<div class="cards">'
          '<div class="cd"><h3>The log</h3><p>Every rupee and every count. '
          'Recomputable by anyone holding the same events, which is why each '
          'row prints the event number it was published as. Look one up: '
          '<code>select payload from events where seq=964</code></p></div>'
          '<div class="cd"><h3>Razorpay</h3><p>The green strip. Real payment '
          'links and real captures on real payment ids. Not a projection of '
          'anything &mdash; the money either arrived or it did not.</p></div>'
          '<div class="cd"><h3>The press</h3><p>One sentence explaining why a '
          'category is moving, with the outlets it was read from. It explains '
          'a row. It can never move one.</p></div>'
          '<div class="cd"><h3>Reddit and X</h3><p>What people running these '
          'businesses say. Same rule: attached after the ranking is fixed, '
          'and carrying the threads so you can check the reading.</p></div>'
          "</div>"

        + '<h2>The rule that makes the ranking checkable</h2>'
          '<p>The ranking function cannot reach the model, the news, or '
          'Reddit. That is enforced by a test rather than by discipline, '
          'because an agent that reads a headline and then reports a number '
          'has laundered an opinion into a fact. Press and discussion are '
          'attached <em>after</em> the order is fixed and can only ever add '
          'text.</p>'

        + '<h2>The amber band, and why it is separated</h2>'
          '<p>Everything above it is arithmetic over money that moved between '
          'merchants on this exchange. Everything below it is a count of '
          'strangers talking about companies that are not on it. Both are '
          'useful and they are not the same kind of fact, so they never share '
          'a heading.</p>'
          f'<div class="layout">{scanning}</div>'

        + '<h2>What the numbers do not say</h2>'
          '<p><b>The settled share is not a conversion rate.</b> This run pays '
          'one payment link per merchant, because Razorpay test mode allows '
          'thirty links per account and the privacy floor counts distinct '
          'merchants. Unpaid links are the run declining to spend, not buyers '
          'declining to pay.</p>'
          '<p><b>Razorpay sees the payment, not the ad click.</b> So this '
          'measures campaign to cash, not ad to sale. Tagging a payment link '
          'with its campaign closes that gap, and the tag travels in the '
          'order&rsquo;s notes.</p>'
          '<p><b>Refused rows are not failures.</b> Campaigns below the '
          'privacy floor are kept off the board and the refusal is logged. A '
          'floor nobody can see is indistinguishable from no floor.</p>'

        + "</main>"
        + _footer(db_path, summary)
        + "</body></html>")


def _plan_html(bench) -> str:
    """What the intelligence is, and how a merchant gets it.

    IT IS A PLAN FEATURE, NOT AN AUCTION. The auction was a mechanism looking
    for a use: a coffee roaster has no reason to bid against a clothing brand
    for apparel benchmarks, and watching it do so said more about the
    mechanism than about the product. What a merchant wants is the part of
    the market it is actually in, kept current, without an event to attend.

    So the intelligence ships the way everything on a payments platform
    ships — included with a plan, priced by how much of the market you see.
    """
    rows = ""
    for row in (bench or [])[:4]:
        share = row["below_ask_share"]
        moves = (f'{share * 100:.0f}% of trades close under it &middot; '
                 f'{row["median_saving"] * 100:.0f}% saved when they do'
                 if share else "sellers here never move")
        rows += (
            f'<div class="bench"><b>{esc(row["category"])}</b>'
            f'<span>clears at {rupees(row["clears_paise"])}, against a '
            f'{rupees(row["ask_paise"])} ask</span><i>{moves}</i></div>')
    return (
        '<div class="plans">'
        '<div class="plan"><h3>Standard</h3>'
        '<p>Your own agents, your own books, your own audit trail. Everything '
        'a business needs to trade on the exchange.</p>'
        '<span class="inc">included today</span></div>'
        '<div class="plan lit"><h3>Standard + Market</h3>'
        '<p>The board above, kept current as the market trades: what your '
        'category clears at, how often sellers move, and which campaigns are '
        'climbing across the whole client base.</p>'
        '<span class="inc">the intelligence layer</span></div>'
        "</div>"
        + (f'<div class="benches">{rows}</div>' if rows else "")
        + '<p class="fine">Your own trading is what produces this, and a '
          'figure is published only where at least three businesses stand '
          'behind it &mdash; so no row can be traced back to one shop, '
          'including yours.</p>')


def build_landing(db_path: str, roster) -> str:
    """The front door, following the product narrative end to end.

    THE ARGUMENT, IN THE CLIENT'S OWN WORDS. Razorpay already moves money
    between millions of businesses; the network exists and nobody has switched
    it on. Give every business an agent that can reach it and payments become
    partnerships.

    THE FIGURES ARE STILL COUNTED FROM THE LOG. Every number on this page is
    read at build time from the same events the audit trail is made of. The
    copy makes the claim; the log is why you can believe it.
    """
    from exchange.books import entries_for

    summary, _trades, events = load(db_path)
    failures = failure_threads(events)
    desk = board(events)
    bench = benchmarks(events)
    rail_map = rails(events)
    first = _page_name(roster[0])

    relationships = len({(r["buyer"], s["head"])
                         for r in rail_map.values()
                         for s in r["stations"]
                         if s["key"] == "picked" and s["head"].startswith("m_")})
    book_entries = sum(len(entries_for(events, m).entries) for m in roster)

    hero = _hero_deal(rail_map)
    deal = _deal_payload(hero)
    net = _network_data(events, rail_map, roster)

    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light">'
        '<meta name="description" content="An intelligent business network '
        'where AI agents discover opportunities, negotiate deals, execute '
        'transactions, and continuously learn from the market.">'
        '<title>Agent Exchange &mdash; the agentic business network</title>'
        f'<style>{LANDING_CSS.replace("__FONT__", _font_data_uri())}</style>'
        "</head><body>"

        # ---- 1. the hook ---------------------------------------------------
        + '<section class="band dark"><div class="in hero"><div>'
        + "<h1>One business. <em>Two workforces</em>.</h1>"
        + '<p class="say">Razorpay already powers transactions between '
          'millions of businesses. We turn that network into something more '
          'powerful: <b>an intelligent business network where AI agents '
          'discover opportunities, negotiate deals, execute transactions, and '
          'keep learning from the market.</b></p>'
        + f'<div class="tape">'
          f'<span><b class="m num">{rupees(summary.value_paise)}</b> settled '
          f'between agents</span>'
          f'<span><b class="num">{summary.merchants}</b> businesses</span>'
          f'<span><b class="num">{relationships}</b> partnerships formed</span>'
          f'<span><b class="num">{summary.gate_allow + summary.gate_deny}</b> '
          f'decisions recorded</span></div>'
        + f"</div>{_deal_card(hero)}</div></section>"

        # ---- 2. payments to partnerships -----------------------------------
        + '<section class="band dark" style="padding-top:0"><div class="in">'
        + '<div class="lead"><h2>From payments to <em>partnerships</em>.</h2>'
          '<p>Today, businesses use Razorpay to receive and send money. '
          'Tomorrow they can use it to find who they should be doing business '
          'with. Every line below is a partnership that formed on its own — '
          'touch any business to see who its agent reached.</p></div>'
        + _network_html(net)
        + '<div style="height:clamp(44px,6vw,74px)"></div>'
        + _steps_html()
        + "</div></section>"

        # ---- 3. the representative -----------------------------------------
        + '<section class="band light"><div class="in">'
        + '<div class="lead"><h2>Give your business an '
          '<em>AI representative</em>.</h2>'
          '<p>Your agent understands your goals and acts for you. Tell it what '
          'you want in plain words; it searches the network, finds the right '
          'businesses, opens the conversation, and negotiates inside your '
          'rules.</p></div>'
        + _asks_html()
        + '<div style="height:clamp(30px,4vw,52px)"></div>'
        + '<div class="lead" style="max-width:46ch;margin-bottom:22px">'
          '<h2 style="font-size:clamp(24px,2.8vw,32px)">You decide how it '
          'behaves.</h2></div>'
        + _styles_html()
        + "</div></section>"

        # ---- 4. conversation to transaction --------------------------------
        + '<section class="band dark"><div class="in">'
        + '<div class="lead"><h2>From conversation to '
          '<em>transaction</em>.</h2>'
          '<p>Business should not stop at a conversation. Once terms are '
          'agreed, the whole workflow connects to the systems you already run '
          '— products, inventory, pricing and orders stay in step through '
          'tools like Google Sheets.</p></div>'
        + _flow_html(summary, book_entries)
        + '<div style="height:clamp(38px,5vw,62px)"></div>'
        + '<div class="parallel"><div>'
          '<h3>You can only be in one meeting at a time.</h3>'
          '<p>Your agent does not have that limitation. While you meet a '
          'customer it can be finding suppliers. While you negotiate one deal '
          'it can negotiate another.</p>'
          '<p class="par-close">You work offline. Your agent works online. '
          '<b>Together, you work in parallel.</b></p></div>'
          f'{_books_proof(events, roster)}</div>'
        + "</div></section>"

        # ---- 5. the audit trail --------------------------------------------
        + '<section class="band light"><div class="in">'
        + '<div class="lead"><h2>Every decision. <em>Fully auditable</em>.</h2>'
          '<p>AI should never be a black box when it is making business '
          'decisions. Every action is traceable and every decision is '
          'accountable, down to the numbered event it came from.</p></div>'
        + _audit_html(summary, len(failures))
        + "</div></section>"

        # ---- 6. the terminal -----------------------------------------------
        + '<section class="band dark"><div class="in">'
        + '<div class="lead"><h2>The Razorpay '
          '<em>Intelligence Terminal</em>.</h2>'
          '<p>While businesses get their own AI workforce, the Razorpay team '
          'gets a live intelligence layer across the network — a Bloomberg '
          'terminal for the business ecosystem. It surfaces market '
          'intelligence, never confidential business information or trade '
          'secrets.</p></div>'
        + _terminal_html()
        + "</div></section>"

        # ---- 7. the living leaderboard -------------------------------------
        + '<section class="band light"><div class="in">'
        + '<div class="lead"><h2>A living leaderboard of '
          '<em>what is working</em>.</h2>'
          '<p>A campaign nobody noticed yesterday can be the strategy everyone '
          'copies tomorrow. Instead of asking what is working in the market, '
          'you get a continuously evolving answer — refreshed as the market '
          'trades, and included with your plan.</p></div>'
        + _plan_html(bench)
        + "</div></section>"

        # ---- 8. two sides --------------------------------------------------
        + '<section class="band dark"><div class="in door"><div class="sell">'
        + "<h2>Your AI business agent.</h2>"
        + "<p>Discover partners. Build relationships. Negotiate deals. Execute "
          "workflows. Track performance. Maintain accounts.</p>"
        + '<ul class="pts">'
          f'<li>{_icon("chart")}<span>What each part of your agent did, in its '
          f'own words</span></li>'
          f'<li>{_icon("ledger")}<span>Every deal, kept as books that sync to '
          f'your Google Sheet</span></li>'
          f'<li>{_icon("shield")}<span>Your catalogue, and one box to ask for '
          f'anything</span></li></ul>'
        + f'<a class="enter" href="{esc(first)}">Open your dashboard'
          f'{_icon("arrow", 18)}</a>'
        + f'</div>{_board_proof(desk)}</div></section>'

        + '<section class="band light"><div class="in door"><div class="sell">'
        + "<h2>Your network intelligence terminal.</h2>"
        + "<p>Monitor activity. Track market movements. Discover successful "
          "campaigns. Surface ecosystem insights. Identify emerging "
          "opportunities. Razorpay staff only.</p>"
        + '<ul class="pts">'
          f'<li>{_icon("chart")}<span>Every agent and every decision, as it '
          f'happens</span></li>'
          f'<li>{_icon("ledger")}<span>Trending client campaigns, ranked from '
          f'the whole network</span></li>'
          f'<li>{_icon("gavel")}<span>What every category actually clears '
          f'at, and how often sellers move</span></li></ul>'
        + f'<a class="enter" href="desk.html">Enter the terminal'
          f'{_icon("arrow", 18)}</a>'
        + f'</div>{_vision_html()}</div></section>'

        + f'<footer>{summary.gate_allow} money actions allowed, '
          f'{summary.gate_deny} refused, {len(failures)} payment mismatches '
          f'caught and repaired without anyone watching.<br>'
          f'Every figure on this page is counted from the audit trail. The '
          f'negotiation above is quoted, not written.</footer>'

        + f'<script type="application/json" id="deal">{deal}</script>'
        + f'<script type="application/json" id="net">'
          f'{json.dumps(net, separators=(",", ":"))}</script>'
        + LANDING_JS + "</body></html>"
    )


def _asks_html() -> str:
    """What a business actually types. Quoted as speech, because it is."""
    asks = (
        "Find me reliable suppliers for 10,000 units.",
        "Find businesses that could use our logistics service.",
        "Negotiate the best possible price.",
        "Maintain my existing business relationships.",
    )
    return '<div class="asks">' + "".join(
        f'<blockquote class="ask-q">{esc(a)}</blockquote>' for a in asks
    ) + "</div>"


def _styles_html() -> str:
    """The three strategies, which are real settings rather than a brochure.

    These map onto the mandate keywords a merchant actually writes into its
    agent's brief, so the page is describing a control that exists.
    """
    modes = (
        ("Aggressive", "Push for the best price. Negotiate harder. "
                       "Maximise margins."),
        ("Balanced", "Optimise for both price and long-term relationships."),
        ("Polite", "Prioritise relationships, trust, and sustainable "
                   "partnerships."),
    )
    cards = "".join(
        f'<article class="mode"><h3>{esc(name)}</h3><p>{esc(body)}</p>'
        f"</article>" for name, body in modes)
    return (f'<div class="modes">{cards}</div>'
            '<p class="modes-note">You set the strategy and the agent executes '
            'it &mdash; or leave it on the default and it will behave '
            'sensibly.</p>')


def _flow_html(summary, book_entries) -> str:
    steps = (
        ("AI agent", "Finds the counterparty and opens the conversation."),
        ("Deal", "Terms agreed inside the rules you set."),
        ("Business workflow", "Inventory, pricing and orders stay in step."),
        ("Transaction", f"Settled on Razorpay — {summary.completed} payments "
                        f"confirmed in this run."),
    )
    return '<div class="flow">' + "".join(
        f'<div class="fl"><h3>{esc(t)}</h3><p>{esc(d)}</p></div>'
        for t, d in steps) + "</div>"


def _audit_html(summary, repaired) -> str:
    """The audit trail, as the questions it can answer."""
    rows = (
        ("What the agent was asked to achieve",
         "The need, in the words the business typed."),
        ("Which businesses it identified",
         "The full shortlist, not only the winner."),
        ("Why it chose a particular opportunity",
         "The agent's own reasoning, quoted."),
        ("How it negotiated", "Every offer, in order, with the price."),
        ("What decisions were made",
         f"{summary.gate_allow + summary.gate_deny} rulings recorded before "
         f"money moved, including {summary.gate_deny} refusals."),
        ("What happened after the transaction",
         f"{repaired} mismatches with Razorpay caught and repaired by the "
         f"system itself."),
    )
    return '<div class="audit">' + "".join(
        f'<div class="au"><h3>{esc(q)}</h3><p>{esc(a)}</p></div>'
        for q, a in rows) + "</div>"


def _terminal_html() -> str:
    areas = (
        ("Live activity",
         "Business activity and transactions across the network, as they "
         "happen."),
        ("Market intelligence",
         "Emerging trends, categories, demand signals and shifts in activity."),
        ("Campaign leaderboard",
         "The advertising campaigns and creative strategies gaining traction "
         "right now."),
        ("Business performance",
         "Which strategies are helping businesses perform better."),
        ("Emerging opportunities",
         "Patterns across the ecosystem that could become something."),
    )
    return '<div class="areas">' + "".join(
        f'<div class="ar"><h3>{esc(t)}</h3><p>{esc(d)}</p></div>'
        for t, d in areas) + "</div>"


def _vision_html() -> str:
    return (
        '<div class="vision">'
        '<p>Payments tell you <b>that a transaction happened</b>.</p>'
        '<p>This tells you who could do business together, why they should, '
        'what they should trade, how they should negotiate, and what the '
        'market is doing around them.</p>'
        '<p class="v-close">Payments enabled commerce.<br>'
        '<b>Agents can enable the commerce itself.</b></p>'
        "</div>")


def _hero_deal(rail_map):
    """The longest real negotiation that reached a confirmed payment.

    Found by shape, so a different run finds its own best story rather than
    rendering an empty box.
    """
    candidates = [r for r in rail_map.values()
                  if len(r["talk"]) >= 4 and any(
                      s["key"] == "paid" and any(
                          str(x).startswith("pay_") for x in s["lines"])
                      for s in r["stations"])]
    return max(candidates, key=lambda r: len(r["talk"]), default=None)


def _deal_card(hero) -> str:
    if hero is None:
        return ""
    return (
        '<div class="deal"><div class="dh"><span class="dot"></span>'
        'live negotiation'
        f'<span class="want">{esc(hero["need"])}</span></div>'
        '<div class="spread"><span class="lab">spread</span>'
        '<span class="track"><i id="gap"></i></span>'
        '<span class="val" id="sprd">&mdash;</span></div>'
        '<div class="feed" id="feed"></div></div>')


def _deal_payload(hero) -> str:
    """The hero negotiation, as lines the page plays.

    Prices convert from paise once, here, so the page never does arithmetic
    on money. The spread is computed here for the same reason.
    """
    if hero is None:
        return json.dumps({"lines": []})

    lines, last, opening = [], {}, None
    for n, turn in enumerate(hero["talk"]):
        said = str(turn["said"])
        said = said.split("—", 1)[-1].split(" - ", 1)[-1].strip()
        last[turn["who"]] = turn["price"] or 0
        spread = None
        if len(last) == 2:
            spread = abs(list(last.values())[0] - list(last.values())[1])
            if opening is None and spread:
                opening = spread
        lines.append({
            "kind": "offer",
            "who": turn["who"],
            "px": f'₹{(turn["price"] or 0) / 100:,.2f}',
            "said": _clip_words(said, 104),
            "done": n == len(hero["talk"]) - 1,
            "hold": 1700,
            "spread": (f'₹{spread / 100:,.2f}' if spread is not None else None),
            "gap": (round(spread / opening, 4)
                    if spread is not None and opening else 0),
        })

    gate = next((s for s in hero["stations"] if s["key"] == "gate"), None)
    if gate:
        lines.append({"kind": "gate", "label": "the gate ruled",
                      "value": gate["head"], "hold": 1500})
    paid = next((s for s in hero["stations"] if s["key"] == "paid"), None)
    if paid:
        pid = next((x for x in paid["lines"] if str(x).startswith("pay_")), "")
        lines.append({"kind": "paid", "label": "razorpay confirmed",
                      "value": pid or paid["head"], "hold": 2400})
    return json.dumps({"lines": lines}, separators=(",", ":"))


def _jobs_html(summary, relationships, book_entries, bench) -> str:
    won = str(len(bench)) if bench else "—"
    jobs = (
        ("swap", "Buys and sells for you",
         "Posts what you need in plain words, finds who has it, argues the "
         "price down, and settles on Razorpay.",
         rupees(summary.value_paise), f"settled across {summary.completed} "
         f"payments"),
        ("link", "Makes the connections",
         "New suppliers get a deliberate first try, with a small cap, so a "
         "good one can earn its way in on results.",
         str(relationships), "counterparties found with no introduction"),
        ("ledger", "Keeps your books",
         "Every buy and sell lands in a ledger with the counterparty, the "
         "unit price, and the payment id — and syncs to your Google Sheet.",
         str(book_entries), "entries kept without anyone typing"),
        ("gavel", "Knows what things really cost",
         "What each category actually clears at against the asking price, "
         "computed from both sides of every settled trade.",
         won, "categories priced from the whole network"),
    )
    return '<div class="jobs">' + "".join(
        f'<article class="job">{_icon(icon, 22)}<h3>{esc(title)}</h3>'
        f'<p>{esc(body)}</p>'
        f'<div class="ev"><b>{esc(figure)}</b><span>{esc(caption)}</span></div>'
        f"</article>"
        for icon, title, body, figure, caption in jobs) + "</div>"


def _steps_html() -> str:
    """Discover, connect, negotiate, transact, maintain — the arc a
    relationship actually travels, and the order carries the information."""
    steps = (
        ("Discover", "It finds businesses on the network worth talking to."),
        ("Connect", "It opens the conversation and reads the counterparty."),
        ("Negotiate", "Offers go back and forth inside the rules you set."),
        ("Transact", "A real Razorpay order, and a real payment id back."),
        ("Maintain", "It keeps the relationship, and what it learned from it."),
    )
    return ('<div class="steps steps-5">' + "".join(
        f'<div class="st"><div class="n">{n + 1}</div>'
        f'<div class="t">{esc(name)}</div><div class="d">{esc(why)}</div></div>'
        for n, (name, why) in enumerate(steps)) + "</div>")


def _books_proof(events, roster) -> str:
    from exchange.books import entries_for

    best = max((entries_for(events, a) for a in roster),
               key=lambda b: len(b.entries), default=None)
    if best is None or not best.entries:
        return ""
    rows = "".join(
        f'<div class="pr"><span class="nm">{esc(_clip_words(e.item, 32))}'
        f'</span><span class="v">₹{e.amount_inr:,.0f}</span></div>'
        for e in best.entries[:5])
    return (
        '<div class="proof"><div class="ph">'
        f'{esc(who(best.actor_id))} &middot; books</div>'
        f'<div class="pb">{rows}'
        f'<div class="pr"><span class="nm">confirmed by Razorpay</span>'
        f'<span class="v">₹{best.settled_inr:,.0f}</span></div></div></div>')


def _board_proof(desk) -> str:
    if not desk:
        return ""
    rows = "".join(
        f'<div class="pr"><span class="nm"><span class="rk">{row["rank"]}'
        f'</span>{esc(row["campaign"])}</span>'
        f'<span class="v">{row["movement"]:.1f}&times;</span></div>'
        for row in desk["rows"][:5])
    return ('<div class="proof"><div class="ph">trending client campaigns'
            f'</div><div class="pb">{rows}</div></div>')


def main(argv=None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    db = args[0] if args else "runs/market.db"
    out = pathlib.Path(args[1] if len(args) > 1 else "docs")
    if out.suffix == ".html":  # a path to a page still names its directory
        out = out.parent
    out.mkdir(parents=True, exist_ok=True)

    _summary, _trades, events = load(db)
    roster = sorted(state_actors(events))

    written = 0
    for actor in roster:
        page = build_merchant(db, actor, roster)
        (out / _page_name(actor)).write_text(page, encoding="utf-8")
        written += 1

    # `replay.html` stays the entry point every earlier note and command
    # refers to. It is the first merchant's page, so an old link still opens
    # something meaningful rather than a 404.
    first = build_merchant(db, roster[0], roster)
    (out / "replay.html").write_text(first, encoding="utf-8")

    (out / "desk.html").write_text(build_desk(db), encoding="utf-8")
    (out / "how-to.html").write_text(build_howto(db), encoding="utf-8")
    (out / "index.html").write_text(build_landing(db, roster), encoding="utf-8")

    print(f"wrote index.html + desk.html + {written} merchant pages "
          f"(and replay.html) to {out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
