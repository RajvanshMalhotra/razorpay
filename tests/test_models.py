import pytest

from exchange.models import (
    Asset,
    AssetKind,
    Currency,
    Order,
    Side,
    currency_for_kind,
)


def test_currency_for_kind_maps_goods_and_services_to_inr():
    assert currency_for_kind(AssetKind.GOODS) == Currency.INR
    assert currency_for_kind(AssetKind.SERVICE) == Currency.INR


def test_currency_for_kind_maps_insight_to_credits():
    assert currency_for_kind(AssetKind.INSIGHT) == Currency.CREDITS


def test_asset_rejects_insight_priced_in_inr():
    with pytest.raises(ValueError, match="INSIGHT"):
        Asset(
            asset_id="ast_1",
            kind=AssetKind.INSIGHT,
            title="Skincare AOV up 12%",
            spec={},
            currency=Currency.INR,
            origin_actor_id="house",
        )


def test_asset_rejects_goods_priced_in_credits():
    with pytest.raises(ValueError, match="GOODS"):
        Asset(
            asset_id="ast_1",
            kind=AssetKind.GOODS,
            title="Corrugated boxes",
            spec={},
            currency=Currency.CREDITS,
            origin_actor_id="merchant_a",
        )


def test_asset_accepts_valid_pairing():
    asset = Asset(
        asset_id="ast_1",
        kind=AssetKind.GOODS,
        title="Corrugated boxes",
        spec={"material": "kraft"},
        currency=Currency.INR,
        origin_actor_id="merchant_a",
    )
    assert asset.currency == Currency.INR


def test_order_is_descriptive_when_it_carries_a_query():
    order = Order(
        order_id="ord_1",
        actor_id="merchant_a",
        side=Side.BID,
        asset_ref=None,
        asset_query={"text": "eco packaging", "max_unit_price": 2200},
        qty=500,
        limit_price=1100000,
        currency=Currency.INR,
        expires_at="2026-09-01T00:00:00+00:00",
        policy_snapshot={},
    )
    assert order.is_descriptive is True


def test_order_is_not_descriptive_when_it_references_an_asset():
    order = Order(
        order_id="ord_1",
        actor_id="merchant_a",
        side=Side.BID,
        asset_ref="ast_1",
        asset_query=None,
        qty=500,
        limit_price=1100000,
        currency=Currency.INR,
        expires_at="2026-09-01T00:00:00+00:00",
        policy_snapshot={},
    )
    assert order.is_descriptive is False


def test_order_rejects_both_ref_and_query():
    with pytest.raises(ValueError, match="exactly one"):
        Order(
            order_id="ord_1",
            actor_id="merchant_a",
            side=Side.BID,
            asset_ref="ast_1",
            asset_query={"text": "eco packaging"},
            qty=500,
            limit_price=1100000,
            currency=Currency.INR,
            expires_at="2026-09-01T00:00:00+00:00",
            policy_snapshot={},
        )


def test_order_rejects_neither_ref_nor_query():
    with pytest.raises(ValueError, match="exactly one"):
        Order(
            order_id="ord_1",
            actor_id="merchant_a",
            side=Side.BID,
            asset_ref=None,
            asset_query=None,
            qty=500,
            limit_price=1100000,
            currency=Currency.INR,
            expires_at="2026-09-01T00:00:00+00:00",
            policy_snapshot={},
        )
