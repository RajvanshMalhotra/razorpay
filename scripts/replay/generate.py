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

from scripts.replay.read import auction, failure_threads, lessons, load, tape


def state_actors(events):
    return {e.actor_id for e in events if e.actor_id.startswith('m_')}

CSS = """
/* A TERMINAL, not a document. The content is a market log: thousands of
   figures, timestamps, verdicts and counterparties. Instrumentation is the
   honest register for that — a terminal reads as a readout of something that
   happened, where a marketing page reads as a claim about it. That serves the
   credibility constraint better than the document draft did.

   Bloomberg's actual lesson is not "amber on black". It is that DENSITY IS A
   COURTESY when every row is something the reader came for: no scroll spent
   on decoration, labels always adjacent to their number, and one glance
   telling you the state of the system. Legibility outranks atmosphere
   everywhere the two disagree. */
:root{
  --bg:#07090c; --panel:#0c1014; --raised:#11161c; --line:#1c242e;
  --ink:#dde5ee; --dim:#7d8b9c; --faint:#4a5666;
  --amber:#f0a828; --green:#3fd07f; --red:#f4685a; --blue:#5aa9f0;
  --grid:rgba(255,255,255,.028);
}
*{box-sizing:border-box}
html{color-scheme:dark}
body{margin:0;background:var(--bg);color:var(--ink);
  font:13px/1.5 ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
  font-variant-numeric:tabular-nums lining-nums;
  -webkit-font-smoothing:antialiased;
  background-image:linear-gradient(var(--grid) 1px,transparent 1px),
                   linear-gradient(90deg,var(--grid) 1px,transparent 1px);
  background-size:34px 34px}
::selection{background:rgba(240,168,40,.26);color:#fff}
:root{caret-color:var(--amber);accent-color:var(--amber)}

/* --- the chrome ------------------------------------------------------- */
.bar{position:sticky;top:0;z-index:9;display:flex;gap:18px;align-items:center;
  padding:9px 16px;background:rgba(7,9,12,.94);backdrop-filter:blur(8px);
  border-bottom:1px solid var(--line);font-size:11.5px;letter-spacing:.06em;
  text-transform:uppercase;color:var(--dim);flex-wrap:wrap}
.bar .mark{color:var(--amber);font-weight:600;letter-spacing:.14em}
.bar .sep{color:var(--faint)}
.bar .live{display:inline-flex;gap:6px;align-items:center;color:var(--green)}
.dot{width:6px;height:6px;border-radius:50%;background:currentColor}
.wrap{max-width:1180px;margin:0 auto;padding:20px 16px 70px}

/* --- panels ----------------------------------------------------------- */
.panel{border:1px solid var(--line);background:var(--panel);margin-bottom:14px;
  position:relative}
/* Corner ticks: the cheapest way to say "instrument" without decoration. */
.panel::before,.panel::after{content:"";position:absolute;width:7px;height:7px;
  border-color:var(--faint);border-style:solid;pointer-events:none}
.panel::before{top:-1px;left:-1px;border-width:1px 0 0 1px}
.panel::after{bottom:-1px;right:-1px;border-width:0 1px 1px 0}
.phead{display:flex;gap:12px;align-items:baseline;padding:9px 13px;
  border-bottom:1px solid var(--line);background:var(--raised)}
.phead .idx{color:var(--amber);font-size:11px;letter-spacing:.12em}
.phead h2{margin:0;font-size:12.5px;font-weight:600;letter-spacing:.055em;
  text-transform:uppercase;color:var(--ink)}
.phead .note{margin-left:auto;color:var(--faint);font-size:11px;
  letter-spacing:.05em;text-transform:uppercase}
.pbody{padding:13px}
/* Prose gets a humanist sans; every FIGURE stays mono. Monospace
   paragraphs are punishing to read, and the brief asked for user-friendly —
   the terminal register lives in the data, not in the sentences. */
.lede{color:var(--dim);margin:0 0 12px;max-width:68ch;line-height:1.68;
  font-family:ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif;
  font-size:13.5px}
.q{font-family:ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif;
  font-size:13.5px}
.big{font-family:ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif}

/* --- readouts --------------------------------------------------------- */
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));
  gap:1px;background:var(--line);border:1px solid var(--line)}
.stat{background:var(--panel);padding:11px 12px}
.stat b{display:block;font-size:20px;font-weight:600;letter-spacing:-.01em;
  color:var(--ink);line-height:1.2}
.stat span{display:block;margin-top:4px;font-size:10.5px;letter-spacing:.07em;
  text-transform:uppercase;color:var(--faint)}
.stat.up b{color:var(--green)} .stat.warn b{color:var(--amber)}

table{width:100%;border-collapse:collapse;font-size:12.5px}
thead th{text-align:left;padding:6px 10px;font-size:10.5px;font-weight:600;
  letter-spacing:.08em;text-transform:uppercase;color:var(--faint);
  border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:6px 10px;border-bottom:1px solid rgba(28,36,46,.55);
  vertical-align:top}
tbody tr:hover{background:rgba(255,255,255,.022)}
tr:last-child td{border-bottom:0}
td.a{white-space:nowrap;color:var(--blue);width:1%}
td.p{white-space:nowrap;text-align:right;width:1%;color:var(--ink)}
td.t{white-space:nowrap;color:var(--dim);width:1%}
td.seq{color:var(--faint);text-align:right;width:1%}
.deny{color:var(--red);font-weight:600}
.allow{color:var(--green);font-weight:600}
.ok{color:var(--green)} .no{color:var(--red)}

.tag{display:inline-block;font-size:10px;letter-spacing:.07em;
  text-transform:uppercase;padding:2px 7px;border:1px solid var(--line);
  color:var(--dim);white-space:nowrap}
.tag.ok{color:var(--green);border-color:rgba(63,208,127,.34)}
.tag.no{color:var(--red);border-color:rgba(244,104,90,.34)}
.tag.amber{color:var(--amber);border-color:rgba(240,168,40,.34)}

.head{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;
  align-items:baseline;margin-bottom:9px;padding-bottom:9px;
  border-bottom:1px solid var(--line)}
.who{color:var(--blue)}
.scroll{overflow-x:auto}
.q{border-left:2px solid var(--line);padding:3px 0 3px 12px;margin:9px 0;
  color:var(--ink);line-height:1.6}
.q .by{color:var(--faint);font-size:11px;letter-spacing:.05em;margin-top:3px}
.big{font-size:17px;line-height:1.45;color:var(--ink);margin:0 0 10px;
  max-width:70ch}
.rule{border:0;border-top:1px solid var(--line);margin:12px 0}
.empty{color:var(--faint);padding:8px 0}
summary{cursor:pointer;color:var(--dim);font-size:11px;letter-spacing:.07em;
  text-transform:uppercase;padding:3px 5px;margin:6px -5px 0;border-radius:2px}
summary:hover{color:var(--amber)}
summary:focus-visible{outline:1px solid var(--amber);outline-offset:2px}
footer{border-top:1px solid var(--line);margin-top:18px;padding:12px 2px;
  color:var(--faint);font-size:11px;letter-spacing:.04em;line-height:1.7}
@media(prefers-reduced-motion:reduce){*{transition-duration:.01ms!important}}

/* --- THE WORKSPACE ---------------------------------------------------- */
/* Not a page you scroll: a desk you sit at. Every pane is a standing
   instrument, and the tape at the centre drives all of them — as events
   stream, the leaderboard climbs, the gate log fills, the negotiation
   updates. One clock, many readouts, which is what makes it a terminal
   rather than a set of charts that happen to share a background. */
.desk{display:grid;gap:10px;grid-template-columns:210px minmax(0,1fr) 290px;
  grid-template-areas:
    "roster floor  board"
    "roster nego   gate"
    "shop   shop   intel";
  align-items:start}
@media(max-width:1180px){
  .desk{grid-template-columns:minmax(0,1fr) 300px;
    grid-template-areas:"floor board" "nego gate" "shop intel" "roster roster"}}
@media(max-width:820px){
  .desk{grid-template-columns:minmax(0,1fr);
    grid-template-areas:"floor" "nego" "board" "gate" "shop" "intel" "roster"}}
.a-roster{grid-area:roster} .a-floor{grid-area:floor} .a-board{grid-area:board}
.a-nego{grid-area:nego} .a-gate{grid-area:gate} .a-shop{grid-area:shop}
.a-intel{grid-area:intel}
.pane{border:1px solid var(--line);background:var(--panel);position:relative;
  display:flex;flex-direction:column;min-width:0}
.pane::before,.pane::after{content:"";position:absolute;width:6px;height:6px;
  border-color:var(--faint);border-style:solid;pointer-events:none}
.pane::before{top:-1px;left:-1px;border-width:1px 0 0 1px}
.pane::after{bottom:-1px;right:-1px;border-width:0 1px 1px 0}
.ph{display:flex;gap:9px;align-items:baseline;padding:7px 11px;
  border-bottom:1px solid var(--line);background:var(--raised);
  font-size:10.5px;letter-spacing:.1em;text-transform:uppercase}
.ph b{color:var(--amber);font-weight:600}
.ph span{color:var(--dim);font-weight:600}
.ph em{margin-left:auto;font-style:normal;color:var(--faint);font-size:10px}
.pc{padding:9px 11px;overflow-y:auto;min-height:0}
.h150{max-height:150px} .h210{max-height:210px} .h250{max-height:250px}
.h300{max-height:300px}

/* readouts inside panes */
.lb{display:grid;grid-template-columns:18px 1fr auto;gap:8px;
  padding:3px 0;font-size:12px;align-items:baseline}
.lb .r{color:var(--faint);text-align:right}
.lb .n{color:var(--blue);overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap}
.lb .v{color:var(--amber);font-weight:600}
.lb.up .v{color:var(--green)}
.mrow{display:flex;gap:7px;align-items:center;padding:2.5px 0;font-size:11.5px}
.mrow .st{width:6px;height:6px;border-radius:50%;background:var(--faint);
  flex:none}
.mrow.act .st{background:var(--green)} .mrow.frz .st{background:var(--red)}
.mrow .nm{color:var(--dim);overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap}
.mrow.act .nm{color:var(--ink)}
.nline{display:grid;grid-template-columns:auto auto 1fr;gap:9px;padding:3px 0;
  font-size:12px;align-items:baseline}
.nline .who{color:var(--blue);white-space:nowrap}
.nline .px{color:var(--ink);font-weight:600;white-space:nowrap}
.nline .msg{color:var(--dim);overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap}
.grow{display:grid;grid-template-columns:auto 1fr;gap:9px;padding:3px 0;
  font-size:11.5px;align-items:baseline}
.empty{color:var(--faint);font-size:11.5px;padding:6px 0}

/* the storefront box */
.shopbar{display:flex;gap:8px;padding:9px 11px;border-bottom:1px solid var(--line)}
.shopbar input{flex:1;min-width:0;font:inherit;font-size:13px;
  background:var(--bg);border:1px solid var(--line);color:var(--ink);
  padding:7px 10px}
.shopbar input:focus{outline:none;border-color:var(--amber)}
.shopbar input::placeholder{color:var(--faint)}
.shopbar button{font:inherit;font-size:11px;letter-spacing:.09em;
  text-transform:uppercase;background:transparent;border:1px solid var(--line);
  color:var(--ink);padding:0 14px;cursor:pointer}
.shopbar button:hover{border-color:var(--amber);color:var(--amber)}
.shopbar button:focus-visible{outline:1px solid var(--amber);outline-offset:2px}
.hit{display:grid;grid-template-columns:1fr auto auto;gap:10px;padding:4px 0;
  font-size:12px;align-items:baseline;border-bottom:1px solid rgba(28,36,46,.5)}
.hit .t{color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hit .s{color:var(--blue);white-space:nowrap}
.hit .p{color:var(--amber);white-space:nowrap}

/* --- THE TAPE: the page's one signature ------------------------------- */
/* A terminal's identity is the stream, and "watch the agents run" IS the
   stream. It plays real recorded events in real order; it does not run a
   market. That distinction is the whole reason it can be trusted: a replay
   cannot fail on camera, and every row is checkable against the log. */
.tape{border:1px solid var(--line);background:var(--panel);margin-bottom:14px}
.tape .ctl{display:flex;gap:10px;align-items:center;padding:9px 13px;
  border-bottom:1px solid var(--line);background:var(--raised);flex-wrap:wrap}
.tape button{font:inherit;font-size:11px;letter-spacing:.09em;
  text-transform:uppercase;color:var(--ink);background:transparent;
  border:1px solid var(--line);padding:5px 11px;cursor:pointer;
  transition:border-color 120ms ease,color 120ms ease}
.tape button:hover{border-color:var(--amber);color:var(--amber)}
.tape button:focus-visible{outline:1px solid var(--amber);outline-offset:2px}
.tape button[aria-pressed="true"]{border-color:var(--amber);color:var(--amber)}
.tape .count{margin-left:auto;color:var(--faint);font-size:11px;
  letter-spacing:.06em}
.tape .stream{height:290px;overflow-y:auto;padding:4px 0;scroll-behavior:smooth}
@media(prefers-reduced-motion:reduce){.tape .stream{scroll-behavior:auto}}
.row{display:grid;grid-template-columns:52px 150px 165px 1fr;gap:12px;
  padding:4px 13px;font-size:12px;line-height:1.5;border-left:2px solid transparent}
.row .s{color:var(--faint);text-align:right}
.row .w{color:var(--blue);white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.row .d{color:var(--dim)}
.row .x{color:var(--dim);white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.row.allow{border-left-color:var(--green)} .row.allow .d{color:var(--green)}
.row.deny{border-left-color:var(--red)} .row.deny .d{color:var(--red)}
.row.amber{border-left-color:var(--amber)} .row.amber .d{color:var(--amber)}
.row.new{background:rgba(240,168,40,.07)}
@media(max-width:760px){.row{grid-template-columns:40px 1fr;gap:6px}
  .row .x,.row .d{grid-column:2}}
.legend{display:flex;gap:16px;flex-wrap:wrap;padding:8px 13px;
  border-top:1px solid var(--line);font-size:10.5px;letter-spacing:.06em;
  text-transform:uppercase;color:var(--faint)}
.legend i{font-style:normal;display:inline-flex;gap:6px;align-items:center}
.legend b{width:8px;height:8px;display:inline-block}
@media(max-width:640px){.bar{font-size:10.5px;gap:10px}.wrap{padding:14px 10px 50px}}
"""


def esc(text) -> str:
    return html.escape(str(text if text is not None else ""))


def rupees(paise) -> str:
    return f"₹{(paise or 0) / 100:,.2f}"


def _stat(value, label, tone: str = "") -> str:
    cls = f"stat {tone}".strip()
    return (f'<div class="{cls}"><b>{esc(value)}</b>'
            f'<span>{esc(label)}</span></div>')


def _tape(rows) -> str:
    """The stream, plus the controls a viewer actually needs.

    Play/pause and speed, not a scrubber: the point is to watch it run, and a
    scrubber invites fiddling during a take. It autoplays because a page that
    waits to be told to start looks broken on a video.
    """
    import json

    data = json.dumps(rows, separators=(",", ":"))
    return f"""<section class="tape">
  <div class="ctl">
    <button id="pp" aria-pressed="true">pause</button>
    <button data-rate="1">1&times;</button>
    <button data-rate="4" aria-pressed="true">4&times;</button>
    <button data-rate="20">20&times;</button>
    <button id="rs">restart</button>
    <span class="count"><span id="ct">0</span> / {len(rows)} events</span>
  </div>
  <div class="stream" id="st" role="log" aria-live="polite"
       aria-label="Market events, playing in the order they happened"></div>
  <div class="legend">
    <i><b style="background:var(--green)"></b>allowed / agreed</i>
    <i><b style="background:var(--red)"></b>refused / disagreed</i>
    <i><b style="background:var(--amber)"></b>money or intelligence moved</i>
    <i><b style="background:var(--blue)"></b>the merchant acting</i>
  </div>
</section>
<script>
(function(){{
  var rows={data},i=0,rate=4,timer=null,
      st=document.getElementById('st'),ct=document.getElementById('ct'),
      pp=document.getElementById('pp'),rs=document.getElementById('rs');
  var reduce=matchMedia('(prefers-reduced-motion: reduce)').matches;
  function esc(t){{var d=document.createElement('div');d.textContent=t;return d.innerHTML}}
  function step(){{
    if(i>=rows.length){{stop();pp.textContent='replay';pp.setAttribute('aria-pressed','false');return}}
    var r=rows[i++],el=document.createElement('div');
    el.className='row '+(r.tone||'')+' new';
    el.innerHTML='<span class="s">'+r.seq+'</span>'+
      '<span class="w">'+esc(r.actor)+'</span>'+
      '<span class="x">'+esc(r.says)+'</span>'+
      '<span class="d">'+esc(r.detail)+'</span>';
    st.appendChild(el);
    setTimeout(function(){{el.classList.remove('new')}},420);
    while(st.children.length>160)st.removeChild(st.firstChild);
    st.scrollTop=st.scrollHeight;
    ct.textContent=i;
  }}
  function play(){{stop();timer=setInterval(step,Math.max(16,320/rate))}}
  function stop(){{if(timer)clearInterval(timer);timer=null}}
  pp.addEventListener('click',function(){{
    if(timer){{stop();pp.textContent='play';pp.setAttribute('aria-pressed','false')}}
    else{{if(i>=rows.length)restart();else play();
      pp.textContent='pause';pp.setAttribute('aria-pressed','true')}}
  }});
  function restart(){{stop();st.innerHTML='';i=0;ct.textContent=0;play();
    pp.textContent='pause';pp.setAttribute('aria-pressed','true')}}
  rs.addEventListener('click',restart);
  Array.prototype.forEach.call(document.querySelectorAll('[data-rate]'),function(b){{
    b.addEventListener('click',function(){{
      rate=+b.dataset.rate;
      Array.prototype.forEach.call(document.querySelectorAll('[data-rate]'),
        function(o){{o.setAttribute('aria-pressed',o===b?'true':'false')}});
      if(timer)play();
    }});
  }});
  if(reduce){{
    // Someone who has asked for less motion still gets the whole log,
    // all at once, rather than a stream they cannot follow.
    while(i<rows.length)step();
    stop();pp.textContent='replay';pp.setAttribute('aria-pressed','false');
  }} else {{play()}}
}})();
</script>"""


ENGINE = r'''<script>
(function(){
var M=JSON.parse(document.getElementById('mkt').textContent),
    rows=M.rows,i=0,rate=4,timer=null,pts={},ga=0,gd=0,negoKey=null;
var $=function(id){return document.getElementById(id)};
var st=$('st'),ct=$('ct'),pp=$('pp'),rs=$('rs'),board=$('board'),
    nego=$('nego'),negowho=$('negowho'),gate=$('gate');
var reduce=matchMedia('(prefers-reduced-motion: reduce)').matches;
function esc(t){var d=document.createElement('div');d.textContent=t==null?'':t;
  return d.innerHTML}

function merchant(id,cls){
  var el=document.querySelector('[data-m="'+CSS.escape(id)+'"]');
  if(!el)return; el.classList.remove('act','frz');
  if(cls)el.classList.add(cls);
  if(cls==='act')setTimeout(function(){el.classList.remove('act')},2600);
}
function leader(){
  var top=Object.keys(pts).map(function(k){return[k,pts[k]]})
    .sort(function(a,b){return b[1]-a[1]}).slice(0,9);
  if(!top.length)return;
  board.innerHTML=top.map(function(p,n){
    return '<div class="lb'+(n===0?' up':'')+'"><span class="r">'+(n+1)+
      '</span><span class="n">'+esc(p[0])+'</span><span class="v">'+
      p[1].toLocaleString()+'</span></div>'}).join('');
}
function gateRow(r){
  if(gate.querySelector('.empty'))gate.innerHTML='';
  var ok=/ALLOW/.test(r.detail);
  ok?ga++:gd++; $('ga').textContent=ga; $('gd').textContent=gd;
  var d=document.createElement('div');
  d.className='grow';
  d.innerHTML='<span class="'+(ok?'allow':'deny')+'">'+(ok?'ALLOW':'DENY')+
    '</span><span class="msg" style="color:var(--dim);overflow:hidden;'+
    'text-overflow:ellipsis;white-space:nowrap">'+
    esc(r.detail.replace(/^(ALLOW|DENY)\s*—\s*/,''))+'</span>';
  gate.insertBefore(d,gate.firstChild);
  while(gate.children.length>40)gate.removeChild(gate.lastChild);
}
function negoRow(r){
  var key=r.actor;
  if(nego.querySelector('.empty'))nego.innerHTML='';
  var d=document.createElement('div');d.className='nline';
  var parts=String(r.detail).split(' — ');
  d.innerHTML='<span class="who">'+esc(r.actor)+'</span>'+
    '<span class="px">'+esc(parts[0])+'</span>'+
    '<span class="msg">'+esc(parts.slice(1).join(' — '))+'</span>';
  nego.appendChild(d); nego.scrollTop=nego.scrollHeight;
  negowho.textContent='in progress';
  while(nego.children.length>14)nego.removeChild(nego.firstChild);
}
function step(){
  if(i>=rows.length){stop();pp.textContent='replay';
    pp.setAttribute('aria-pressed','false');return}
  var r=rows[i++];
  var el=document.createElement('div');
  el.className='row '+(r.tone||'')+' new';
  el.innerHTML='<span class="s">'+r.seq+'</span><span class="w">'+
    esc(r.actor)+'</span><span class="x">'+esc(r.says)+'</span>'+
    '<span class="d">'+esc(r.detail)+'</span>';
  st.appendChild(el);
  setTimeout(function(){el.classList.remove('new')},400);
  while(st.children.length>120)st.removeChild(st.firstChild);
  st.scrollTop=st.scrollHeight; ct.textContent=i;

  if(r.type==='POLICY_DECIDED')gateRow(r);
  else if(r.type==='NEGOTIATION_ROUND')negoRow(r);
  else if(r.type==='NEGOTIATION_ENDED'){
    negowho.textContent=/agreed/.test(r.detail)?'agreed':'no deal';}
  else if(r.type==='POINTS_MINTED'){
    var n=parseInt(r.detail,10)||0; pts[r.actor]=(pts[r.actor]||0)+n; leader();}
  else if(r.type==='ACTOR_FROZEN'){merchant(r.actor,'frz');}
  else if(r.type==='ACTOR_RESUMED'){merchant(r.actor,'act');}
  else if(r.type==='INSIGHT_MINTED'||r.type==='AUCTION_CLEARED'||
          r.type==='BID_PLACED'){
    var box=$('intel'),body=$('intelbody');
    var e0=box.querySelector('.empty'); if(e0)e0.remove();
    body.hidden=false;
    if(r.type==='BID_PLACED'){
      var b=document.createElement('div');b.className='lb';
      b.innerHTML='<span class="r"></span><span class="n">'+esc(r.actor)+
        '</span><span class="v">'+esc(r.detail)+'</span>';
      $('bids').appendChild(b);}
    if(r.type==='AUCTION_CLEARED'){
      var w=document.createElement('div');w.className='lb up';
      w.innerHTML='<span class="r">&#10003;</span><span class="n">'+
        esc(r.detail)+'</span><span class="v"></span>';
      $('bids').appendChild(w);}
  }
  if(r.actor&&r.actor.indexOf('m_')===0)merchant(r.actor,'act');
}
function play(){stop();timer=setInterval(step,Math.max(14,300/rate))}
function stop(){if(timer)clearInterval(timer);timer=null}
function restart(){stop();st.innerHTML='';nego.innerHTML='';gate.innerHTML='';
  board.innerHTML='';pts={};ga=gd=0;$('ga').textContent=0;$('gd').textContent=0;
  i=0;ct.textContent=0;play();pp.textContent='pause';
  pp.setAttribute('aria-pressed','true')}
pp.addEventListener('click',function(){
  if(timer){stop();pp.textContent='play';pp.setAttribute('aria-pressed','false')}
  else if(i>=rows.length)restart();
  else{play();pp.textContent='pause';pp.setAttribute('aria-pressed','true')}});
rs.addEventListener('click',restart);
[].forEach.call(document.querySelectorAll('[data-rate]'),function(b){
  b.addEventListener('click',function(){rate=+b.dataset.rate;
    [].forEach.call(document.querySelectorAll('[data-rate]'),function(o){
      o.setAttribute('aria-pressed',o===b?'true':'false')});
    if(timer)play();})});

/* --- the storefront ---------------------------------------------------
   This searches the REAL catalogue read out of the log, then shows the
   purchase a person actually made. It does not pretend to run an agent in
   the browser: the honest claim is that a person reaches the same order
   book, and the evidence is the recorded thread. */
var q=$('q'),hits=$('hits');
function search(){
  var terms=(q.value||q.placeholder).toLowerCase().split(/\s+/)
    .filter(function(w){return w.length>3});
  var found=M.cat.map(function(c){
    var t=c.title.toLowerCase(),n=0;
    terms.forEach(function(w){if(t.indexOf(w)>=0)n++});
    return[c,n]}).filter(function(p){return p[1]>0})
    .sort(function(a,b){return b[1]-a[1]}).slice(0,6);
  if(!found.length){hits.innerHTML='<div class="empty">Nothing on the book '+
    'matches that. The agents only stock what merchants actually listed.</div>';
    return}
  hits.innerHTML=found.map(function(p){var c=p[0];
    return '<div class="hit"><span class="t">'+esc(c.title)+
      '</span><span class="s">'+esc(c.seller)+'</span><span class="p">'+
      (c.price/100).toFixed(2)+'</span></div>'}).join('')+
    '<div style="margin-top:9px;font-size:11px;letter-spacing:.06em;'+
    'text-transform:uppercase;color:var(--faint)">the purchase a person '+
    'actually made</div>'+
    M.shop.rows.map(function(r){
      return '<div class="grow"><span style="color:var(--faint)">'+r.seq+
        '</span><span style="color:var(--dim)">'+esc(r.says)+'</span></div>'
    }).join('');
}
$('go').addEventListener('click',search);
q.addEventListener('keydown',function(e){if(e.key==='Enter')search()});

if(reduce){while(i<rows.length)step();stop();pp.textContent='replay';
  pp.setAttribute('aria-pressed','false')}else{play()}
})();
</script>
'''


def _desk(rows, merchants, cat, sf, sale) -> str:
    """The workspace. One clock, many readouts.

    The tape drives every pane: as events stream, the leaderboard climbs, the
    gate log fills, the negotiation updates, merchants light up and freeze.
    That shared clock is what makes this a terminal rather than a set of
    charts that happen to share a background.
    """
    import json

    data = json.dumps({"rows": rows, "merchants": merchants,
                       "cat": cat, "shop": sf}, separators=(",", ":"))
    mrows = "".join(
        f'<div class="mrow" data-m="{esc(m)}"><span class="st"></span>'
        f'<span class="nm">{esc(m)}</span></div>' for m in merchants)
    hl = esc(sale["headline"]) if sale else "nothing minted yet"
    return f"""<div class="desk">

<section class="pane a-roster"><div class="ph"><b>A</b><span>merchants</span>
  <em id="mcount">{len(merchants)}</em></div>
  <div class="pc h300" id="mlist">{mrows}</div></section>

<section class="pane a-floor"><div class="ph"><b>B</b><span>trading floor</span>
  <em><span id="ct">0</span> / {len(rows)} events</em></div>
  <div class="ctl">
    <button id="pp" aria-pressed="true">pause</button>
    <button data-rate="1">1&times;</button>
    <button data-rate="4" aria-pressed="true">4&times;</button>
    <button data-rate="20">20&times;</button>
    <button id="rs">restart</button>
  </div>
  <div class="pc stream h250" id="st" role="log" aria-live="polite"
       aria-label="Market events in the order they happened"></div>
  <div class="legend">
    <i><b style="background:var(--green)"></b>allowed</i>
    <i><b style="background:var(--red)"></b>refused</i>
    <i><b style="background:var(--amber)"></b>money moved</i>
  </div></section>

<section class="pane a-board"><div class="ph"><b>C</b><span>leaderboard</span>
  <em>points earned</em></div>
  <div class="pc h250" id="board">
    <div class="empty">Points are minted when a trade settles. Watch them
      arrive.</div></div></section>

<section class="pane a-nego"><div class="ph"><b>D</b><span>live negotiation</span>
  <em id="negowho">waiting</em></div>
  <div class="pc h210" id="nego">
    <div class="empty">The current haggle appears here as it happens.</div>
  </div></section>

<section class="pane a-gate"><div class="ph"><b>E</b><span>the gate</span>
  <em><span id="ga">0</span> allowed / <span id="gd">0</span> refused</em></div>
  <div class="pc h210" id="gate">
    <div class="empty">Every money action is ruled on before it happens.</div>
  </div></section>

<section class="pane a-shop"><div class="ph"><b>F</b><span>storefront</span>
  <em>a person buys, in plain language</em></div>
  <div class="shopbar">
    <input id="q" placeholder="{esc(sf['query'] or 'what do you need?')}"
      aria-label="Describe what you need">
    <button id="go">search</button>
  </div>
  <div class="pc h210" id="hits">
    <div class="empty">Type what you need. This searches the same order book
      the agents trade on &mdash; then shows the purchase a person actually
      made through it.</div></div></section>

<section class="pane a-intel"><div class="ph"><b>G</b><span>intelligence</span>
  <em>second price, sealed bids</em></div>
  <div class="pc h210" id="intel">
    <div class="empty">Mined from settled trades once the privacy floor is
      met.</div>
    <div id="intelbody" hidden><p class="lede"
      style="font-size:12.5px;margin:0 0 8px">&ldquo;{hl}&rdquo;</p>
      <div id="bids"></div></div>
  </div></section>

</div>
<script id="mkt" type="application/json">{data}</script>""" + ENGINE


def _panel(idx: str, title: str, body: str, note: str = "") -> str:
    """Every readout sits in a labelled frame. A terminal's discipline is that
    nothing floats: if it is on screen it belongs to a named panel, and the
    reader always knows which instrument they are looking at."""
    tail = f'<span class="note">{esc(note)}</span>' if note else ""
    return (f'<section class="panel">'
            f'<div class="phead"><span class="idx">{esc(idx)}</span>'
            f'<h2>{esc(title)}</h2>{tail}</div>'
            f'<div class="pbody">{body}</div></section>')


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
    return ('<div class="scroll"><table><thead><tr>'
            '<th>party</th><th style="text-align:right">offer</th>'
            f'<th>reasoning</th></tr></thead><tbody>{rows}</tbody></table></div>')


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
    return ('<div class="scroll"><table><thead><tr>'
            '<th style="text-align:right">seq</th><th>actor</th>'
            '<th>event</th><th>detail</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


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
    from scripts.replay.read import catalogue, storefront

    merchants = sorted(state_actors(events))
    tape_html = _desk(tape(events), merchants, catalogue(events),
                      storefront(events), sale)

    beat1 = (f'<div class="grid">{stats}</div>'
             f'<p class="lede" style="margin-top:12px">{summary.walked} of '
             f'{summary.negotiations} negotiations ended without a deal. That '
             f'is not a failure rate. Agents decline, and a market where every '
             f'deal closes is not a market.</p>')
    beat2 = (f'<p class="lede">Discovery, a real negotiation, the gate refusing '
             f'a full lot to a stranger and then allowing a smaller one, and '
             f'money moving. {len(trials)} trades in this log were refused and '
             f're-tried smaller.</p>'
             + (_trade_card(lead) if lead
                else '<div class="empty">No completed trade.</div>'))
    beat3 = f'<p class="lede">{esc(failure_note)}</p>{failure_html}'
    beat5 = (f'<p class="lede">Each merchant&rsquo;s Subconscious compresses a '
             f'whole trade into one durable sentence, typed by what it may '
             f'affect: a reliability lesson can move a counterparty&rsquo;s '
             f'standing, a behavioural one never can.</p>'
             + (lesson_html or '<div class="empty">No lessons yet.</div>'))

    panels = "".join([
        _panel("01", "The market", beat1,
               note=f"{rupees(summary.value_paise)} transacted"),
        _panel("02", "One trade, end to end", beat2, note="one correlation id"),
        _panel("03", "The failure, caught and repaired", beat3,
               note=f"{len(failures)} drifts caught"),
        _panel("04", "The intelligence economy", econ,
               note="second price, sealed bids"),
        _panel("05", "What the agents remember", beat5,
               note=f"{summary.lessons} lessons"),
    ])

    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>AGENT EXCHANGE &mdash; market replay</title>'
        f'<style>{CSS}</style></head><body>'
        '<div class="bar"><span class="mark">AGENT EXCHANGE</span>'
        '<span class="sep">/</span><span>replay</span>'
        f'<span class="sep">/</span><span>{esc(db_path)}</span>'
        f'<span class="sep">/</span><span>{summary.events} events</span>'
        f'<span class="sep">/</span><span>{summary.merchants} merchants</span>'
        '<span class="live"><span class="dot"></span>log sealed</span></div>'
        f'<div class="wrap">{tape_html}{panels}'
        f'<footer>Generated {esc(generated)} from {esc(summary.events)} events '
        f'in {esc(db_path)} &middot; gate {summary.gate_allow} allowed / '
        f'{summary.gate_deny} refused &middot; {summary.completed} payments '
        f'completed against real Razorpay test-mode orders.<br>'
        f'Nothing on this page was computed by the page. Every figure is read '
        f'from the log, or from the same projection the exchange itself runs '
        f'on.</footer></div></body></html>'
    )


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
