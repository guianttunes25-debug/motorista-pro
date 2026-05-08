"""Testes do market_sentiment_engine."""
from __future__ import annotations

from core.sentiment import market_sentiment_engine


def test_neutral_sentiment():
    s = market_sentiment_engine(change_pct_24h=0.5, quote_volume_24h=1e6)
    assert s.score == 0
    assert not s.bullish and not s.bearish


def test_strong_bullish():
    s = market_sentiment_engine(change_pct_24h=3.0, quote_volume_24h=2e9, news_score=1)
    assert s.score == 3
    assert s.bullish and not s.bearish


def test_strong_bearish():
    s = market_sentiment_engine(change_pct_24h=-3.0, quote_volume_24h=2e9, news_score=-1)
    assert s.score == -3
    assert s.bearish and not s.bullish


def test_score_clamped_to_3():
    """Mesmo somando muitos fatores positivos, score deve ser clamp em +3."""
    s = market_sentiment_engine(change_pct_24h=10.0, quote_volume_24h=1e12, news_score=5)
    assert s.score == 3


def test_score_clamped_to_minus_3():
    s = market_sentiment_engine(change_pct_24h=-10.0, quote_volume_24h=1e12, news_score=-5)
    assert s.score == -3


def test_high_volume_with_drop_is_bearish():
    s = market_sentiment_engine(change_pct_24h=-2.5, quote_volume_24h=2e9)
    # -1 (variação) + -1 (volume reforça queda) = -2 → bearish
    assert s.score == -2
    assert s.bearish


def test_low_volume_does_not_amplify():
    s = market_sentiment_engine(change_pct_24h=2.5, quote_volume_24h=1e6)
    # +1 só
    assert s.score == 1


def test_reasons_populated():
    s = market_sentiment_engine(change_pct_24h=3.0, quote_volume_24h=2e9, news_score=2)
    assert len(s.reasons) >= 2
    assert any("variação" in r for r in s.reasons)
