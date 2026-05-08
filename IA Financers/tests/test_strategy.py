"""Testes da Strategy (sistema de score multifator)."""
from __future__ import annotations

import pandas as pd

from core.market import MarketSnapshot
from core.strategy import ScoringStrategy, StrategyConfig, simple_strategy


def _snap(rsi=50.0, sma_fast=100.0, sma_slow=100.0,
          macd=0.0, macd_signal=0.0, macd_hist=0.0,
          price=100.0, vol=0.5):
    return MarketSnapshot(
        symbol="BTC/USDT", price=price, rsi=rsi,
        sma_fast=sma_fast, sma_slow=sma_slow,
        macd=macd, macd_signal=macd_signal, macd_hist=macd_hist,
        volatility_pct=vol, df=pd.DataFrame(),
    )


def test_strategy_buy_when_all_factors_align():
    strat = ScoringStrategy(StrategyConfig())
    snap = _snap(rsi=20.0, sma_fast=110.0, sma_slow=100.0,
                 macd=1.0, macd_signal=0.5, macd_hist=0.5)
    dec = strat.decide(snap, news_score=1)
    assert dec.signal == "BUY"
    assert dec.score >= 2
    assert any("RSI" in r for r in dec.reasons)


def test_strategy_sell_when_all_factors_negative():
    strat = ScoringStrategy(StrategyConfig())
    snap = _snap(rsi=80.0, sma_fast=90.0, sma_slow=100.0,
                 macd=-1.0, macd_signal=-0.5, macd_hist=-0.5)
    dec = strat.decide(snap, news_score=-1)
    assert dec.signal == "SELL"
    assert dec.score <= -2


def test_strategy_hold_when_neutral():
    strat = ScoringStrategy(StrategyConfig())
    snap = _snap(rsi=50.0, sma_fast=100.0, sma_slow=100.0)
    dec = strat.decide(snap)
    assert dec.signal == "HOLD"
    assert dec.score == 0


def test_strategy_thresholds_customizable():
    strat = ScoringStrategy(StrategyConfig(score_buy_threshold=3))
    snap = _snap(rsi=20.0, sma_fast=110.0, sma_slow=100.0,
                 macd=1.0, macd_signal=0.5, macd_hist=0.5)
    # score = 3 (RSI + SMA + MACD), threshold=3 → ainda BUY
    dec = strat.decide(snap)
    assert dec.signal == "BUY"
    # com threshold=4 deveria HOLD
    strat2 = ScoringStrategy(StrategyConfig(score_buy_threshold=4))
    assert strat2.decide(snap).signal == "HOLD"


def test_strategy_macd_requires_both_sides():
    """MACD só conta quando macd vs signal E hist concordam."""
    strat = ScoringStrategy(StrategyConfig())
    # macd > signal mas hist negativo → não soma
    snap = _snap(macd=1.0, macd_signal=0.5, macd_hist=-0.1)
    dec = strat.decide(snap)
    assert not any("MACD" in r for r in dec.reasons)


def test_simple_strategy():
    assert simple_strategy(20) == "BUY"
    assert simple_strategy(80) == "SELL"
    assert simple_strategy(50) == "HOLD"
