"""Who acted is the envelope's answer, not the payload's.

Every writer copied the actor id into the ORDER_POSTED payload as well, so
the projection read it from there and nobody noticed it was reading the copy.
The first writer that left it out — correctly, since the envelope already
carries it — brought the entire fold down with a KeyError, taking every page
with it.
"""
from exchange import events as ev
from exchange.projections import fold


class E:
    def __init__(self, seq, actor, type_, payload, corr="c1"):
        self.seq, self.actor_id, self.type = seq, actor, type_
        self.payload, self.correlation_id = payload, corr
        self.ts = "2026-09-05T10:00:00Z"


def _order(payload):
    base = {"order_id": "ord_1", "side": "ASK", "asset_ref": "ast_1",
            "qty": 10, "limit_price": 1000, "currency": "INR",
            "expires_at": "2026-12-31T00:00:00+00:00"}
    base.update(payload)
    return [E(1, "m_a", ev.ORDER_POSTED, base)]


def test_an_order_without_the_actor_in_its_payload_still_folds():
    state = fold(_order({}))

    assert state.posted_orders["ord_1"].actor_id == "m_a"


def test_the_payload_is_used_when_it_is_there():
    """Every seeded event carries it, and they agree. Changing which one wins
    would rewrite history for the existing log."""
    state = fold(_order({"actor_id": "m_a"}))

    assert state.posted_orders["ord_1"].actor_id == "m_a"


def test_a_stocked_catalogue_is_genuinely_on_the_book():
    """The shelf is not decoration: each line is an ASK anybody can buy."""
    events = [
        E(1, "m_a", ev.ASSET_LISTED,
          {"asset_id": "ast_1", "kind": "GOODS", "title": "packing tape",
           "spec": {}, "currency": "INR", "origin_actor_id": "m_a"}),
    ] + _order({})

    state = fold(events)
    order = state.open_orders["ord_1"]

    assert order.asset_ref == "ast_1"
    assert state.assets["ast_1"].title == "packing tape"
    assert str(order.side).endswith("ASK")
