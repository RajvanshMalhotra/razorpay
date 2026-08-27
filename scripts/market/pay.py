"""Pay test-mode payment links without a human.

    .venv/bin/python -m scripts.market.pay runs/market.db --limit 25

WHY THIS EXISTS AS A SCRIPT RATHER THAN A CHORE. A settlement completes only
when its link is paid — probed, not assumed: server-side payment creation
returns 403 on these accounts. Everything downstream waits on it: orders do
not fill, no points are minted, the Subconscious files no lessons, and the
house agent has nothing to mine. Twenty-five payments at a minute and a half
each is an afternoon; the same twenty-five here take about two minutes.

THE POPUP IS THE WHOLE PROBLEM, and the reason the browser extension could
not do this. Choosing netbanking opens a SEPARATE WINDOW — Razorpay's mock
bank, at `api.razorpay.com/v1/gateway/mocksharp/payment` — with Success and
Failure buttons. The extension's tab group never contained that window, so
automation sat watching a spinner while the decisive click waited somewhere
it could not reach. Playwright sees popups (`page.expect_popup`), so it can
finish what it started.

PAYS ONE LINK PER MERCHANT BY DEFAULT. The privacy floor counts DISTINCT
contributing merchants, so a second payment for a merchant that already has
one buys nothing toward it and spends a capped resource — test mode allows
30 links per account — that another merchant needs.

VERIFIED THROUGH THE API, NEVER THROUGH THE PAGE. The checkout shows a
"Processing your payment" spinner that outlives the payment itself; an
earlier session waited sixty seconds on one that had already captured. The
page is a way to cause a payment, not a way to learn about one, so success
is whatever `payment_link.fetch` says it is.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field

TEST_CONTACT = "9812345670"          # a valid-looking test number
TEST_EMAIL = "merchant@example.com"


@dataclass
class PayReport:
    paid: list = field(default_factory=list)
    failed: list = field(default_factory=list)
    skipped: int = 0

    def __str__(self) -> str:
        return (f"paid {len(self.paid)}, failed {len(self.failed)}, "
                f"skipped {self.skipped}")


def _checkout_frame(page, timeout_ms: int):
    """The one frame of twelve that holds the checkout."""
    deadline = timeout_ms
    step = 500
    while deadline > 0:
        for frame in page.frames:
            if "checkout" in (frame.url or ""):
                if frame.locator("input[name='contact']").count():
                    return frame
        page.wait_for_timeout(step)
        deadline -= step
    raise RuntimeError("checkout frame never appeared")


def pay_one(page, url: str, timeout_ms: int = 45_000) -> str:
    """Drive one link to a captured payment. Returns a short outcome word."""
    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

    # THE CHECKOUT IS AN IFRAME, and page-level locators only search the main
    # frame. The page has twelve frames; exactly one holds the contact input,
    # so the flow is driven inside that frame rather than at the top level —
    # a page-level `get_by_placeholder("Mobile number")` simply waits forever.
    frame = _checkout_frame(page, timeout_ms)

    contact = frame.get_by_placeholder("Mobile number")
    contact.wait_for(state="visible", timeout=timeout_ms)
    contact.click()
    contact.fill(TEST_CONTACT)
    frame.get_by_role("button", name="Continue").first.click()

    # Netbanking, not cards: cards are rejected on these accounts with
    # "International cards are not supported", and netbanking has fewer
    # fields and a deterministic result page.
    # A NAMED BANK UNDER "Recommended", not the "Netbanking" accordion. The
    # accordion expands a list and waits for a choice; a recommended row like
    # "Canara Bank Netbanking" is one click straight through to the bank.
    # Clicking the accordion and then hunting for a bank is how this spent
    # forty-five seconds waiting for a popup that was never triggered.
    named_bank = frame.get_by_text(
        re.compile(r"\w+\s+Bank\s+Netbanking", re.I)).first
    named_bank.wait_for(state="visible", timeout=timeout_ms)

    # THE POPUP MUST BE AWAITED AROUND THE CLICK THAT CAUSES IT. Playwright's
    # `expect_popup` arms a listener and then runs the block; an empty block
    # arms it after the window has already opened.
    with page.expect_popup(timeout=timeout_ms) as popup_info:
        named_bank.click()
    bank_page = popup_info.value
    bank_page.wait_for_load_state("domcontentloaded")
    bank_page.get_by_role("button", name="Success").click()
    bank_page.wait_for_event("close", timeout=timeout_ms)
    return "submitted"


def pay_all(log, client, limit: int = 25, one_per_merchant: bool = True,
            headless: bool = True) -> PayReport:
    from playwright.sync_api import sync_playwright

    from scripts.market.clerk import pending_payments

    report = PayReport()
    payable = pending_payments(log).payable

    seen: set[str] = set()
    queue = []
    for settlement in payable:
        if one_per_merchant and settlement.actor_id in seen:
            report.skipped += 1
            continue
        seen.add(settlement.actor_id)
        queue.append(settlement)
        if len(queue) >= limit:
            break

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        for settlement in queue:
            # ASK BEFORE DRIVING. The clerk reads the log, and a link paid
            # outside this script — by hand, or by an earlier run whose
            # repair has not happened yet — is still "payable" as far as the
            # log knows. Opening it wastes 45 seconds discovering the form is
            # gone, and in the worst case pays twice, which in test mode is
            # as real as the first payment.
            try:
                existing = client.payment_link.fetch(settlement.payment_link_id)
                already = [q for q in (existing.get("payments") or [])
                           if q.get("status") == "captured"]
                if already:
                    report.paid.append((settlement.settlement_id,
                                        already[0]["payment_id"]))
                    continue
            except Exception:  # noqa: BLE001 - an unfetchable link is still worth trying
                pass

            try:
                pay_one(page, settlement.payment_link_url)
            except Exception as exc:  # noqa: BLE001 - one link is not the batch
                report.failed.append((settlement.settlement_id,
                                      f"{type(exc).__name__}: {exc}"[:120]))
                continue
            # The page is a way to CAUSE a payment, not to learn about one.
            try:
                link = client.payment_link.fetch(settlement.payment_link_id)
                captured = [q for q in (link.get("payments") or [])
                            if q.get("status") == "captured"]
                if captured:
                    report.paid.append((settlement.settlement_id,
                                        captured[0]["payment_id"]))
                else:
                    report.failed.append((settlement.settlement_id,
                                          f"submitted but link is {link.get('status')}"))
            except Exception as exc:  # noqa: BLE001
                report.failed.append((settlement.settlement_id,
                                      f"verify failed: {type(exc).__name__}"))
        browser.close()
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Pay test-mode payment links.")
    parser.add_argument("db", nargs="?", default="runs/market.db")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--all-settlements", action="store_true",
                        help="pay every payable link, not one per merchant")
    parser.add_argument("--show", action="store_true",
                        help="run the browser visibly")
    args = parser.parse_args(argv)

    import razorpay
    from dotenv import load_dotenv

    from exchange.config import Config
    from exchange.eventlog import EventLog

    load_dotenv()
    cfg = Config.from_env()
    client = razorpay.Client(auth=(cfg.razorpay_key_id, cfg.razorpay_key_secret))

    log = EventLog(args.db)
    try:
        report = pay_all(log, client, limit=args.limit,
                         one_per_merchant=not args.all_settlements,
                         headless=not args.show)
    finally:
        log.close()

    print(f"  {report}")
    for settlement_id, payment_id in report.paid[:40]:
        print(f"    paid   {settlement_id}  {payment_id}")
    for settlement_id, reason in report.failed[:10]:
        print(f"    FAILED {settlement_id}  {reason}")
    return 0 if report.paid else 1


if __name__ == "__main__":
    sys.exit(main())
