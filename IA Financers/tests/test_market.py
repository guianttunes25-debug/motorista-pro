"""Testes do MarketDataService — usa fake client para evitar rede."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.market import MarketDataService, MarketSnapshot


class _FakeBinance:
    """Client minimalista que retorna OHLCV sintético."""

    def __init__(self, df: pd.DataFrame, ticker: dict | None = None,
                 raise_ticker: bool = False):
        self._df = df
        self._ticker = ticker or {"quoteVolume": 1.5e9, "percentage": 1.23}
        self._raise = raise_ticker
        # mimics ccxt sub-client
        self.client = self

    def get_ohlcv(self, symbol, timeframe="1m", limit=200):
        return self._df.copy()

    def fetch_ticker(self, symbol):
        if self._raise:
            raise RuntimeError("ticker offline")
        return self._ticker


def _gen_df(n=120, start=100.0, step=0.1):
    closes = [start + i * step for i in range(n)]
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=n, freq="min"),
        "open": closes, "high": closes, "low": closes,
        "close": closes, "volume": [1.0] * n,
    })


def test_fetch_snapshot_basic():
    svc = MarketDataService(_FakeBinance(_gen_df()))
    snap = svc.fetch_snapshot("BTC/USDT", "1m")
    assert isinstance(snap, MarketSnapshot)
    assert snap.price > 100
    assert 0 <= snap.rsi <= 100
    assert snap.quote_volume_24h == 1.5e9
    assert snap.change_pct_24h == 1.23


def test_fetch_snapshot_handles_ticker_failure():
    """Falha no fetch_ticker NÃO deve quebrar o snapshot."""
    svc = MarketDataService(_FakeBinance(_gen_df(), raise_ticker=True))
    snap = svc.fetch_snapshot("BTC/USDT", "1m")
    assert snap.quote_volume_24h == 0.0
    assert snap.change_pct_24h == 0.0


def test_fetch_snapshot_with_constant_prices_no_nan():
    """Regressão do bug `nan or 0.0`: preços constantes geravam NaN
    em macd_hist e o `or 0.0` NÃO removia (nan é truthy)."""
    n = 80
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=n, freq="min"),
        "open": [100.0] * n, "high": [100.0] * n, "low": [100.0] * n,
        "close": [100.0] * n, "volume": [1.0] * n,
    })
    svc = MarketDataService(_FakeBinance(df))
    snap = svc.fetch_snapshot("BTC/USDT", "1m")
    assert not np.isnan(snap.macd)
    assert not np.isnan(snap.macd_signal)
    assert not np.isnan(snap.macd_hist)
    assert not np.isnan(snap.rsi)
    assert not np.isnan(snap.volatility_pct)


def test_fetch_snapshot_empty_raises():
    svc = MarketDataService(_FakeBinance(pd.DataFrame(
        columns=["timestamp", "open", "high", "low", "close", "volume"]
    )))
    try:
        svc.fetch_snapshot("BTC/USDT", "1m")
    except RuntimeError as e:
        assert "vazio" in str(e).lower()
    else:
        raise AssertionError("RuntimeError esperado")
