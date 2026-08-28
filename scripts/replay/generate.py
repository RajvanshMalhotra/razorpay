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
haggled, the gate, paid, remembered. The interesting cases are not different
screens: a trade that breaks grows three more stations out of the fifth one; a
trade a person typed differs only at the first.

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
import pathlib
import sys
from datetime import datetime, timezone

from exchange.books import COLUMNS, entries_for
from scripts.replay.read import (
    auction,
    board,
    catalogue,
    failure_threads,
    load,
    merchant_view,
    rails,
    storefront,
    tape,
)


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
  --blue:#4D9EFF; --green:#26D07C; --red:#FF4B3E; --violet:#B69CFF;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
  --serif:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
}
*{box-sizing:border-box}
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
.stn{flex:1 1 148px;min-width:148px;background:var(--panel);
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
.mgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(98px,1fr));
  gap:1px}
.m{font-size:10.5px;color:var(--faint);padding:3px 5px;display:flex;gap:6px;
  align-items:center;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
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
.internal>.hdr{background:rgba(182,156,255,.14);padding:7px 13px;font-size:10px;
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
.crow .why{grid-column:2/-1;color:var(--white);font-size:14.5px;margin-top:6px;
  line-height:1.45;font-family:var(--serif)}
.crow .src{grid-column:2/-1;margin-top:8px;display:flex;gap:5px;flex-wrap:wrap}
.crow .src a{font-size:10px;color:var(--faint);border:1px solid var(--rule);
  padding:2px 7px;text-decoration:none;max-width:230px;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.crow .src a:hover{color:var(--violet);border-color:var(--violet)}
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
var CAPS={wants:'wants',picked:'picked',haggled:'haggled',gate:'the gate',
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
  --paper:#F2F5FA; --card:#FFF; --line:#E1E7F2; --edge:#CFD8E8;
  --ink:#111826; --body:#3D4759; --mute:#6B7A94; --pale:#93A0B6;
  --brand:#2B5CE6; --brandsoft:#EAF0FE;
  --money:#0B7A57; --moneysoft:#E6F5EF;
  --warn:#C2410C; --warnsoft:#FDF0E7;
  --stop:#CE3226; --stopsoft:#FCEDEB;
  --violet:#6D5BD0; --violetsoft:#F0EDFB;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",
    sans-serif;
  --serif:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
  --shadow:0 1px 2px rgba(17,24,38,.05),0 4px 14px rgba(17,24,38,.05);
}
*{box-sizing:border-box}
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
.chip{background:var(--card);border:1px solid var(--edge);border-radius:7px;
  padding:5px 11px;font-size:12px;color:var(--pale);cursor:not-allowed}

/* --- catalogue ----------------------------------------------------------- */
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
        f'{esc(a[2:].replace("_", " ").title())}</option>'
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
        f'<p>{esc(actor_id)} &middot; {esc(view["plan"])} plan &middot; '
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
                f'<td class="{"a" if n in (0, 7) else ""}">{esc(cell)}</td>'
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
        '<div class="connect"><b>These books sync to a Google Sheet.</b> '
        'The grid above is exactly what gets pushed &mdash; one tab per '
        'merchant, replaced on every run, because the books are a projection '
        'of a log that is the only thing allowed to accumulate. Run '
        '<code>python -m scripts.market.sheets --sheet</code> '
        'with a service-account key in <code>.env</code>; without one the same '
        'command writes CSVs that open in Sheets as they are.</div>'
        "</div></section>")


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

    mgrid = "".join(f'<div class="m" data-m="{esc(m)}"><i></i>{esc(m[2:])}</div>'
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
        + _board_html(board(events), auction(events), summary))

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
          '<a class="nav" href="replay.html">&larr; exchange</a>'
          "</span></div>"

        + f'<main><section class="vw" id="vw-live" data-on>{live}</section>'
          f'<section class="vw" id="vw-board">{boardv}</section></main>'

        + _footer(db_path, summary)
        + f'<script type="application/json" id="mkt">{payload}</script>'
        + DESK_JS + "</body></html>"
    )


def _board_html(desk, sale, summary) -> str:
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
                f'<div class="crow"><span class="rk">{row["rank"]}</span>'
                f'<span class="nm">{esc(row["campaign"])}</span>'
                f'<span class="mv">{movement}</span>'
                f'<span class="mc">{row["merchants"]} merchants</span>'
                f'<span class="vl">{rupees(row["value_paise"])}</span>'
                f'<div class="why">{esc(row.get("driver", ""))}</div>'
                f'<div class="src">{sources}</div></div>')
        refused = desk["refused"]
        refusal = (
            f'<div class="refused">{len(refused)} campaigns refused a place on '
            f'this board — fewer distinct merchants than the floor of '
            f'{refused[0].get("floor")} allows. A floor nobody can see is '
            f'indistinguishable from no floor.</div>') if refused else ""
        board_html = (
            '<div class="internal"><div class="hdr">'
            "<b>Trending client campaigns</b>"
            '<span>ranking computed from the log &middot; '
            'explanations sourced from the public press</span></div>'
            f"{rows}{refusal}</div>")
    else:
        board_html = ('<div class="empty">No campaign board published. Run '
                      'scripts.market.research over this log.</div>')

    if sale:
        bids = "".join(
            f'<tr><td class="a">{esc(e.actor_id)}</td>'
            f'<td>{esc(e.payload.get("amount"))}</td>'
            f'<td class="q">{esc(str(e.payload.get("reason", ""))[:130])}</td>'
            f"</tr>"
            for e in sale["bids"])
        paid = (sale["royalties"][0].payload.get("amount")
                if sale["royalties"] else 0)
        return board_html + _panel(
            "the lot that went to auction",
            f'<p style="margin:0 0 13px;font-family:var(--serif);'
            f'font-size:17px">&ldquo;{esc(sale["headline"])}&rdquo;</p>'
            f'<table><tr><th>bidder</th><th>points</th>'
            f'<th>why they valued it there</th></tr>{bids}</table>'
            f'<p class="note" style="margin-bottom:0">'
            f'<b>{esc(sale["winner"])}</b> won and paid '
            f'<b>{esc(sale["price"])}</b> points — the runner-up&rsquo;s bid, '
            f'not its own. {len(sale["royalties"])} contributing merchants each '
            f'earned <b>{esc(paid)}</b> points from a win they did not know was '
            f'being sold.</p>',
            note="sealed bids, second price")

    return board_html + (
        f'<div class="note">The privacy floor refused: only '
        f'{summary.distinct_traders} merchants contributed. A floor that '
        f'refuses is the control working.</div>')


# =============================================================================
#  THE FRONT DOOR
# =============================================================================
#
# THE PAGE IS SPLIT INTO THE TWO WORLDS IT LEADS TO. Dark is the machine side,
# where agents haggle and the house watches the whole book. Light is the
# merchant side. You scroll out of one and into the other, and each door sits
# inside its own world — so clicking through is visually continuous rather
# than a jump. The inversion is the structure, and it encodes the product's
# actual split rather than decorating it.
#
# THE HERO IS A REAL NEGOTIATION. Two agents converging over seven offers,
# quoted from the log, ending in the gate's ruling and the payment id Razorpay
# returned. It is the most characteristic thing in this world and the hardest
# to believe, so it goes first and it is real: the copy on this page contains
# no sentence a machine did not actually say.
#
# The display face is Fraunces, vendored under the OFL and embedded as a data
# URI so the page renders from disk with no network. An organic, high-contrast
# serif over monospaced machine data is the tension of the whole product:
# agents that argue in English about money that moves like clockwork.


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
    "chart": ('<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>'),
    "arrow": '<path d="M5 12h13M12 6l6 6-6 6"/>',
}


def _icon(name: str, size: int = 20) -> str:
    return (f'<svg class="ic" width="{size}" height="{size}" viewBox="0 0 24 24"'
            f' fill="none" stroke="currentColor" stroke-width="1.5"'
            f' stroke-linecap="round" stroke-linejoin="round"'
            f' aria-hidden="true">{ICONS[name]}</svg>')


LANDING_CSS = """
@font-face{
  font-family:'Fraunces Display';
  src:url(__FONT__) format('woff2');
  font-weight:400 900;font-style:normal;font-display:swap;
}
:root{
  --void:#0B0C10; --deep:#131519; --line:#23262E; --edge:#343945;
  --paper:#F4F2ED; --card:#FFF; --pline:#E3E0D8;
  --bright:#F7F8FA; --mid:#9BA3B4; --low:#828B9C;
  --ink:#14161B; --body:#404757; --soft:#6E7686;
  --ember:#FF8A3D; --mint:#34E2A0; --flare:#FF4D5E; --iris:#9C8CFF;
  --disp:'Fraunces Display',"Iowan Old Style",Georgia,serif;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--void);color:var(--bright);
  font-family:var(--sans);font-size:16px;line-height:1.6;
  -webkit-font-smoothing:antialiased}
/* The browser's own surfaces belong to the design too. */
::selection{background:var(--ember);color:#000}
:focus-visible{outline:2px solid var(--ember);outline-offset:3px}
.dark{scrollbar-color:var(--edge) var(--void)}
h1,h2,h3{font-family:var(--disp);font-weight:600;letter-spacing:-.025em;
  margin:0;text-wrap:balance}
.ic{flex:none}

.band{padding:clamp(52px,7vw,92px) clamp(20px,5vw,64px)}
.in{max-width:1180px;margin:0 auto}
.dark{background:var(--void);color:var(--bright)}
.light{background:var(--paper);color:var(--ink)}
/* The seam between the two worlds, cut rather than faded. */
.light{border-top:1px solid var(--line)}

/* --- hero ---------------------------------------------------------------- */
.hero{display:grid;grid-template-columns:minmax(0,1.02fr) minmax(0,.98fr);
  gap:clamp(30px,4vw,60px);align-items:center;padding-top:clamp(8px,2vw,26px)}
@media(max-width:940px){.hero{grid-template-columns:1fr}}
.hero h1{font-size:clamp(42px,6.2vw,80px);line-height:1;
  letter-spacing:-.038em}
.hero h1 em{font-style:italic;color:var(--ember)}
.hero .say{margin:26px 0 0;color:var(--mid);font-size:clamp(16px,1.5vw,18.5px);
  max-width:46ch;line-height:1.62}
.tape{margin-top:28px;padding-top:18px;border-top:1px solid var(--line);
  font-family:var(--mono);font-size:12px;letter-spacing:.02em;
  color:var(--low);display:flex;flex-wrap:wrap;gap:6px 20px}
.tape b{color:var(--bright);font-weight:600}
.tape .m{color:var(--ember)}

/* --- the signature: a negotiation, played --------------------------------- */
.deal{background:var(--deep);border:1px solid var(--line);border-radius:14px;
  overflow:hidden;box-shadow:0 26px 60px -24px rgba(0,0,0,.85),
    0 2px 0 rgba(255,255,255,.03) inset}
.deal .dh{display:flex;align-items:center;gap:10px;padding:12px 16px;
  border-bottom:1px solid var(--line);font-family:var(--mono);font-size:10.5px;
  letter-spacing:.15em;text-transform:uppercase;color:var(--low)}
.deal .dh .dot{width:6px;height:6px;border-radius:50%;background:var(--mint)}
.deal .dh .want{margin-left:auto;color:var(--mid);letter-spacing:.04em;
  text-transform:none;font-size:11.5px;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap;max-width:52%}
/* Fixed, not growing. The card sits beside a headline and must not push
   the page around as lines arrive; old offers scroll off the top instead. */
.feed{padding:14px 16px;height:clamp(300px,44vh,392px);display:flex;
  flex-direction:column;gap:9px;justify-content:flex-end;overflow:hidden}
.line{display:grid;grid-template-columns:1fr auto;gap:14px;
  align-items:baseline;padding:9px 12px;border-radius:9px;
  background:#191C22;border:1px solid transparent}
.line.b{background:#171A20}
.line .who{font-family:var(--mono);font-size:10.5px;color:var(--low);
  display:block;margin-bottom:3px;letter-spacing:.03em}
.line .said{font-size:13.5px;color:var(--mid);line-height:1.42;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;
  overflow:hidden}
.line .px{font-family:var(--mono);font-size:19px;color:var(--bright);
  font-variant-numeric:tabular-nums;white-space:nowrap}
.line.deal-done{border-color:var(--mint);background:rgba(52,226,160,.07)}
.line.deal-done .px{color:var(--mint)}
.stamp{display:grid;grid-template-columns:1fr auto;gap:14px;
  align-items:center;padding:11px 12px;border-radius:9px;
  border:1px solid var(--line)}
.stamp .lb{font-family:var(--mono);font-size:10px;letter-spacing:.15em;
  text-transform:uppercase;color:var(--low)}
.stamp .vl{font-family:var(--mono);font-size:13px;color:var(--mid);
  overflow-wrap:anywhere}
.stamp.gate{border-color:rgba(52,226,160,.34)}
.stamp.gate .vl{color:var(--mint)}
.stamp.paid{border-color:rgba(255,138,61,.34)}
.stamp.paid .vl{color:var(--ember);font-size:15px}
@media(prefers-reduced-motion:no-preference){
  .line,.stamp{animation:step .5s cubic-bezier(.16,1,.3,1) both}
  @keyframes step{
    from{opacity:0;transform:translateY(9px);filter:blur(4px)}
    to{opacity:1;transform:none;filter:blur(0)}}
}

/* --- the six steps -------------------------------------------------------- */
.steps{display:grid;grid-template-columns:repeat(6,1fr);
  border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
@media(max-width:860px){.steps{grid-template-columns:repeat(2,1fr)}}
.st{padding:22px 20px 26px;border-left:1px solid var(--line)}
.st:first-child{border-left:0}
@media(max-width:860px){.st:nth-child(odd){border-left:0}}
.st .n{font-family:var(--mono);font-size:10.5px;color:var(--ember);
  letter-spacing:.1em}
.st .t{font-family:var(--disp);font-size:19px;margin-top:8px;
  letter-spacing:-.02em}
.st .d{font-size:13.5px;color:var(--low);margin-top:6px;line-height:1.5}
.leadin{max-width:52ch;margin:0 0 34px}
.leadin h2{font-size:clamp(28px,3.6vw,40px);line-height:1.08}
.leadin p{color:var(--mid);margin:14px 0 0;font-size:16.5px}
.light .leadin p{color:var(--soft)}

/* --- the doors ------------------------------------------------------------ */
.door{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,1fr);
  gap:clamp(22px,3vw,44px);align-items:start}
@media(max-width:860px){.door{grid-template-columns:1fr}}
.sell h2{font-size:clamp(32px,4.4vw,52px);line-height:1.04;
  letter-spacing:-.032em}
.sell p{font-size:17px;line-height:1.62;margin:18px 0 0;max-width:50ch}
.light .sell p{color:var(--body)}
.dark .sell p{color:var(--mid)}
.pts{list-style:none;margin:26px 0 0;padding:0;display:grid;gap:14px}
.pts li{display:grid;grid-template-columns:auto 1fr;gap:12px;
  align-items:start;font-size:15px;line-height:1.5}
.light .pts li{color:var(--body)}
.dark .pts li{color:var(--mid)}
.pts .ic{margin-top:3px}
.light .pts .ic{color:var(--ink)}
.dark .pts .ic{color:var(--iris)}

.enter{margin-top:30px;display:inline-flex;align-items:center;gap:11px;
  text-decoration:none;
  font-size:16px;font-weight:600;padding:15px 22px;border-radius:11px;
  transition:transform .22s cubic-bezier(.16,1,.3,1),box-shadow .22s}
.enter .ic{transition:transform .22s cubic-bezier(.16,1,.3,1)}
.enter:hover .ic{transform:translateX(4px)}
.light .enter{background:var(--ink);color:var(--paper);
  box-shadow:0 12px 26px -12px rgba(20,22,27,.6)}
.light .enter:hover{transform:translateY(-2px);
  box-shadow:0 18px 34px -12px rgba(20,22,27,.55)}
.dark .enter{background:var(--iris);color:#0B0C10;
  box-shadow:0 14px 34px -14px rgba(156,140,255,.7)}
.dark .enter:hover{transform:translateY(-2px)}

/* The proof panel beside each pitch: a real fragment of that world. */
.proof{border-radius:13px;overflow:hidden;font-family:var(--mono)}
.light .proof{background:var(--card);border:1px solid var(--pline);
  box-shadow:0 1px 2px rgba(20,22,27,.05),0 14px 34px -20px rgba(20,22,27,.28)}
.dark .proof{background:#000;border:1px solid var(--line)}
.proof .ph{padding:9px 14px;font-size:10px;letter-spacing:.15em;
  text-transform:uppercase}
.light .proof .ph{color:var(--soft);border-bottom:1px solid var(--pline)}
.dark .proof .ph{color:var(--iris);border-bottom:1px solid var(--line)}
.proof .pb{padding:6px 14px 14px}
.pr{display:grid;grid-template-columns:1fr auto;gap:12px;padding:8px 0;
  font-size:12.5px;align-items:baseline}
.light .pr{border-bottom:1px solid var(--pline);color:var(--body)}
.dark .pr{border-bottom:1px solid var(--line);color:var(--mid)}
.pr:last-child{border-bottom:0}
.pr .v{font-variant-numeric:tabular-nums;white-space:nowrap}
.light .pr .v{color:var(--ink);font-weight:600}
.dark .pr .v{color:var(--ember)}
.pr .rk{color:var(--iris);margin-right:9px}
.pr .nm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

foot{display:block}
footer{padding:26px clamp(20px,5vw,64px);border-top:1px solid var(--line);
  color:var(--low);font-family:var(--mono);font-size:11px;line-height:1.8;
  background:var(--void)}
@media(prefers-reduced-motion:reduce){*{animation:none!important;
  transition:none!important;scroll-behavior:auto}}
"""


LANDING_JS = r"""<script>
(function(){
/* The negotiation plays once, at reading speed, and stops. It is the page's
   one authored moment: everything else is still, so this is where the eye
   goes. Every line is quoted from the log — the page does not simulate a
   deal, it replays one. */
var D=JSON.parse(document.getElementById('deal').textContent);
var feed=document.getElementById('feed'),n=0;
function esc(t){var d=document.createElement('div');
  d.textContent=t==null?'':t;return d.innerHTML}
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
  if(n<D.lines.length)setTimeout(add,L.hold||1500);
}
if(matchMedia('(prefers-reduced-motion: reduce)').matches){
  while(n<D.lines.length)add.call(null);
}else{
  setTimeout(add,420);
}
})();
</script>"""


def build_landing(db_path: str, roster) -> str:
    summary, _trades, events = load(db_path)
    failures = failure_threads(events)
    desk = board(events)
    first = _page_name(roster[0])

    # The hero deal: the longest real negotiation that reached a confirmed
    # payment. Found by shape, so a different run still finds its own best
    # story rather than showing an empty box.
    rail_map = rails(events)
    candidates = [r for r in rail_map.values()
                  if len(r["talk"]) >= 4 and any(
                      s["key"] == "paid" and any(
                          str(x).startswith("pay_") for x in s["lines"])
                      for s in r["stations"])]
    hero = max(candidates, key=lambda r: len(r["talk"]), default=None)
    deal = _deal_payload(hero)

    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="dark light">'
        '<title>Agent Exchange</title>'
        f'<style>{LANDING_CSS.replace("__FONT__", _font_data_uri())}</style>'
        "</head><body>"

        # --- the machines' world -------------------------------------------
        + '<section class="band dark"><div class="in hero"><div>'
        + "<h1>Machines that haggle, and money that <em>proves it</em>.</h1>"
        + '<p class="say">Every Razorpay merchant gets an agent. The agents '
          'find each other, argue about price in plain English, and settle '
          'real payments — and not one rupee moves until a policy gate has '
          'recorded its ruling.</p>'
        + f'<div class="tape">'
          f'<span><b class="m">{rupees(summary.value_paise)}</b> settled '
          f'between agents</span>'
          f'<span><b>{summary.merchants}</b> merchants</span>'
          f'<span><b>{summary.gate_allow + summary.gate_deny}</b> rulings '
          f'recorded</span>'
          f'<span><b>{len(failures)}</b> mismatches repaired</span></div>'
        + f"</div>{_deal_card(hero)}</div></section>"

        # --- what happens to every trade ------------------------------------
        + '<section class="band dark" style="padding-top:0"><div class="in">'
        + '<div class="leadin"><h2>Every trade takes the same six steps.</h2>'
          '<p>You learn the shape once. A trade that breaks grows three more '
          'steps out of the fifth one; a trade a person typed differs only at '
          'the first.</p></div>'
        + _steps_html()
        + "</div></section>"

        # --- the merchants' world -------------------------------------------
        + '<section class="band light"><div class="in door"><div class="sell">'
        + "<h2>Your agents, and your books.</h2>"
        + "<p>See what your broker did with your money, then check any of it "
          "against the payment ids Razorpay returned. Every figure carries the "
          "number of the event it came from.</p>"
        + '<ul class="pts">'
          f'<li>{_icon("chart")}<span>What each of your four agents did, in '
          f'its own words</span></li>'
          f'<li>{_icon("ledger")}<span>Every buy and sell, kept as books that '
          f'sync to a Google Sheet</span></li>'
          f'<li>{_icon("shield")}<span>Your catalogue, and one box to ask for '
          f'anything</span></li></ul>'
        + f'<a class="enter" href="{esc(first)}">Open your dashboard'
          f'{_icon("arrow", 18)}</a>'
        + f"</div>{_books_proof(events, roster)}</div></section>"

        # --- the house's world ----------------------------------------------
        + '<section class="band dark"><div class="in door"><div class="sell">'
        + "<h2>The desk.</h2>"
        + "<p>The live floor, and the board that ranks which campaigns are "
          "climbing across the whole client base. Staff only — that ranking is "
          "the one thing a merchant cannot see for itself, which is exactly "
          "why it is worth something.</p>"
        + '<ul class="pts">'
          f'<li>{_icon("chart")}<span>Every agent and every ruling, as it '
          f'happens</span></li>'
          f'<li>{_icon("ledger")}<span>Trending client campaigns, ranked from '
          f'the whole book</span></li>'
          f'<li>{_icon("shield")}<span>The sealed-bid auction that sells '
          f'them</span></li></ul>'
        + f'<a class="enter" href="desk.html">Enter the desk'
          f'{_icon("arrow", 18)}</a>'
        + f"</div>{_board_proof(desk)}</div></section>"

        + '<footer>Every figure on this page is backed by a numbered event on '
          'the audit trail. The negotiation above is quoted, not written.'
          "</footer>"

        + f'<script type="application/json" id="deal">{deal}</script>'
        + LANDING_JS + "</body></html>"
    )


def _deal_card(hero) -> str:
    if hero is None:
        return ""
    return (
        '<div class="deal"><div class="dh"><span class="dot"></span>'
        'live negotiation'
        f'<span class="want">{esc(hero["need"])}</span></div>'
        '<div class="feed" id="feed"></div></div>')


def _deal_payload(hero) -> str:
    """The hero negotiation, as lines the page plays.

    Prices are converted from paise once, here, so the page never does
    arithmetic on money it was handed.
    """
    if hero is None:
        return json.dumps({"lines": []})

    lines = []
    for n, turn in enumerate(hero["talk"]):
        said = str(turn["said"])
        # the model prefixes its own offer; the number is already in the price
        said = said.split("—", 1)[-1].split(" - ", 1)[-1].strip()
        lines.append({
            "kind": "offer",
            "who": turn["who"],
            "px": f'₹{(turn["price"] or 0) / 100:,.2f}',
            "said": _clip_words(said, 104),
            "done": n == len(hero["talk"]) - 1,
            "hold": 1700,
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


def _steps_html() -> str:
    steps = (
        ("wants", "A merchant needs something, in plain words."),
        ("picks", "Three sellers surface; the agent argues for one."),
        ("haggles", "Offers go back and forth until somebody yields."),
        ("the gate", "The ruling is recorded before any money moves."),
        ("pays", "A real Razorpay order, and a real payment id back."),
        ("remembers", "One sentence kept, read before the next deal."),
    )
    return '<div class="steps">' + "".join(
        f'<div class="st"><div class="n">{n + 1}</div>'
        f'<div class="t">{esc(name)}</div>'
        f'<div class="d">{esc(why)}</div></div>'
        for n, (name, why) in enumerate(steps)) + "</div>"


def _books_proof(events, roster) -> str:
    """A real merchant's books, as the fragment that sells the merchant page."""
    from exchange.books import entries_for

    best = max((entries_for(events, a) for a in roster),
               key=lambda b: len(b.entries), default=None)
    if best is None or not best.entries:
        return ""
    rows = "".join(
        f'<div class="pr"><span class="nm">{esc(_clip_words(e.item, 32))}'
        f"</span>"
        f'<span class="v">₹{e.amount_inr:,.0f}</span></div>'
        for e in best.entries[:5])
    return (
        '<div class="proof"><div class="ph">'
        f'{esc(best.actor_id[2:].replace("_", " "))} &middot; books</div>'
        f'<div class="pb">{rows}'
        f'<div class="pr"><span class="nm">confirmed by Razorpay</span>'
        f'<span class="v">₹{best.settled_inr:,.0f}</span></div>'
        "</div></div>")


def _board_proof(desk) -> str:
    if not desk:
        return ""
    rows = "".join(
        f'<div class="pr"><span class="nm"><span class="rk">'
        f'{row["rank"]}</span>{esc(row["campaign"])}</span>'
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
    (out / "index.html").write_text(build_landing(db, roster), encoding="utf-8")

    print(f"wrote index.html + desk.html + {written} merchant pages "
          f"(and replay.html) to {out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
