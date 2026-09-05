"""The exchange, live, behind the pages.

    .venv/bin/python -m scripts.serve --merchant m_dawn

WHAT THIS IS FOR. The replay pages are static: they prove what happened, and
a static file cannot post to an order book. So the ask box on a merchant's
page could only ever word-match a catalogue read out of the log — the real
retrieval, the real gate and a real payment all lived on the command line,
which is the wrong place for the part of the product a person actually
touches.

This puts the running exchange behind the same pages. Type what you need, and
the answer comes from the same `find_supply` the agents use, the same gate
rules on it, and the money moves on a real Razorpay test-mode order.

THREE ENDPOINTS, AND THE SPLIT BETWEEN THEM IS THE PRODUCT.

    GET  /api/catalogue   what is for sale, machine-readable
    POST /api/quote       what you want, in words -> one offer. Your need is
                          posted and a counterparty is picked. No terms are
                          proposed and no money is committed.
    POST /api/buy         take the offer. THIS is where money moves, and
                          only after a person said yes.

The quote/buy split is not REST tidiness: a person must see the price before
anything is committed, and the log must be able to prove nothing was.

WHAT A QUOTE ACTUALLY WRITES, because this docstring said "nothing" and was
wrong. Asking posts a real BID — that is what searching this exchange IS,
and other agents can see it — and records which counterparty the Diplomat
picked and why. What it does NOT write is the half that binds anyone:
MATCH_PROPOSED, POLICY_DECIDED, SETTLEMENT_INITIATED and SETTLEMENT_COMPLETED
all come after the click, on the same correlation id, so the thread reads as
a question and then an answer.

NOT A PUBLIC SERVER. It binds to localhost, holds no auth, and runs a real
Razorpay client. It is a demo harness for a machine you control.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import pathlib
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DOCS = pathlib.Path("docs")

# Quotes waiting for a yes. A quote is a promise about a price, and it has to
# survive between the two requests without being trusted afterwards — the buy
# re-reads the match from the exchange rather than from anything the browser
# sends back.
_QUOTES: dict[str, dict] = {}
_LOCK = threading.Lock()


class Live:
    """One running exchange, shared by every request."""

    def __init__(self, db: str, merchant: str) -> None:
        import razorpay
        from dotenv import load_dotenv

        from exchange.agents.broker import Broker
        from exchange.config import Config
        from exchange.eventlog import EventLog
        from exchange.llm.openai_compat import providers_from_env
        from exchange.rails.credits import CreditRail
        from exchange.rails.inr import RazorpayRail
        from exchange.retrieval import HybridIndex, default_embedder
        from exchange.service import Exchange

        load_dotenv()
        cfg = Config.from_env()
        client = razorpay.Client(
            auth=(cfg.razorpay_key_id, cfg.razorpay_key_secret))
        strong, fast = providers_from_env()

        self.log = EventLog(db)
        self.exchange = Exchange(self.log,
                                 HybridIndex(embed_fn=default_embedder()),
                                 RazorpayRail(self.log, client),
                                 CreditRail(self.log))
        self.broker = Broker(merchant, self.exchange, strong,
                             fast_provider=fast)
        self.merchant = merchant
        self.db = db

    # --- what is for sale ---------------------------------------------------

    def catalogue(self) -> list[dict]:
        """Machine-readable, and deliberately so.

        An agent shopping here should not have to scrape a page. Every field
        an agent needs to decide — what it is, who sells it, how many, what
        they want per unit — with the units named rather than assumed.
        """
        state = self.exchange.state()
        out = []
        for order in state.posted_orders.values():
            if str(order.side) not in ("Side.ASK", "ASK"):
                continue
            asset = state.assets.get(order.asset_ref) if order.asset_ref else None
            out.append({
                "id": order.order_id,
                "title": (asset.title if asset else order.asset_ref) or "",
                "seller": _plain(order.actor_id),
                "seller_id": order.actor_id,
                "qty_available": order.qty,
                "unit_price_paise": order.limit_price,
                "unit_price_inr": round((order.limit_price or 0) / 100, 2),
                "currency": str(order.currency),
            })
        return out

    # --- what you want, in words -------------------------------------------

    def quote(self, need: str, qty: int, limit_paise: int) -> dict:
        """Search and price. Posts the need; commits nothing.

        `find_supply` is the same call the agents make, so the answer a person
        gets is the answer an agent would get — not a keyword match over a
        frozen catalogue.
        """
        from exchange.ids import new_id

        corr = new_id("shop")
        matches = self.broker.find_supply(
            need_text=need, qty=qty, limit_price=limit_paise,
            correlation_id=corr)
        if not matches:
            return {"found": False,
                    "why": "Nothing on the book matches that. The agents only "
                           "stock what merchants have actually listed."}

        match = self.broker.choose(matches, correlation_id=corr)
        posted = self.exchange.state().posted_orders.get(match.ask_order_id)
        seller = posted.actor_id if posted else "unknown"

        with _LOCK:
            # THE MATCH ITSELF IS HELD, not its id and not the words that
            # produced it. Re-running the search on confirm returned a
            # DIFFERENT seller at a different price — quoted 245, charged 120
            # — and a storefront that does not honour the price it showed is
            # broken however favourable the difference happens to be.
            _QUOTES[corr] = {"match": match, "seller": seller,
                             "price": match.clearing_price, "qty": match.qty,
                             "need": need, "limit": limit_paise}
        return {
            "found": True,
            "quote_id": corr,
            "seller": _plain(seller),
            "unit_price_inr": round(match.clearing_price / 100, 2),
            "qty": match.qty,
            "total_inr": round(match.clearing_price * match.qty / 100, 2),
            "within_your_limit": match.clearing_price <= limit_paise,
            "considered": len(matches),
        }

    # --- take it ------------------------------------------------------------

    def buy(self, quote_id: str) -> dict:
        """Close the trade. Everything from here is written to the log."""
        from exchange.agents.negotiation import negotiate
        from exchange.matching import resize
        from scripts.market.storefront import _affordable

        with _LOCK:
            held = _QUOTES.get(quote_id)
        if not held:
            return {"ok": False, "why": "That quote has expired. Ask again."}

        match = held["match"]

        price = match.clearing_price
        if price > held["limit"]:
            outcome = negotiate(
                buyer_id=self.broker.actor_id, seller_id=held["seller"],
                buyer_provider=self.broker.fast_tier,
                seller_provider=self.broker.fast_tier,
                opening_price=price, buyer_limit=held["limit"],
                seller_floor=int(price * 0.88))
            if not outcome.agreed or outcome.final_price is None:
                return {"ok": False, "why": f"No deal: {outcome.ended_reason}"}
            price = outcome.final_price

        decision, settlement = self.broker.close(
            match=match, seller_id=held["seller"],
            correlation_id=quote_id, agreed_price=price)

        # The same trial-size retry a broker gets. A person buying from a
        # stranger is as unproven as an agent buying from one.
        resized = False
        if settlement is None and "cap" in (decision.reason or "").lower():
            smaller = _affordable(decision, price)
            if smaller and smaller < match.qty:
                resized = True
                decision, settlement = self.broker.close(
                    match=resize(match, smaller), seller_id=held["seller"],
                    correlation_id=quote_id, agreed_price=price)

        with _LOCK:
            _QUOTES.pop(quote_id, None)

        if settlement is None:
            return {"ok": False, "gate": "refused", "why": decision.reason,
                    "quote_id": quote_id}
        return {
            "ok": True,
            "gate": "refused, then allowed" if resized else "allowed",
            "seller": _plain(held["seller"]),
            "unit_price_inr": round(price / 100, 2),
            "qty": settlement.qty if hasattr(settlement, "qty") else match.qty,
            "total_inr": round(settlement.amount / 100, 2),
            "razorpay_order_id": settlement.razorpay_order_id,
            "pay_url": getattr(settlement, "payment_link_url", None),
            "quote_id": quote_id,
            "events": [{"seq": e.seq, "type": e.type,
                        "actor": _plain(e.actor_id)}
                       for e in self.log.read_by_correlation(quote_id)],
        }


def _plain(actor_id) -> str:
    name = str(actor_id or "")
    return (name[2:] if name.startswith("m_") else name).replace("_", " ")



# --- the demo both dashboards watch -----------------------------------------
#
# WHY A SERVER CLOCK. A merchant's dashboard and Razorpay's desk are two pages
# telling the same story to two different audiences, and a demo where they
# disagree about what has happened yet is worse than either alone. So neither
# page keeps time. One clock runs here, both poll it, and each renders the
# same instant its own way: the merchant sees "negotiating", the desk sees
# NEGOTIATION_ROUND with the event number.
#
# WHAT IT PLAYS IS A REAL TRADE. Not a script — a correlation id out of the
# log, with its own event numbers, its own prices and its own gate ruling.
# Replaying it is honest and repeatable, which live buying is not: a live buy
# spends a merchant, needs the payment rail up, and cannot be run twice the
# same way in front of an audience.

STATIONS = {
    "wants":   ("Posting what you need", 0),
    "picked":  ("Finding who can supply it", 2600),
    "haggled": ("Negotiating the price", 2800),
    "gate":    ("Checking it against your limits", 3000),
    "paid":    ("Paying", 2600),
    "broke":   ("The books disagreed", 2400),
    "froze":   ("Trading paused", 2000),
    "fixed":   ("Repaired", 2400),
    "resumed": ("Trading again", 2000),
    "learned": ("What your agent will remember", 2400),
}


class Demo:
    """One recorded trade, replayed on a clock both pages read."""

    def __init__(self, db: str) -> None:
        from exchange.eventlog import EventLog
        from scripts.replay.read import rails

        log = EventLog(db)
        try:
            self.trades = rails(log.read_all(), limit=400)
        finally:
            log.close()
        self.playing: dict | None = None

    def pick(self, need: str) -> dict | None:
        """The recorded trade closest to what was typed.

        Word overlap, not a model: the point of this screen is that the
        merchant's own words reach a real trade, and a model in the middle
        would make that claim unverifiable at exactly the moment it matters.
        Ties go to the trade with the most to show.
        """
        words = {w for w in str(need or "").lower().split() if len(w) > 3}
        best, score = None, 0
        for trade in self.trades.values():
            text = str(trade.get("need") or "").lower()
            hits = sum(1 for w in words if w in text)
            weight = hits * 10 + len(trade.get("stations") or ())
            if hits and weight > score:
                best, score = trade, weight
        return best or self._richest()

    def _richest(self) -> dict | None:
        """Nothing matched. Show the fullest trade rather than nothing —
        it is the one with the refusal, the retry and the repair in it."""
        return max(self.trades.values(),
                   key=lambda t: len(t.get("stations") or ()),
                   default=None)

    def start(self, need: str, asker: str = "") -> dict:
        import time

        trade = self.pick(need)
        if trade is None:
            return {"running": False,
                    "why": "There is no recorded trade to replay."}

        steps, at = [], 0
        for station in trade.get("stations") or ():
            label, gap = STATIONS.get(station["key"], (station["key"], 2400))
            at += gap
            steps.append({
                "at_ms": at,
                "key": station["key"],
                "label": label,
                "head": station.get("head", ""),
                "lines": [l for l in (station.get("lines") or []) if l],
                "tone": station.get("tone", ""),
                "seq": station.get("seq"),
            })
        self.playing = {
            "corr": trade["corr"],
            "need": trade.get("need", ""),
            # WHO IS ASKING AND WHO TRADED ARE DIFFERENT BUSINESSES. The desk
            # announced the recorded thread's buyer as the person at the
            # keyboard — "bl koramangala is asking" while sunrise was typing.
            "asker": _plain(asker),
            "buyer": trade.get("buyer_name") or trade.get("buyer", ""),
            "asked": need,
            "steps": steps,
            "t0": time.time(),
            "total_ms": (steps[-1]["at_ms"] + 1800) if steps else 0,
        }
        return self.state()

    def state(self) -> dict:
        import time

        playing = self.playing
        if not playing:
            return {"running": False}
        elapsed = int((time.time() - playing["t0"]) * 1000)
        revealed = [s for s in playing["steps"] if s["at_ms"] <= elapsed]
        return {
            "running": True,
            "corr": playing["corr"],
            "need": playing["need"],
            "asked": playing["asked"],
            "asker": playing["asker"],
            "buyer": playing["buyer"],
            "elapsed_ms": elapsed,
            "total_ms": playing["total_ms"],
            "steps": revealed,
            "count": len(playing["steps"]),
            # What the desk needs: every event number revealed so far, so it
            # can show the raw rows for exactly the same instant.
            "seqs": [s["seq"] for s in revealed if s["seq"] is not None],
            "done": elapsed >= playing["total_ms"],
        }

    def stop(self) -> dict:
        self.playing = None
        return {"running": False}



class Handler(BaseHTTPRequestHandler):
    live: Live = None            # set on the class before serving
    demo: Demo = None

    def _send(self, code, body, ctype="application/json"):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/demo/state":
            return self._guard(lambda: self._send(200, self.demo.state()))
        if path == "/api/catalogue":
            return self._guard(lambda: self._send(200, {
                "merchant": _plain(self.live.merchant),
                "items": self.live.catalogue()}))
        if path in ("/", ""):
            path = "/index.html"
        target = (DOCS / path.lstrip("/")).resolve()
        if not str(target).startswith(str(DOCS.resolve())) or not target.is_file():
            return self._send(404, {"error": "not found"})
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), ctype)

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._body()
        if path == "/api/quote":
            return self._guard(lambda: self._send(200, self.live.quote(
                str(body.get("need", "")).strip(),
                int(body.get("qty") or 100),
                int(round(float(body.get("limit_inr") or 0) * 100)) or 10**9)))
        if path == "/api/demo/start":
            return self._guard(lambda: self._send(
                200, self.demo.start(str(body.get("need", "")).strip(),
                                     self.live.merchant)))
        if path == "/api/demo/stop":
            return self._guard(lambda: self._send(200, self.demo.stop()))
        if path == "/api/buy":
            return self._guard(lambda: self._send(
                200, self.live.buy(str(body.get("quote_id", "")))))
        self._send(404, {"error": "not found"})

    def _guard(self, fn):
        """A failure here must say what broke. A blank 500 in front of a demo
        is worse than the error itself."""
        try:
            fn()
        except Exception as exc:                       # noqa: BLE001
            traceback.print_exc()
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Serve the live exchange.")
    parser.add_argument("--db", default="runs/market.db")
    # THE DEMO MERCHANT HAS NEVER TRADED, and stays that way until the
    # camera is on. Rehearsing on it spends it: the first run through this
    # server put three trades and ₹5,055 on daybreak's page, and an
    # append-only log has no way to take them back. Rehearse as m_daybreak,
    # which has that history already, and leave this one alone.
    parser.add_argument("--merchant", default="m_dawn")
    parser.add_argument("--port", type=int, default=8795)
    args = parser.parse_args(argv)

    print(f"  opening the exchange on {args.db} as {args.merchant}…")
    Handler.live = Live(args.db, args.merchant)
    Handler.demo = Demo(args.db)
    print(f"  {len(Handler.live.catalogue())} items on the book")
    print(f"  {len(Handler.demo.trades)} recorded trades available to replay")
    print(f"\n  http://localhost:{args.port}/m-{args.merchant[2:].replace('_','-')}.html")
    print("  ctrl-c to stop\n")
    try:
        ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
