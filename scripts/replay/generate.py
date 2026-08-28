"""Turn a market log into one self-contained HTML page.

    .venv/bin/python -m scripts.replay.generate runs/market.db docs/replay.html

No server, no build step, no network at view time. It opens from disk, on any
machine, in a year — which matters because the page's whole claim is that a
reader can check it, and a page that needs infrastructure to render is a page
that will eventually stop rendering.

ONE THING ON SCREEN AT A TIME. The page was seven panes at once and it was
unreadable — the viewer's eye had nowhere to land, and a judge watching a
recording cannot choose where to look. Now it is six scenes and you are only
ever in one. Every feature survived; none of them share a screen.

They run on ONE CLOCK. Switching scenes does not restart anything: the tape
plays, and each scene is a different readout of the same moment. That shared
clock is what makes this a terminal rather than six charts on a background.

DESIGN RULE, applied everywhere below: where legibility and credibility
conflict, the raw event wins. Prices are shown as the log records them,
reasoning is quoted rather than paraphrased, and nothing on the page is
computed here that `fold` could compute instead.
"""
from __future__ import annotations

import html
import json
import sys
from datetime import datetime, timezone

from scripts.replay.read import (
    auction,
    board,
    catalogue,
    failure_threads,
    lessons,
    load,
    storefront,
    tape,
)


def state_actors(events):
    return {e.actor_id for e in events if e.actor_id.startswith("m_")}


def arc_seen(thread, arc):
    """Which steps of a story a thread actually contains."""
    types = {e.type for e in thread}
    return [t for t in arc if t in types]


def esc(text) -> str:
    return html.escape(str(text if text is not None else ""))


def rupees(paise) -> str:
    return f"₹{(paise or 0) / 100:,.0f}"


# --- the visual world --------------------------------------------------------
#
# A dealing room at night, not a dashboard. The ground is near-black with a
# blue cast, data is monospaced because every figure here is meant to be read
# against another figure, and colour carries exactly four meanings and no
# decoration: allowed, refused, money, and the house.
#
# Violet is reserved. It appears only on the internal campaign board and
# nowhere else on the page, so the one screen merchants cannot see is the one
# screen that does not look like the others.
#
# Fonts are system stacks on purpose. The page is recorded to video, often
# offline, and a webfont that fails to load mid-take is a re-shoot.

CSS = """
:root{
  --ink:#0A0C10; --panel:#11141A; --raise:#171B22; --line:#242A34;
  --text:#CBD3DE; --dim:#7A8595; --faint:#4C5768;
  --allow:#3FBF9C; --deny:#E06055; --money:#E0A83C; --house:#9B7BF0;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
}
*{box-sizing:border-box}
html,body{margin:0;height:100%}
body{background:var(--ink);color:var(--text);font-family:var(--sans);
  font-size:15px;line-height:1.5;-webkit-font-smoothing:antialiased;
  display:flex;flex-direction:column;overflow:hidden}
@media(max-width:900px){body{overflow:auto;height:auto}}

/* --- the fixed head: identity, scenes, transport, clock ------------------ */
.bar{display:flex;align-items:center;gap:14px;padding:0 18px;height:44px;
  border-bottom:1px solid var(--line);background:var(--panel);flex:none;
  font-family:var(--mono);font-size:11.5px;letter-spacing:.08em;
  text-transform:uppercase;color:var(--faint)}
.mark{color:var(--text);font-weight:600;letter-spacing:.16em}
.bar .sep{color:var(--line)}
.sealed{margin-left:auto;display:flex;align-items:center;gap:7px;
  color:var(--allow)}
.sealed i{width:6px;height:6px;border-radius:50%;background:var(--allow);
  display:block}

.scenes{display:flex;gap:2px;padding:0 12px;background:var(--panel);
  border-bottom:1px solid var(--line);flex:none;overflow-x:auto}
.tab{appearance:none;background:none;border:0;border-bottom:2px solid
  transparent;color:var(--dim);font-family:var(--mono);font-size:12px;
  letter-spacing:.12em;text-transform:uppercase;padding:11px 14px 9px;
  cursor:pointer;white-space:nowrap;display:flex;gap:8px;align-items:baseline}
.tab b{color:var(--faint);font-weight:500;font-size:10px}
.tab:hover{color:var(--text)}
.tab[aria-selected=true]{color:var(--text);border-bottom-color:var(--money)}
.tab[aria-selected=true] b{color:var(--money)}
.tab:focus-visible{outline:2px solid var(--money);outline-offset:-2px}
.tab[data-scene=desk][aria-selected=true]{border-bottom-color:var(--house)}
.tab[data-scene=desk][aria-selected=true] b{color:var(--house)}

.transport{display:flex;align-items:center;gap:8px;padding:8px 18px;
  background:var(--panel);border-bottom:1px solid var(--line);flex:none}
.tbtn{appearance:none;background:var(--raise);border:1px solid var(--line);
  color:var(--dim);font-family:var(--mono);font-size:11px;letter-spacing:.1em;
  text-transform:uppercase;padding:5px 12px;border-radius:3px;cursor:pointer}
.tbtn:hover{color:var(--text);border-color:var(--dim)}
.tbtn[aria-pressed=true]{color:var(--ink);background:var(--money);
  border-color:var(--money);font-weight:600}
.tbtn:focus-visible{outline:2px solid var(--money);outline-offset:2px}
.clock{margin-left:auto;font-family:var(--mono);font-size:11px;
  color:var(--faint);letter-spacing:.08em;text-transform:uppercase}
.clock b{color:var(--money);font-weight:600}
/* THE SIGNATURE. One hairline across the whole head, shared by every scene.
   It is the only element that never changes when you switch tabs, which is
   how the page says: same run, same moment, different window onto it. */
.progress{height:2px;background:var(--line);flex:none;position:relative}
.progress i{position:absolute;inset:0 auto 0 0;width:0;background:var(--money);
  display:block;transition:width .18s linear}

/* --- scenes -------------------------------------------------------------- */
main{flex:1;min-height:0;position:relative}
.scene{position:absolute;inset:0;display:none;flex-direction:column;
  padding:22px 26px 18px;overflow:auto}
.scene[data-active]{display:flex}
@media(max-width:900px){
  main{position:static}
  .scene{position:static;padding:18px 14px}
}
.headline{font-size:25px;line-height:1.3;font-weight:400;margin:0 0 6px;
  max-width:62ch;letter-spacing:-.01em}
.headline em{font-style:normal;color:var(--money)}
.sub{margin:0 0 20px;color:var(--dim);font-size:14px;max-width:74ch}
.sub code{font-family:var(--mono);font-size:12.5px;color:var(--text)}

.stats{display:flex;gap:34px;flex-wrap:wrap;margin:0 0 22px;
  padding-bottom:20px;border-bottom:1px solid var(--line)}
.stat .v{font-family:var(--mono);font-size:27px;color:var(--text);
  letter-spacing:-.02em;line-height:1.1}
.stat .l{font-size:11px;letter-spacing:.1em;text-transform:uppercase;
  color:var(--faint);margin-top:5px}
.stat.money .v{color:var(--money)}
.stat.allow .v{color:var(--allow)}
.stat.deny .v{color:var(--deny)}
.stat.house .v{color:var(--house)}

.cols{display:grid;gap:16px;flex:1;min-height:0}
.c-floor{grid-template-columns:minmax(0,1.9fr) minmax(280px,1fr)}
.c-gate{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}
@media(max-width:900px){.cols{grid-template-columns:1fr!important}}
.stack{display:flex;flex-direction:column;gap:16px;min-height:0}
/* Both children were content-sized inside a column that already had a fixed
   height, so the second one was simply clipped off the bottom. The roster is
   allowed to take what it needs up to a share of the column; the negotiation
   takes the rest, because it is the pane that keeps growing. */
.stack>.box:first-child{flex:0 1 auto;max-height:55%}
.stack>.box:last-child{flex:1 1 0;min-height:120px}

.box{background:var(--panel);border:1px solid var(--line);border-radius:5px;
  display:flex;flex-direction:column;min-height:0;overflow:hidden}
.box>h2{margin:0;padding:9px 13px;font-family:var(--mono);font-size:10.5px;
  letter-spacing:.14em;text-transform:uppercase;color:var(--faint);
  border-bottom:1px solid var(--line);font-weight:500;display:flex;
  justify-content:space-between;gap:10px;align-items:center;flex:none}
.box>h2 span{color:var(--dim)}
.body{overflow:auto;padding:10px 13px;flex:1;min-height:0}
.body.flush{padding:0}
@media(max-width:900px){.body{max-height:44vh}}

/* --- the tape ------------------------------------------------------------ */
.tape{font-family:var(--mono);font-size:12.5px}
.row{display:grid;grid-template-columns:52px 150px 1fr;gap:10px;
  padding:3px 13px;border-left:2px solid transparent;align-items:baseline}
.row .s{color:var(--faint);font-size:11px}
.row .w{color:var(--text);overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap}
.row .x{color:var(--dim);white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.row.allow{border-left-color:var(--allow)}
.row.deny{border-left-color:var(--deny)}
.row.amber{border-left-color:var(--money)}
.row.new{background:#1B2029}
@media(prefers-reduced-motion:no-preference){.row.new{transition:background .5s}}
.row .x b{color:var(--text);font-weight:400}

/* --- merchants ----------------------------------------------------------- */
.mgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(112px,1fr));
  gap:3px}
.m{font-family:var(--mono);font-size:10.5px;color:var(--faint);padding:3px 5px;
  border-radius:3px;display:flex;gap:6px;align-items:center;overflow:hidden;
  white-space:nowrap;text-overflow:ellipsis}
.m i{width:5px;height:5px;border-radius:50%;background:var(--line);flex:none}
.m.act{color:var(--text);background:var(--raise)}
.m.act i{background:var(--allow)}
.m.frz{color:var(--deny)}
.m.frz i{background:var(--deny)}

/* --- negotiation --------------------------------------------------------- */
.nline{display:grid;grid-template-columns:auto auto 1fr;gap:9px;
  padding:5px 0;border-bottom:1px solid var(--line);font-size:13px;
  align-items:baseline}
.nline .who{font-family:var(--mono);font-size:11px;color:var(--dim)}
.nline .px{font-family:var(--mono);color:var(--money)}
.nline .msg{color:var(--text)}

/* --- the gate ------------------------------------------------------------ */
.grow{display:grid;grid-template-columns:62px 1fr;gap:10px;padding:4px 0;
  font-family:var(--mono);font-size:12px;border-bottom:1px solid var(--line);
  align-items:baseline}
.verdict{font-weight:600;letter-spacing:.06em}
.allow,.verdict.allow{color:var(--allow)}
.deny,.verdict.deny{color:var(--deny)}
.grow .msg{color:var(--dim);overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap}

/* --- the break: a stepper ------------------------------------------------ */
.steps{display:flex;gap:0;flex-wrap:wrap;margin-bottom:20px}
.step{flex:1;min-width:150px;padding:13px 15px;background:var(--panel);
  border:1px solid var(--line);border-right:0;position:relative}
.step:last-child{border-right:1px solid var(--line)}
.step .n{font-family:var(--mono);font-size:10px;letter-spacing:.14em;
  color:var(--faint);text-transform:uppercase}
.step .t{font-family:var(--mono);font-size:13px;margin-top:5px}
.step .d{font-size:12.5px;color:var(--dim);margin-top:5px;line-height:1.4}
.step.bad .t{color:var(--deny)}
.step.good .t{color:var(--allow)}

/* --- tables -------------------------------------------------------------- */
table{width:100%;border-collapse:collapse;font-size:12.5px}
th{text-align:left;font-family:var(--mono);font-size:10px;letter-spacing:.12em;
  text-transform:uppercase;color:var(--faint);font-weight:500;
  padding:6px 10px 6px 0;border-bottom:1px solid var(--line)}
td{padding:5px 10px 5px 0;border-bottom:1px solid var(--line);
  vertical-align:top}
td.mono,td.a,td.p{font-family:var(--mono)}
td.a{color:var(--dim);font-size:11.5px;white-space:nowrap}
td.p{color:var(--faint);font-size:11px}
td.t{font-family:var(--mono);font-size:11px;color:var(--dim);white-space:nowrap}

/* --- the desk: violet, and only here ------------------------------------- */
.internal{border:1px solid var(--house);border-radius:5px;overflow:hidden;
  margin-bottom:20px}
.internal>.hdr{background:rgba(155,123,240,.12);padding:8px 14px;
  font-family:var(--mono);font-size:10.5px;letter-spacing:.14em;
  text-transform:uppercase;color:var(--house);display:flex;gap:12px;
  justify-content:space-between;flex-wrap:wrap}
.internal>.hdr b{font-weight:600}
.internal>.hdr span{color:var(--dim);letter-spacing:.08em}
.crow{display:grid;grid-template-columns:34px 1fr 74px 92px 108px;gap:12px;
  padding:11px 14px;border-top:1px solid var(--line);align-items:baseline}
.crow .rk{font-family:var(--mono);font-size:17px;color:var(--house)}
.crow .nm{font-size:15px}
.crow .mv,.crow .mc,.crow .vl{font-family:var(--mono);font-size:13px;
  text-align:right}
.crow .mv{color:var(--money)}
.crow .mc,.crow .vl{color:var(--dim)}
.crow .why{grid-column:2/-1;color:var(--dim);font-size:13px;margin-top:5px;
  line-height:1.45}
.crow .src{grid-column:2/-1;margin-top:7px;display:flex;gap:7px;flex-wrap:wrap}
.crow .src a{font-family:var(--mono);font-size:10.5px;color:var(--faint);
  border:1px solid var(--line);border-radius:3px;padding:2px 7px;
  text-decoration:none;max-width:250px;overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap}
.crow .src a:hover{color:var(--house);border-color:var(--house)}
@media(max-width:900px){
  .crow{grid-template-columns:28px 1fr 60px}
  .crow .mc,.crow .vl{display:none}
}
.refused{padding:10px 14px;border-top:1px solid var(--line);
  color:var(--deny);font-size:12.5px;font-family:var(--mono)}

/* --- the shop ------------------------------------------------------------ */
.ask{display:flex;gap:9px;margin-bottom:16px;max-width:660px}
.ask input{flex:1;background:var(--panel);border:1px solid var(--line);
  border-radius:4px;color:var(--text);font-family:var(--sans);font-size:15px;
  padding:11px 14px}
.ask input:focus{outline:none;border-color:var(--money)}
.ask button{background:var(--money);border:0;border-radius:4px;
  color:var(--ink);font-family:var(--mono);font-size:12px;font-weight:600;
  letter-spacing:.1em;text-transform:uppercase;padding:0 20px;cursor:pointer}
#sc-shop .body{min-height:168px}
.hit{display:grid;grid-template-columns:1fr auto auto;gap:14px;padding:8px 0;
  border-bottom:1px solid var(--line);font-size:13.5px;align-items:baseline}
.hit .sl{font-family:var(--mono);font-size:11px;color:var(--dim)}
.hit .pr{font-family:var(--mono);color:var(--money)}

/* --- memory -------------------------------------------------------------- */
.q{background:var(--panel);border-left:2px solid var(--line);
  padding:11px 15px;margin-bottom:9px;font-size:14px;line-height:1.5}
.q .by{font-family:var(--mono);font-size:10.5px;color:var(--faint);
  margin-top:7px;letter-spacing:.06em;text-transform:uppercase}
.q.reliability{border-left-color:var(--deny)}
.q.behavioural{border-left-color:var(--dim)}

/* --- shared -------------------------------------------------------------- */
.empty{color:var(--faint);font-size:13px;padding:14px 0;font-style:italic}
.note{color:var(--dim);font-size:13px;border-left:2px solid var(--line);
  padding-left:13px;margin:16px 0}
details.raw{margin-top:18px;border-top:1px solid var(--line);padding-top:12px}
details.raw>summary{cursor:pointer;font-family:var(--mono);font-size:11px;
  letter-spacing:.1em;text-transform:uppercase;color:var(--faint)}
details.raw>summary:hover{color:var(--dim)}
details.raw .body{max-height:340px;padding:12px 0 0}
.tag{display:inline-block;font-family:var(--mono);font-size:10.5px;
  letter-spacing:.06em;padding:2px 8px;border:1px solid var(--line);
  border-radius:3px;color:var(--dim);margin-left:6px}
.tag.ok{color:var(--allow);border-color:var(--allow)}
.tag.no{color:var(--deny);border-color:var(--deny)}
footer{padding:14px 26px;border-top:1px solid var(--line);color:var(--faint);
  font-size:11.5px;font-family:var(--mono);line-height:1.7;flex:none;
  background:var(--panel)}
a{color:var(--dim)}
@media(prefers-reduced-motion:reduce){*{transition:none!important}}
"""


def _stat(value, label, tone: str = "", raw: bool = False) -> str:
    """A big number and its caption.

    `raw` exists for the two counters the engine writes into. Escaping is the
    default because every other value here comes from the log, and a page
    whose figures are merchant-authored text is a page with an injection
    surface; the exception is spelled out at each call site.
    """
    shown = value if raw else esc(value)
    return (f'<div class="stat {tone}"><div class="v">{shown}</div>'
            f'<div class="l">{esc(label)}</div></div>')


def _box(title, body, note="", body_id="", flush=False) -> str:
    ident = f' id="{body_id}"' if body_id else ""
    klass = "body flush" if flush else "body"
    return (f'<section class="box"><h2>{esc(title)}'
            f'<span>{esc(note)}</span></h2>'
            f'<div class="{klass}"{ident}>{body}</div></section>')


def _scene(key, title, sub, body, active=False) -> str:
    flag = " data-active" if active else ""
    return (f'<section class="scene" id="sc-{key}" role="tabpanel"{flag}>'
            f'<h1 class="headline">{title}</h1>'
            f'<p class="sub">{sub}</p>{body}</section>')


def _detail(e) -> str:
    p = e.payload
    if e.type == "POLICY_DECIDED":
        v = p.get("verdict", "")
        css = "deny" if v != "ALLOW" else "allow"
        return f'<span class="{css}">{esc(v)}</span> {esc(p.get("reason", ""))}'
    if e.type == "NEGOTIATION_ENDED":
        return ("agreed at " + esc(p.get("final_price"))
                if p.get("agreed") else esc(p.get("reason", "")))
    if e.type == "NEGOTIATION_ROUND":
        return f'{esc(p.get("price"))} — {esc(str(p.get("message", "")).strip())}'
    if e.type == "SETTLEMENT_INITIATED":
        return f'{rupees(p.get("amount"))} · {esc(p.get("razorpay_order_id"))}'
    if e.type == "SETTLEMENT_COMPLETED":
        return esc(p.get("razorpay_payment_id"))
    if e.type == "DRIFT_DETECTED":
        return (f'local {esc(p.get("local_status"))} · '
                f'remote {esc(p.get("remote_status"))}')
    if e.type in ("ACTOR_FROZEN",):
        return esc(p.get("reason"))
    if e.type == "POINTS_MINTED":
        return f'{esc(p.get("points"))} points'
    return ""


def _thread_table(thread) -> str:
    rows = "".join(
        f'<tr><td class="p">{e.seq}</td><td class="a">{esc(e.actor_id)}</td>'
        f'<td class="t">{esc(e.type)}</td><td>{_detail(e)}</td></tr>'
        for e in thread)
    return f"<table>{rows}</table>"


def _raw(summary_text, body) -> str:
    return (f'<details class="raw"><summary>{esc(summary_text)}</summary>'
            f'<div class="body">{body}</div></details>')


# --- the engine --------------------------------------------------------------
#
# One clock, six readouts. `step()` advances the tape by a single real event
# and hands it to whichever readouts care; scenes that are not on screen keep
# updating, so switching tabs mid-run shows you the market as it is now rather
# than as it was when you last looked.

ENGINE = r"""<script>
(function(){
var M=JSON.parse(document.getElementById('mkt').textContent);
var rows=M.rows,i=0,rate=4,timer=null,ga=0,gd=0;
var $=function(id){return document.getElementById(id)};
var reduce=matchMedia('(prefers-reduced-motion: reduce)').matches;
function esc(t){var d=document.createElement('div');
  d.textContent=t==null?'':t;return d.innerHTML}

/* --- scenes ---------------------------------------------------------- */
var tabs=[].slice.call(document.querySelectorAll('.tab'));
function show(key){
  tabs.forEach(function(t){
    var on=t.dataset.scene===key;
    t.setAttribute('aria-selected',on?'true':'false');
    t.tabIndex=on?0:-1;
    var p=$('sc-'+t.dataset.scene);
    if(!p)return;
    /* Reset the scroll on entry. Scenes keep their scrollTop while hidden,
       so arriving at one you had scrolled before dropped you into the
       middle of it with the headline off screen — which is the one thing
       a viewer needs to read first. */
    if(on){p.setAttribute('data-active','');p.scrollTop=0}
    else p.removeAttribute('data-active');
  });
}
tabs.forEach(function(t,n){
  t.addEventListener('click',function(){show(t.dataset.scene)});
  t.addEventListener('keydown',function(e){
    var d=e.key==='ArrowRight'?1:e.key==='ArrowLeft'?-1:0;
    if(!d)return; e.preventDefault();
    var nx=tabs[(n+d+tabs.length)%tabs.length];
    show(nx.dataset.scene); nx.focus();
  });
});
document.addEventListener('keydown',function(e){
  if(/^(INPUT|TEXTAREA)$/.test((e.target.tagName||'')))return;
  if(e.key>='1'&&e.key<='9'){var t=tabs[+e.key-1];if(t)show(t.dataset.scene)}
  if(e.key===' '){e.preventDefault();toggle()}
});

/* --- readouts -------------------------------------------------------- */
function merchant(id,cls){
  var el=document.querySelector('[data-m="'+CSS.escape(id)+'"]');
  if(!el)return;
  el.classList.remove('act','frz');
  if(cls)el.classList.add(cls);
  if(cls==='act')setTimeout(function(){el.classList.remove('act')},2600);
}
function gateRow(r){
  var g=$('gate'),e0=g.querySelector('.empty'); if(e0)e0.remove();
  var ok=/^ALLOW/.test(r.detail);
  ok?ga++:gd++;
  $('ga').textContent=ga; $('gd').textContent=gd;
  var d=document.createElement('div'); d.className='grow';
  d.innerHTML='<span class="verdict '+(ok?'allow':'deny')+'">'+
    (ok?'ALLOW':'DENY')+'</span><span class="msg">'+
    esc(r.detail.replace(/^(ALLOW|DENY)\s*—\s*/,''))+'</span>';
  g.insertBefore(d,g.firstChild);
  while(g.children.length>60)g.removeChild(g.lastChild);
}
function negoRow(r){
  var n=$('nego'),e0=n.querySelector('.empty'); if(e0)e0.remove();
  var parts=String(r.detail).split(' — ');
  var d=document.createElement('div'); d.className='nline';
  d.innerHTML='<span class="who">'+esc(r.actor)+'</span><span class="px">'+
    esc(parts[0])+'</span><span class="msg">'+
    esc(parts.slice(1).join(' — '))+'</span>';
  n.appendChild(d); n.scrollTop=n.scrollHeight;
  $('negostate').textContent='talking';
  while(n.children.length>16)n.removeChild(n.firstChild);
}
function bidRow(r,win){
  var b=$('bids'),e0=b.querySelector('.empty'); if(e0)e0.remove();
  var d=document.createElement('div'); d.className='grow';
  d.innerHTML='<span class="'+(win?'allow':'')+'">'+(win?'WON':'bid')+
    '</span><span class="msg">'+esc(r.actor)+' — '+esc(r.detail)+'</span>';
  b.appendChild(d);
}

function step(){
  if(i>=rows.length){stop();setLabel('replay',false);return}
  var r=rows[i++];
  var t=$('tape');
  var el=document.createElement('div');
  el.className='row '+(r.tone||'')+' new';
  el.innerHTML='<span class="s">'+r.seq+'</span><span class="w">'+
    esc(r.actor)+'</span><span class="x"><b>'+esc(r.says)+'</b> '+
    esc(r.detail)+'</span>';
  t.appendChild(el);
  setTimeout(function(){el.classList.remove('new')},420);
  while(t.children.length>140)t.removeChild(t.firstChild);
  t.scrollTop=t.scrollHeight;

  $('seq').textContent=i;
  $('bar').style.width=(100*i/rows.length).toFixed(2)+'%';

  if(r.type==='POLICY_DECIDED')gateRow(r);
  else if(r.type==='NEGOTIATION_ROUND')negoRow(r);
  else if(r.type==='NEGOTIATION_ENDED')
    $('negostate').textContent=/agreed/.test(r.detail)?'agreed':'no deal';
  else if(r.type==='ACTOR_FROZEN')merchant(r.actor,'frz');
  else if(r.type==='ACTOR_RESUMED')merchant(r.actor,'act');
  else if(r.type==='BID_PLACED')bidRow(r,false);
  else if(r.type==='AUCTION_CLEARED')bidRow(r,true);
  if(r.actor&&r.actor.indexOf('m_')===0)merchant(r.actor,'act');
}

/* --- transport ------------------------------------------------------- */
function setLabel(text,pressed){
  var pp=$('pp'); pp.textContent=text;
  pp.setAttribute('aria-pressed',pressed?'true':'false');
}
function play(){stop();timer=setInterval(step,Math.max(12,320/rate));
  setLabel('pause',true)}
function stop(){if(timer)clearInterval(timer);timer=null}
function toggle(){
  if(timer){stop();setLabel('play',false)}
  else if(i>=rows.length)restart();
  else play();
}
function restart(){
  stop();
  ['tape','gate','nego','bids'].forEach(function(id){$(id).innerHTML=''});
  ga=gd=0; $('ga').textContent=0; $('gd').textContent=0;
  $('negostate').textContent='waiting';
  i=0; $('seq').textContent=0; $('bar').style.width='0%';
  play();
}
$('pp').addEventListener('click',toggle);
$('rs').addEventListener('click',restart);
[].forEach.call(document.querySelectorAll('[data-rate]'),function(b){
  b.addEventListener('click',function(){
    rate=+b.dataset.rate;
    [].forEach.call(document.querySelectorAll('[data-rate]'),function(o){
      o.setAttribute('aria-pressed',o===b?'true':'false')});
    if(timer)play();
  });
});

/* --- the storefront ---------------------------------------------------
   Searches the REAL catalogue read out of the log, then shows the purchase
   a person actually made. It does not pretend to run an agent in the
   browser: the honest claim is that a person reaches the same order book,
   and the evidence is the recorded thread. */
var q=$('q'),hits=$('hits');
function search(){
  var terms=(q.value||q.placeholder).toLowerCase().split(/\s+/)
    .filter(function(w){return w.length>3});
  var found=M.cat.map(function(c){
    var t=c.title.toLowerCase(),n=0;
    terms.forEach(function(w){if(t.indexOf(w)>=0)n++});
    return[c,n]}).filter(function(p){return p[1]>0})
    .sort(function(a,b){return b[1]-a[1]}).slice(0,6);
  if(!found.length){
    hits.innerHTML='<div class="empty">Nothing on the book matches that. '+
      'The agents only stock what merchants actually listed.</div>';
    return;
  }
  hits.innerHTML=found.map(function(p){var c=p[0];
    return '<div class="hit"><span>'+esc(c.title)+'</span><span class="sl">'+
      esc(c.seller)+'</span><span class="pr">'+(c.price/100).toFixed(2)+
      '</span></div>'}).join('');
}
$('go').addEventListener('click',search);
q.addEventListener('keydown',function(e){if(e.key==='Enter')search()});

/* Reduced motion: fast-forward to the end rather than animate, so the page
   is complete and readable without any movement at all. */
if(reduce){while(i<rows.length)step();stop();setLabel('replay',false)}
else{play()}
})();
</script>"""


def build(db_path: str) -> str:
    summary, trades, events = load(db_path)
    settled = [t for t in trades if t.outcome == "settled"]
    trials = [t for t in trades if t.was_refused_then_allowed]
    failures = failure_threads(events)
    sale = auction(events)
    learned = lessons(events, limit=8)
    desk = board(events)
    merchants = sorted(state_actors(events))

    payload = json.dumps({
        "rows": tape(events),
        "cat": catalogue(events),
        "shop": storefront(events),
    }, separators=(",", ":"))

    # The trade to lead with: a real haggle the gate refused once and then
    # allowed smaller. One thread carrying discovery, reasoning, the cap
    # binding, and money moving.
    lead = next((t for t in trials
                 if len([e for e in t.events
                         if e.type == "NEGOTIATION_ROUND"]) >= 3), None)
    lead = lead or (trials[0] if trials else (settled[0] if settled else None))

    # --- 1. the floor -------------------------------------------------------
    mgrid = "".join(f'<div class="m" data-m="{esc(m)}"><i></i>'
                    f'{esc(m[2:])}</div>' for m in merchants)
    floor = (
        '<div class="stats">'
        + _stat(summary.merchants, "agents trading")
        + _stat(f"{summary.agreed} / {summary.walked}", "agreed / walked away")
        + _stat(summary.completed, "payments completed", "allow")
        + _stat(rupees(summary.value_paise), "moved through Razorpay", "money")
        + _stat(summary.events, "events recorded")
        + "</div>"
        '<div class="cols c-floor">'
        + _box("the tape", "", note="every event, in order",
               body_id="tape", flush=True).replace(
                   'class="body flush" id="tape"',
                   'class="body flush tape" id="tape"')
        + '<div class="stack">'
        + _box("who is acting", f'<div class="mgrid">{mgrid}</div>',
               note=f"{summary.merchants} agents")
        + _box("what they are saying",
               '<div class="empty">Waiting for the first offer.</div>',
               note="live", body_id="nego").replace(
                   '<span>live</span>', '<span id="negostate">waiting</span>')
        + "</div></div>")

    # --- 2. the gate --------------------------------------------------------
    trial_body = (
        _thread_table([e for e in lead.events
                       if e.type in ("ORDER_POSTED", "COUNTERPARTY_CHOSEN",
                                     "NEGOTIATION_ENDED", "POLICY_DECIDED",
                                     "SETTLEMENT_INITIATED",
                                     "SETTLEMENT_COMPLETED")])
        if lead else '<div class="empty">No completed trade.</div>')
    gate = (
        '<div class="stats">'
        + _stat('<span id="ga">0</span>', "allowed", "allow", raw=True)
        + _stat('<span id="gd">0</span>', "refused", "deny", raw=True)
        + _stat(len(trials), "refused, then retried smaller")
        + "</div>"
        '<div class="cols c-gate">'
        + _box("every ruling, as it happens",
               '<div class="empty">Waiting for the first money action.</div>',
               note="live", body_id="gate")
        + _box("one refusal, in full",
               trial_body,
               note=esc(lead.correlation_id[:38]) if lead else "")
        + "</div>")

    # --- 3. the break -------------------------------------------------------
    if failures:
        # PICK THE THREAD THAT TELLS THE WHOLE STORY, not the first one.
        # Most drifts are caught and repaired by an ordinary reconciliation
        # sweep with no freeze involved — which is correct behaviour and a
        # bad demonstration. Taking failures[0] put "not in this thread"
        # against the freeze and the resume, which is precisely the pair the
        # track asks to see.
        arc = ("SETTLEMENT_INITIATED", "DRIFT_DETECTED", "ACTOR_FROZEN",
               "SETTLEMENT_COMPLETED", "ACTOR_RESUMED")
        threads = {c: [e for e in events if e.correlation_id == c]
                   for c in failures}
        chosen = max(failures,
                     key=lambda c: len(arc_seen(threads[c], arc)))
        thread = threads[chosen]
        seen = {e.type for e in thread}
        first_seq = {t: next((e.seq for e in thread if e.type == t), None)
                     for t in arc}
        steps = [
            ("01", "SETTLEMENT_INITIATED", "money committed",
             "The agent asked Razorpay for a payment link and recorded the "
             "settlement as pending.", ""),
            ("02", "DRIFT_DETECTED", "the books disagree",
             "Razorpay says captured. Our books say pending. Nobody was "
             "watching — the accountant found it on a routine sweep.", "bad"),
            ("03", "ACTOR_FROZEN", "trading stopped",
             "That one merchant, not the market. A disagreement about one "
             "payment must not halt everybody else.", "bad"),
            ("04", "SETTLEMENT_COMPLETED", "repaired from the log",
             "The real payment id came back from Razorpay, not from us. A "
             "repair that invents an id asserts payments that never "
             "happened.", "good"),
            ("05", "ACTOR_RESUMED", "trading resumed",
             "A freeze that never lifts is a ban, not a hold.", "good"),
        ]
        stepper = "".join(
            f'<div class="step {tone if t in seen else ""}">'
            f'<div class="n">{n} · '
            f'{"event " + str(first_seq[t]) if t in seen else "not recorded"}'
            f'</div><div class="t">{esc(label)}</div>'
            f'<div class="d">{esc(why)}</div></div>'
            for n, t, label, why, tone in steps)
        break_body = (
            f'<div class="steps">{stepper}</div>'
            + _box("the whole failure on one correlation id",
                   _thread_table(thread), note=esc(chosen[:44]))
            + f'<p class="note">Not injected. A payment link paid after the '
              f'settlement returned PENDING produces exactly this, and that '
              f'is how it happened — {len(failures)} times in this log.</p>')
        break_sub = (f"{len(failures)} drifts caught and repaired without a "
                     f"human. Here is one, start to finish.")
    else:
        break_body = ('<div class="empty">No drift here yet: a settlement can '
                      'only drift once its payment link has been paid.</div>')
        break_sub = "No drift in this log."

    # --- 4. the desk --------------------------------------------------------
    desk_body = _desk_html(desk, sale, summary)

    # --- 5. memory ----------------------------------------------------------
    lesson_html = "".join(
        f'<div class="q {esc(e.payload.get("kind", ""))}">'
        f'{esc(str(e.payload.get("text", ""))[:230])}'
        f'<div class="by">{esc(e.actor_id)} on '
        f'{esc(e.payload.get("counterparty_id"))} · '
        f'{esc(e.payload.get("kind"))}</div></div>'
        for e in learned) or '<div class="empty">No lessons yet.</div>'
    memory = (
        '<div class="stats">'
        + _stat(summary.lessons, "lessons kept")
        + _stat(summary.points_minted, "points minted", "money")
        + "</div>" + lesson_html
        + '<p class="note">A lesson is typed by what it is allowed to move. '
          'A <b>reliability</b> lesson can change a counterparty&rsquo;s '
          'standing and therefore its spending cap. A <b>behavioural</b> one '
          'never can — otherwise an agent that haggles hard would be treated '
          'the same as one that did not deliver.</p>')

    # --- 6. the shop --------------------------------------------------------
    shop_rows = "".join(
        f'<tr><td class="p">{r["seq"]}</td><td class="a">{esc(r["actor"])}</td>'
        f'<td class="t">{esc(r["type"])}</td><td>{esc(r["says"])}</td></tr>'
        for r in storefront(events)["rows"])
    shop = (
        '<div class="ask">'
        '<input id="q" type="text" aria-label="what do you need"'
        ' placeholder="biodegradable mailers under 22 rupees a unit">'
        '<button id="go" class="tbtn">search</button></div>'
        + _box("what is actually on the book",
               '<div class="empty">Type a need and press search. This reads '
               'the real catalogue from the log — it will refuse if nothing '
               'matches.</div>', note="live", body_id="hits")
        + '<div style="height:16px"></div>'
        + _box("the purchase a person actually made",
               f"<table>{shop_rows}</table>" if shop_rows
               else '<div class="empty">No human purchase in this log.</div>',
               note="same events as an agent's trade"))

    scenes = "".join([
        _scene("floor",
               f"{summary.merchants} agents traded with each other.",
               f"{summary.walked} of {summary.negotiations} negotiations ended "
               f"without a deal. That is not a failure rate — agents decline, "
               f"and a market where every deal closes is not a market.",
               floor, active=True),
        _scene("gate", "Before any money moves, the gate rules.",
               "It records the ruling <em>even when the answer is yes</em>. "
               "The most common story in this log is a stranger refused at "
               "full size and allowed at a smaller one — the cap on unknown "
               "counterparties, visible inside a single trade.",
               gate),
        _scene("break", "Razorpay said paid. Our books said pending.",
               break_sub, break_body),
        _scene("desk", "Only the payment processor sees the whole book.",
               "A merchant knows its own sales. Razorpay knows which "
               "campaigns are climbing across every client — and can rank "
               "them before any one client could.", desk_body),
        _scene("memory", "Each agent keeps what it learned.",
               "A whole trade compressed into one durable sentence, recalled "
               "before the next deal with the same counterparty.", memory),
        _scene("shop", "Same machinery, with a person driving.",
               "One input box writing a descriptive bid to the same order "
               "book the agents use. The human approves; the gate still "
               "decides.", shop),
    ])

    tabs = "".join(
        f'<button class="tab" data-scene="{key}" role="tab" '
        f'aria-selected="{"true" if n == 0 else "false"}" '
        f'tabindex="{0 if n == 0 else -1}"><b>{n + 1}</b>{esc(label)}</button>'
        for n, (key, label) in enumerate([
            ("floor", "the floor"), ("gate", "the gate"),
            ("break", "the break"), ("desk", "the desk"),
            ("memory", "memory"), ("shop", "the shop"),
        ]))

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total = len(tape(events))

    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Agent Exchange — market replay</title>'
        f"<style>{CSS}</style></head><body>"

        '<div class="bar"><span class="mark">AGENT EXCHANGE</span>'
        f'<span class="sep">/</span><span>{esc(db_path)}</span>'
        f'<span class="sep">/</span><span>{summary.events} events</span>'
        f'<span class="sep">/</span><span>{summary.merchants} merchants</span>'
        '<span class="sealed"><i></i>log sealed &middot; read only</span></div>'

        f'<div class="scenes" role="tablist">{tabs}</div>'

        '<div class="transport">'
        '<button class="tbtn" id="pp" aria-pressed="true">pause</button>'
        '<button class="tbtn" id="rs">restart</button>'
        '<button class="tbtn" data-rate="1" aria-pressed="false">1&times;</button>'
        '<button class="tbtn" data-rate="4" aria-pressed="true">4&times;</button>'
        '<button class="tbtn" data-rate="20" aria-pressed="false">20&times;</button>'
        f'<span class="clock">event <b id="seq">0</b> of {total}</span></div>'
        '<div class="progress"><i id="bar"></i></div>'

        f"<main>{scenes}</main>"

        f'<footer>Generated {esc(generated)} from {esc(summary.events)} events '
        f'in {esc(db_path)} &middot; gate {summary.gate_allow} allowed / '
        f'{summary.gate_deny} refused &middot; {summary.completed} payments '
        f'completed against real Razorpay test-mode orders.<br>'
        f'Nothing on this page was computed by the page. Every figure is read '
        f'from the log, or from the same projection the exchange itself runs '
        f'on. Press 1&ndash;6 to switch, space to pause.</footer>'

        f'<script type="application/json" id="mkt">{payload}</script>'
        f"{ENGINE}</body></html>"
    )


def _desk_html(desk, sale, summary) -> str:
    """Razorpay's internal board, then the auction that sells a piece of it.

    Walled off on purpose. Violet appears nowhere else on the page, and the
    header says who may read this, because the separation between what the
    house sees and what a merchant may buy is the product — not a detail of
    the presentation.
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
            f'<div class="refused">{len(refused)} campaigns refused a place '
            f'on this board — fewer distinct merchants than the floor of '
            f'{refused[0].get("floor")} allows. A floor nobody can see is '
            f'indistinguishable from no floor.</div>') if refused else ""
        board_html = (
            '<div class="internal"><div class="hdr">'
            '<b>Razorpay internal &middot; not visible to merchants</b>'
            '<span>ranking computed from the log &middot; '
            'explanations sourced from the public press</span></div>'
            f'{rows}{refusal}</div>')
    else:
        board_html = ('<div class="empty">No campaign board published. Run '
                      '<code>scripts.market.research</code> over this '
                      'log.</div>')

    if sale:
        bids = "".join(
            f'<tr><td class="a">{esc(e.actor_id)}</td>'
            f'<td class="mono">{esc(e.payload.get("amount"))}</td>'
            f'<td>{esc(str(e.payload.get("reason", ""))[:120])}</td></tr>'
            for e in sale["bids"])
        paid = (sale["royalties"][0].payload.get("amount")
                if sale["royalties"] else 0)
        auction_html = (
            _box("the lot that went to auction",
                 f'<p style="margin:0 0 12px;font-size:16px">'
                 f'&ldquo;{esc(sale["headline"])}&rdquo;</p>'
                 f'<table><tr><th>bidder</th><th>points</th>'
                 f'<th>why they valued it there</th></tr>{bids}</table>'
                 f'<p class="note" style="margin-bottom:0">'
                 f'<b>{esc(sale["winner"])}</b> won and paid '
                 f'<b>{esc(sale["price"])}</b> points &mdash; the '
                 f'runner-up&rsquo;s bid, not its own. '
                 f'{len(sale["royalties"])} contributing merchants each '
                 f'earned <b>{esc(paid)}</b> points from a win they did not '
                 f'know was being sold.</p>',
                 note="sealed bids, second price",
                 body_id="bidsbox").replace(
                     'id="bidsbox"', 'id="bidsbox"') +
            '<div id="bids" hidden></div>')
    else:
        auction_html = (
            f'<div class="note">The privacy floor refused: only '
            f'{summary.distinct_traders} merchants contributed. A floor that '
            f'refuses is the control working.</div><div id="bids" hidden></div>')

    return board_html + auction_html


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
