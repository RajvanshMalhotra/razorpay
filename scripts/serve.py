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
        # WHAT IS STILL ON THE BOOK, not what was ever posted. `posted_orders`
        # never forgets — deliberately, so the audit trail can show what was
        # asked — but a shop window built from it advertises listings that
        # have been filled or withdrawn.
        for order in state.open_orders.values():
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

# The station keys are the rail's own — `repaired` and `remembered`, not
# `fixed` and `learned`. Two were wrong, so those stages fell through to the
# raw key: a merchant read a stage headed "remembered" over a line that said
# "reliability", which is a label and a value that never met.
STATIONS = {
    "wants":     ("Posting what you need", 0),
    "picked":    ("Finding who can supply it", 2600),
    "haggled":   ("Negotiating the price", 2800),
    "gate":      ("Checking it against your limits", 3000),
    "paid":      ("Paying", 2600),
    "broke":     ("Your books and Razorpay disagreed", 2600),
    "froze":     ("Trading paused while it was checked", 2200),
    "repaired":  ("Put right, from Razorpay's own record", 2400),
    "resumed":   ("Trading again", 2000),
    "remembered": ("What your agent will remember", 2600),
}


class Demo:
    """The merchant's OWN agent, run live, watched by both dashboards.

    THIS USED TO REPLAY SOMEBODY ELSE'S TRADE. It was safe and repeatable and
    it never stopped being confusing: a person typed a need into Dawn's
    dashboard and watched bl thirdwave buy something, for a different quantity
    at a different price. Every label added to explain that made the screen
    busier without making it true.

    So it does the real thing. The merchant at the keyboard posts its own
    need, its own Diplomat picks a counterparty and says why, its own Trader
    negotiates, the gate rules on ITS money, and Razorpay takes the payment.
    Both dashboards then read the same correlation id out of the log while it
    is being written — the merchant sees stages, the desk sees the events.

    NOTHING PACES THIS. There is no clock, because the work is the clock: a
    stage appears when the event behind it is written. What you watch is how
    long it actually took.
    """

    def __init__(self, db: str, live) -> None:
        self.db = db
        self.live = live
        self.running: dict | None = None
        self.lock = threading.Lock()

    # --- what a person typed ------------------------------------------------

    @staticmethod
    def figures(need: str) -> tuple:
        """The quantity and the per-unit price in a person's own sentence.

        A merchant does not type a noun and stop. It types "220 units at ₹499
        each", because that is how people ask for things — and a screen that
        answers with different numbers looks like it ignored them.
        """
        import re

        text = str(need or "").lower().replace(",", "")
        qty = None
        found = re.search(r"(\d{2,6})\s*(?:units?|pcs|pieces|nos)\b", text)
        if found:
            qty = int(found.group(1))
        cap = None
        found = re.search(r"(?:at|under|below|max|upto|up to|for)\s*"
                          r"(?:₹|rs\.?|inr)?\s*(\d{1,6})(?:\s*(?:each|per|"
                          r"a unit|/unit|apiece))?", text)
        if found:
            cap = int(found.group(1))
        if cap is not None and qty is not None and cap == qty:
            cap = None
        return qty, cap

    # --- run it -------------------------------------------------------------

    def start(self, need: str, _asker: str = "") -> dict:
        from exchange.ids import new_id

        with self.lock:
            if self.running and not self.running.get("done"):
                return self.state()          # one at a time
            qty, cap = self.figures(need)
            corr = new_id("shop")
            self.running = {
                "corr": corr, "asked": need, "asker": _plain(self.live.merchant),
                "asked_qty": qty, "asked_cap": cap,
                "qty": qty or 40, "cap_paise": (cap or 500) * 100,
                "done": False, "why": "",
            }
        threading.Thread(target=self._work, args=(dict(self.running),),
                         daemon=True).start()
        return self.state()

    def _work(self, plan: dict) -> None:
        """The real trade, on the real exchange, as the real merchant."""
        corr = plan["corr"]
        try:
            broker = self.live.broker
            matches = broker.find_supply(
                need_text=plan["asked"], qty=plan["qty"],
                limit_price=plan["cap_paise"], correlation_id=corr)
            if not matches:
                self._finish("Nothing on the book matches that. Your agents "
                             "only stock what other merchants have listed.")
                return

            match = broker.choose(matches, correlation_id=corr)
            state = self.live.exchange.state()
            posted = state.posted_orders.get(match.ask_order_id)
            seller = posted.actor_id if posted else "unknown"

            price = match.clearing_price
            if price > plan["cap_paise"]:
                from exchange.agents.negotiation import negotiate
                outcome = negotiate(
                    buyer_id=broker.actor_id, seller_id=seller,
                    buyer_provider=broker.fast_tier,
                    seller_provider=broker.fast_tier,
                    opening_price=price, buyer_limit=plan["cap_paise"],
                    seller_floor=int(price * 0.88))
                if not outcome.agreed or outcome.final_price is None:
                    self._finish(f"No deal: {outcome.ended_reason}")
                    return
                price = outcome.final_price

            decision, settlement = broker.close(
                match=match, seller_id=seller, correlation_id=corr,
                agreed_price=price)

            # THE SAME TRIAL-SIZE RETRY A BROKER GETS. A person buying from a
            # stranger is as unproven as an agent buying from one, and this is
            # the step that shows the cap doing its job rather than just
            # blocking the demo.
            if settlement is None and "cap" in (decision.reason or "").lower():
                from exchange.matching import resize
                from scripts.market.storefront import _affordable
                smaller = _affordable(decision, price)
                if smaller and smaller < match.qty:
                    decision, settlement = broker.close(
                        match=resize(match, smaller), seller_id=seller,
                        correlation_id=corr, agreed_price=price)

            self._finish("" if settlement is not None
                         else (decision.reason or "The gate refused it."))
        except Exception as error:                       # noqa: BLE001
            traceback.print_exc()
            self._finish(f"{type(error).__name__}: {error}")

    def _finish(self, why: str) -> None:
        with self.lock:
            if self.running:
                self.running["done"] = True
                self.running["why"] = why
        if not why:
            self._rebuild()

    def _rebuild(self) -> None:
        """Rebuild the pages the trade just changed.

        THE PAGES ARE STATIC FILES BUILT FROM THE LOG. So after a live trade
        the log knew about it and the page did not, and the screen said
        "reload this page and the trade is on your rail" — which reloaded the
        same file that was built before the trade and showed nothing. A page
        that tells you to refresh had better be a page that refreshing
        changes.

        Only the two pages this trade touched: the merchant's own, and the
        desk. Rebuilding the whole roster would take long enough to be
        noticed, and nothing else moved.
        """
        try:
            from scripts.replay.generate import (_page_name, build_desk,
                                                 build_merchant, state_actors)
            from scripts.replay.read import load

            _s, _t, events = load(self.db)
            roster = sorted(state_actors(events))
            actor = self.live.merchant
            (DOCS / _page_name(actor)).write_text(
                build_merchant(self.db, actor, roster), encoding="utf-8")
            (DOCS / "desk.html").write_text(build_desk(self.db),
                                            encoding="utf-8")
            print(f"  rebuilt {_page_name(actor)} and desk.html "
                  f"— the trade is on the rail now")
        except Exception as error:                       # noqa: BLE001
            # A page that failed to rebuild is a stale page, not a lost
            # trade: the log still has every event.
            print(f"  could not rebuild the pages: "
                  f"{type(error).__name__}: {error}")

    # --- what both dashboards read -----------------------------------------

    def state(self) -> dict:
        """Read the trade back out of the log as it is being written.

        The stages are built by the same `rails` the replay pages use, so a
        stage on this screen and a station on a merchant's own rail are the
        same thing rendered twice — which is why they cannot disagree.
        """
        from exchange.eventlog import EventLog
        from scripts.replay.read import rails

        with self.lock:
            run = dict(self.running) if self.running else None
        if not run:
            return {"running": False}

        log = EventLog(self.db)
        try:
            everything = log.read_all()
        finally:
            log.close()
        events = [e for e in everything if e.correlation_id == run["corr"]]

        # RAILS NEEDS THE WHOLE LOG, NOT JUST THIS THREAD. The seller's ASK
        # was posted under its own correlation id, so filtering first left
        # the station with nothing to name and it read "a counterparty" —
        # and the negotiation, which hangs off the same lookup, vanished.
        trade = rails(everything).get(run["corr"]) if events else None
        stations = (trade or {}).get("stations") or []
        steps = [{
            "key": st["key"],
            "label": STATIONS.get(st["key"], (st["key"],))[0],
            "head": st.get("head", ""),
            "lines": [l for l in (st.get("lines") or []) if l],
            "tone": st.get("tone", ""),
            "seq": st.get("seq"),
        } for st in stations]
        for step in steps:                     # a head that repeats its label
            words = {w for w in step["head"].lower().split() if len(w) > 3}
            if words and words <= set(step["label"].lower().split()):
                step["head"] = ""

        return {
            "running": True,
            "live": True,
            "corr": run["corr"],
            "asked": run["asked"],
            "asker": run["asker"],
            "buyer": run["asker"],
            "need": run["asked"],
            "asked_qty": run["asked_qty"],
            "asked_cap": run["asked_cap"],
            "steps": steps,
            "count": len(steps),
            "seqs": [e.seq for e in events],
            "done": run["done"],
            "why": run["why"],
        }

    def stop(self) -> dict:
        with self.lock:
            self.running = None
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
    Handler.demo = Demo(args.db, Handler.live)
    print(f"  {len(Handler.live.catalogue())} items on the book")
    print(f"  asking runs {args.merchant[2:]}'s own agent, live")
    print(f"\n  http://localhost:{args.port}/m-{args.merchant[2:].replace('_','-')}.html")
    print("  ctrl-c to stop\n")
    try:
        ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
