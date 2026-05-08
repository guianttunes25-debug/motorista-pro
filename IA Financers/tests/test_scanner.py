"""Testes do MarketScanner — usa fake client p/ não bater na rede."""
from __future__ import annotations

import pandas as pd

from core.scanner import (
    MarketScanner,
    PairAnalysis,
    ScanReport,
    default_symbols_for_quote,
)
from core.trader_agent import AgentConfig


class _FakeClient:
    """Devolve OHLCV sintético configurável por símbolo."""

    def __init__(self, dfs: dict[str, pd.DataFrame], tickers: dict[str, dict] | None = None,
                 raise_for: set[str] | None = None):
        self._dfs = dfs
        self._tickers = tickers or {}
        self._raise = raise_for or set()
        self.client = self

    def get_ohlcv(self, symbol, timeframe="1m", limit=300):
        if symbol in self._raise:
            raise RuntimeError(f"sem dados para {symbol}")
        return self._dfs[symbol].copy()

    def fetch_ticker(self, symbol):
        return self._tickers.get(symbol, {"quoteVolume": 1e8, "percentage": 0.5})


def _df_uptrend(n=120, start=100.0, step=0.5):
    closes = [start + i * step for i in range(n)]
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=n, freq="min"),
        "open": closes, "high": closes, "low": closes,
        "close": closes, "volume": [1.0] * n,
    })


def _df_flat(n=120, price=100.0):
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=n, freq="min"),
        "open": [price] * n, "high": [price] * n, "low": [price] * n,
        "close": [price] * n, "volume": [1.0] * n,
    })


def test_default_symbols_for_quote():
    brl = default_symbols_for_quote("BRL")
    usdt = default_symbols_for_quote("usdt")
    assert "BTC/BRL" in brl
    assert "BTC/USDT" in usdt
    assert all("/" in s for s in brl + usdt)


def test_pair_analysis_confidence_labels():
    assert PairAnalysis(symbol="X", quality=80).confidence_label.startswith("🟢")
    assert PairAnalysis(symbol="X", quality=60).confidence_label.startswith("🟡")
    assert PairAnalysis(symbol="X", quality=40).confidence_label.startswith("🟠")
    assert PairAnalysis(symbol="X", quality=10).confidence_label.startswith("🔴")
    assert PairAnalysis(symbol="X", error="boom").confidence_label == "❌ ERRO"


def test_scanner_returns_report_with_all_symbols():
    syms = ["AAA/BRL", "BBB/BRL"]
    client = _FakeClient({s: _df_uptrend() for s in syms})
    scanner = MarketScanner(client, agent_cfg=AgentConfig(min_setup_quality=0))
    report = scanner.scan(syms)
    assert isinstance(report, ScanReport)
    assert len(report.analyses) == 2
    assert all(a.symbol in syms for a in report.analyses)
    assert all(not a.error for a in report.analyses)


def test_scanner_isolates_per_symbol_failure():
    """Falha num símbolo NÃO deve derrubar os outros."""
    syms = ["OK/BRL", "BAD/BRL"]
    client = _FakeClient({"OK/BRL": _df_uptrend()}, raise_for={"BAD/BRL"})
    scanner = MarketScanner(client)
    report = scanner.scan(syms)
    bad = [a for a in report.analyses if a.symbol == "BAD/BRL"][0]
    ok = [a for a in report.analyses if a.symbol == "OK/BRL"][0]
    assert bad.error
    assert not ok.error


def test_scanner_best_picks_highest_quality():
    syms = ["LOW/BRL", "HIGH/BRL"]
    client = _FakeClient({
        "LOW/BRL": _df_flat(),         # mercado parado → baixa qualidade
        "HIGH/BRL": _df_uptrend(),     # tendência clara → qualidade maior
    })
    scanner = MarketScanner(client, agent_cfg=AgentConfig(min_setup_quality=0))
    report = scanner.scan(syms)
    best = report.best
    assert best is not None
    assert best.quality >= max(a.quality for a in report.analyses)


def test_scanner_best_returns_none_when_all_fail():
    syms = ["X/BRL", "Y/BRL"]
    client = _FakeClient({}, raise_for=set(syms))
    scanner = MarketScanner(client)
    report = scanner.scan(syms)
    assert report.best is None
    assert all(a.error for a in report.analyses)


def test_scanner_progress_callback_called():
    calls: list[tuple[int, int, str]] = []
    syms = ["A/BRL", "B/BRL", "C/BRL"]
    client = _FakeClient({s: _df_uptrend() for s in syms})
    scanner = MarketScanner(client)
    scanner.scan(syms, progress=lambda i, t, s: calls.append((i, t, s)))
    assert len(calls) == 3
    assert calls[0] == (1, 3, "A/BRL")
    assert calls[-1] == (3, 3, "C/BRL")


def test_scan_report_to_text_contains_recommendation():
    syms = ["BTC/BRL", "ETH/BRL"]
    client = _FakeClient({s: _df_uptrend() for s in syms})
    scanner = MarketScanner(client, agent_cfg=AgentConfig(min_setup_quality=0))
    text = scanner.scan(syms).to_text()
    assert "BRIEFING DE MERCADO" in text
    assert "RECOMENDAÇÃO DA IA" in text
    assert "BTC/BRL" in text


def test_scan_report_to_text_when_all_fail():
    syms = ["X/BRL"]
    client = _FakeClient({}, raise_for=set(syms))
    scanner = MarketScanner(client)
    text = scanner.scan(syms).to_text()
    assert "Nenhum par" in text
