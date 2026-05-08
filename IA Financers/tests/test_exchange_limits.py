"""Testes para core/exchange_limits.py."""
from __future__ import annotations

from core.exchange_limits import MarketLimits


def test_from_ccxt_parses_basic_fields():
    market = {
        "symbol": "BTC/BRL",
        "limits": {
            "amount": {"min": 0.00001, "max": 9000},
            "cost": {"min": 10.0, "max": None},
        },
        "precision": {"amount": 5, "price": 2},
    }
    lim = MarketLimits.from_ccxt(market)
    assert lim.symbol == "BTC/BRL"
    assert lim.min_amount == 0.00001
    assert lim.max_amount == 9000
    assert lim.min_notional == 10.0
    assert lim.amount_precision == 5
    assert lim.price_precision == 2


def test_from_ccxt_handles_empty():
    lim = MarketLimits.from_ccxt({})
    assert lim.symbol == "?"
    assert lim.min_amount == 0.0
    assert lim.max_amount == float("inf")


def test_round_amount_floors_to_step():
    lim = MarketLimits(symbol="X", amount_step=0.001, amount_precision=3)
    assert lim.round_amount(0.12345) == 0.123
    assert lim.round_amount(0.0009) == 0.0
    assert lim.round_amount(1.0) == 1.0


def test_validate_rejects_below_min_notional():
    lim = MarketLimits(symbol="X", min_amount=0.0, min_notional=10.0,
                       amount_step=0.0001, amount_precision=4)
    ok, _, reason = lim.validate_and_round(price=100.0, amount=0.05)  # notional=5
    assert not ok
    assert "min_notional" in reason


def test_validate_rejects_below_min_amount():
    lim = MarketLimits(symbol="X", min_amount=1.0, min_notional=0.0,
                       amount_step=0.1, amount_precision=1)
    ok, _, reason = lim.validate_and_round(price=100.0, amount=0.5)
    assert not ok
    assert "min_amount" in reason


def test_validate_rounds_and_passes():
    lim = MarketLimits(symbol="X", min_amount=0.001, min_notional=1.0,
                       amount_step=0.001, amount_precision=3)
    ok, adj, reason = lim.validate_and_round(price=100.0, amount=0.12345)
    assert ok
    assert adj == 0.123
    assert reason == "OK"


def test_validate_rejects_zero_amount():
    lim = MarketLimits(symbol="X")
    ok, _, _ = lim.validate_and_round(price=100, amount=0)
    assert not ok


def test_validate_rejects_zero_price():
    lim = MarketLimits(symbol="X")
    ok, _, _ = lim.validate_and_round(price=0, amount=1)
    assert not ok


def test_validate_rejects_when_round_collapses_to_zero():
    lim = MarketLimits(symbol="X", min_amount=0.0, min_notional=0.0,
                       amount_step=1.0, amount_precision=0)
    ok, adj, reason = lim.validate_and_round(price=100, amount=0.5)
    assert not ok
    assert adj == 0.0
    assert "arredondou para 0" in reason
