"""Testes do SeniorTraderAgent (cérebro)."""
from __future__ import annotations

import pandas as pd

from core.market import MarketSnapshot
from core.strategy import Decision
from core.trader_agent import AgentConfig, SeniorTraderAgent


def _snap(price=100.0, rsi=50.0, sma_fast=100.0, sma_slow=100.0,
          macd=0.0, macd_signal=0.0, macd_hist=0.0,
          vol=0.5, ch24h=0.0, qvol=0.0):
    return MarketSnapshot(
        symbol="BTC/USDT", price=price, rsi=rsi,
        sma_fast=sma_fast, sma_slow=sma_slow,
        macd=macd, macd_signal=macd_signal, macd_hist=macd_hist,
        volatility_pct=vol, df=pd.DataFrame(),
        quote_volume_24h=qvol, change_pct_24h=ch24h,
    )


def _agent(**kw):
    base = dict(min_setup_quality=40, cooldown_seconds=0)
    return SeniorTraderAgent(AgentConfig(**{**base, **kw}))


def test_extreme_volatility_blocks():
    a = _agent()
    snap = _snap(vol=10.0)  # > extreme_vol_pct (2.5)
    raw = Decision("BUY", 3, ["x"], snap)
    d = a.evaluate(snap, raw, has_position=False)
    assert d.signal == "HOLD"
    assert d.regime == "EXTREME_VOL"


def test_ranging_blocks_entry():
    a = _agent()
    # SMAs idênticas + vol baixo → RANGING
    snap = _snap(sma_fast=100.0, sma_slow=100.0, vol=0.05)
    raw = Decision("BUY", 2, [], snap)
    d = a.evaluate(snap, raw, has_position=False)
    assert d.signal == "HOLD"
    assert d.regime == "RANGING"


def test_buy_passes_in_clear_uptrend():
    a = _agent(min_setup_quality=40)
    snap = _snap(rsi=38.0, sma_fast=101.0, sma_slow=100.0, macd_hist=0.2,
                 vol=0.5, ch24h=3.0, qvol=2e9)  # contexto bullish
    raw = Decision("BUY", 3, ["rsi", "sma", "macd"], snap)
    d = a.evaluate(snap, raw, has_position=False, news_score=1)
    assert d.signal == "BUY"
    assert d.regime == "TRENDING_UP"
    assert d.stop_loss > 0 and d.take_profit > d.stop_loss
    assert d.quality >= 40


def test_buy_blocked_by_quality_threshold():
    # Com sanity-mode, qualidade baixa só bloqueia se < min_setup_quality_floor.
    # Aqui forçamos floor muito alto também para garantir HOLD total.
    a = _agent(min_setup_quality=95, min_setup_quality_floor=95)
    snap = _snap(rsi=38.0, sma_fast=101.0, sma_slow=100.0, vol=0.5)
    raw = Decision("BUY", 1, [], snap)
    d = a.evaluate(snap, raw, has_position=False)
    assert d.signal == "HOLD"
    assert d.quality < 95


def test_buy_reduced_size_when_quality_between_floor_and_min():
    # Sanity-mode: qualidade entre floor e min → BUY com size_factor < 1.0.
    a = _agent(min_setup_quality=95, min_setup_quality_floor=20,
               risk_adjusted_min_size=0.25)
    snap = _snap(rsi=38.0, sma_fast=101.0, sma_slow=100.0, vol=0.5)
    raw = Decision("BUY", 1, [], snap)
    d = a.evaluate(snap, raw, has_position=False)
    assert d.signal == "BUY"
    assert 0.05 <= d.size_factor < 1.0


def test_buy_blocked_by_bearish_sentiment():
    a = _agent()
    snap = _snap(rsi=38.0, sma_fast=101.0, sma_slow=100.0, vol=0.5,
                 ch24h=-3.0, qvol=2e9)
    raw = Decision("BUY", 3, [], snap)
    d = a.evaluate(snap, raw, has_position=False, news_score=-1)
    assert d.signal == "HOLD"
    assert d.sentiment <= -2


def test_sell_forced_when_position_and_bearish_macro():
    a = _agent()
    snap = _snap(vol=0.5, ch24h=-3.0, qvol=2e9)
    raw = Decision("HOLD", 0, [], snap)
    d = a.evaluate(snap, raw, has_position=True, news_score=-1)
    assert d.signal == "SELL"


def test_sell_when_strategy_says_sell_and_has_position():
    a = _agent()
    snap = _snap(rsi=75.0, sma_fast=100.0, sma_slow=101.0, vol=0.5)
    raw = Decision("SELL", -2, [], snap)
    d = a.evaluate(snap, raw, has_position=True)
    assert d.signal == "SELL"


def test_hold_when_position_and_no_exit_reason():
    a = _agent()
    snap = _snap(rsi=55.0, sma_fast=101.0, sma_slow=100.0, vol=0.5)
    raw = Decision("BUY", 3, [], snap)  # mas já temos posição
    d = a.evaluate(snap, raw, has_position=True)
    assert d.signal == "HOLD"
    assert any("posição" in r.lower() for r in d.reasons)


def test_stop_loss_triggers_check_exit():
    a = _agent(stop_loss_pct=1.0, take_profit_pct=2.0)
    a.on_position_opened(100.0)
    must, reason = a.check_exit(price=98.5)
    assert must
    assert "STOP-LOSS" in reason


def test_take_profit_triggers_check_exit():
    a = _agent(stop_loss_pct=1.0, take_profit_pct=2.0)
    a.on_position_opened(100.0)
    must, reason = a.check_exit(price=102.5)
    assert must
    assert "TAKE-PROFIT" in reason


def test_no_exit_inside_band():
    a = _agent(stop_loss_pct=1.0, take_profit_pct=2.0)
    a.on_position_opened(100.0)
    must, _ = a.check_exit(price=100.5)
    assert not must


def test_position_closed_clears_plan():
    a = _agent()
    a.on_position_opened(100.0)
    assert a.has_open_plan()
    a.on_position_closed()
    assert not a.has_open_plan()
    must, _ = a.check_exit(price=50.0)  # sem posição não dispara
    assert not must


def test_cooldown_blocks_new_entry():
    a = _agent(cooldown_seconds=120)
    a.on_position_closed()  # registra last_trade_ts agora
    snap = _snap(rsi=38.0, sma_fast=101.0, sma_slow=100.0, vol=0.5)
    raw = Decision("BUY", 3, [], snap)
    d = a.evaluate(snap, raw, has_position=False)
    assert d.signal == "HOLD"
    assert any("cooldown" in r.lower() for r in d.reasons)


def test_trailing_stop_sobe_quando_lucro_passa_da_ativacao():
    """Trailing stop deve subir quando lucro >= activation, mantendo distância."""
    a = _agent(
        stop_loss_pct=1.0, take_profit_pct=10.0,
        trailing_enabled=True,
        trailing_activation_pct=0.5,
        trailing_distance_pct=0.5,
    )
    a.on_position_opened(entry_price=100.0)
    # Stop inicial é 99.0 (-1%). Sem trailing ainda (lucro 0%).
    must, _ = a.check_exit(price=100.2)
    assert not must
    # Preço sobe pra 101 (+1% > activation 0.5%) → trailing ativa
    # Novo stop = 101 * (1 - 0.005) = 100.495
    must, _ = a.check_exit(price=101.0)
    assert not must
    assert a._plan.trailing_active
    assert a._plan.stop_loss > 99.0
    assert abs(a._plan.stop_loss - 100.495) < 0.001
    # Preço cai pra 100.4 → bate trailing
    must, motivo = a.check_exit(price=100.4)
    assert must
    assert "TRAILING" in motivo


def test_trailing_stop_nao_desce():
    """Trailing stop é monotônico — só sobe, nunca desce."""
    a = _agent(
        stop_loss_pct=1.0, take_profit_pct=10.0,
        trailing_enabled=True,
        trailing_activation_pct=0.5, trailing_distance_pct=0.5,
    )
    a.on_position_opened(entry_price=100.0)
    a.check_exit(price=102.0)        # ativa trailing, stop ~ 101.49
    stop_alto = a._plan.stop_loss
    a.check_exit(price=101.5)        # preço cai mas não bate
    assert a._plan.stop_loss == stop_alto  # não desce
    a.check_exit(price=103.0)        # novo pico
    assert a._plan.stop_loss > stop_alto   # subiu


def test_trailing_desligado_usa_sl_fixo():
    a = _agent(
        stop_loss_pct=1.0, take_profit_pct=10.0,
        trailing_enabled=False,
        trailing_activation_pct=0.5, trailing_distance_pct=0.5,
    )
    a.on_position_opened(entry_price=100.0)
    a.check_exit(price=105.0)  # subiu muito mas trailing desligado
    assert a._plan.stop_loss == 99.0  # stop original mantido
    must, motivo = a.check_exit(price=98.9)
    assert must
    assert "STOP-LOSS" in motivo and "TRAILING" not in motivo
