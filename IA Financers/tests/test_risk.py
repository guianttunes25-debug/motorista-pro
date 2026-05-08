"""Testes do RiskEngine."""
from __future__ import annotations

from datetime import date, timedelta

from core.risk import RiskConfig, RiskEngine, allow_trade


def _engine(**kw):
    cfg = RiskConfig(**{**dict(initial_balance_usdt=1000.0), **kw})
    return RiskEngine(cfg)


def test_allow_trade_ok_initial():
    e = _engine()
    ok, reason = e.allow_trade(equity=1000.0, volatility_pct=0.5)
    assert ok and reason == "ok"


def test_kill_switch_blocks():
    e = _engine()
    e.trip_kill_switch("teste")
    ok, reason = e.allow_trade(1000.0, 0.5)
    assert not ok
    assert "Kill switch" in reason


def test_pause_blocks():
    e = _engine()
    e.pause()
    ok, reason = e.allow_trade(1000.0, 0.5)
    assert not ok
    assert "pausadas" in reason
    e.resume()
    assert e.allow_trade(1000.0, 0.5)[0]


def test_high_volatility_blocks():
    e = _engine(max_volatility_pct=2.0)
    ok, reason = e.allow_trade(1000.0, volatility_pct=5.0)
    assert not ok
    assert "Volatilidade" in reason


def test_daily_loss_trips_kill_switch():
    e = _engine(max_daily_loss_pct=2.0)
    # equity caiu 3% → bate o limite
    ok, _ = e.allow_trade(equity=970.0, volatility_pct=0.5)
    assert not ok
    assert e.state.kill_switch
    assert "Perda diária" in e.state.last_reason


def test_order_size_buy_respects_max_position():
    e = _engine(trade_size_pct=10.0, max_position_pct=25.0)
    # equity 1000 → 10% = 100 USDT por trade, max position 250 USDT
    qty = e.order_size("BUY", equity=1000.0, position_value=200.0, price=100.0)
    # 100 USDT ou (250-200)=50 → fica com 50/100 = 0.5
    assert qty == 0.5


def test_order_size_buy_zero_when_position_full():
    e = _engine(trade_size_pct=10.0, max_position_pct=25.0)
    qty = e.order_size("BUY", equity=1000.0, position_value=250.0, price=100.0)
    assert qty == 0.0


def test_order_size_sell_limited_by_position():
    e = _engine(trade_size_pct=10.0)
    # tenta vender 10% do equity (100 USDT) mas só tem 30 em posição
    qty = e.order_size("SELL", equity=1000.0, position_value=30.0, price=100.0)
    assert qty == 0.3


def test_order_size_zero_price_safe():
    e = _engine()
    assert e.order_size("BUY", 1000.0, 0.0, price=0.0) == 0.0


def test_status_returns_dict():
    e = _engine()
    s = e.status(equity=1000.0)
    assert "kill_switch" in s and "daily_pnl_pct" in s
    assert s["daily_pnl_pct"] == 0.0


def test_roll_day_resets_auto_kill_switch():
    """Se o kill-switch foi acionado por perda diária, novo dia deve liberar."""
    e = _engine(max_daily_loss_pct=2.0)
    e.allow_trade(equity=970.0, volatility_pct=0.5)  # dispara kill_switch
    assert e.state.kill_switch
    # simula virada de dia
    e.state.day = date.today() - timedelta(days=1)
    e.roll_day_if_needed(equity=970.0)
    assert not e.state.kill_switch
    assert e.state.day_start_equity == 970.0


def test_roll_day_keeps_manual_kill_switch():
    """Kill-switch manual NÃO deve ser resetado pela virada do dia."""
    e = _engine()
    e.trip_kill_switch("Acionado manualmente")
    e.state.day = date.today() - timedelta(days=1)
    e.roll_day_if_needed(equity=1000.0)
    assert e.state.kill_switch  # permanece


def test_legacy_allow_trade_function():
    assert allow_trade(0.01) is True
    assert allow_trade(0.05) is False
