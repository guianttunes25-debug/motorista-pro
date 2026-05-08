"""Camada de mercado - busca, normaliza e agrega indicadores.

Une dados de candles + indicadores em um único snapshot consumido
pela strategy/engine.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import MACD, SMAIndicator, ADXIndicator

from exchange.base import BrokerClient


@dataclass
class MarketSnapshot:
    symbol: str
    price: float
    rsi: float
    sma_fast: float
    sma_slow: float
    macd: float
    macd_signal: float
    macd_hist: float
    volatility_pct: float  # desvio padrão dos retornos *100 (últimas 20 amostras)
    df: pd.DataFrame
    quote_volume_24h: float = 0.0   # volume em USDT nas últimas 24h
    change_pct_24h: float = 0.0     # variação % nas últimas 24h
    # Filtros anti-overtrading
    volume_ratio: float = 1.0       # volume da última barra / média(20). >1 = forte; <1 = fraco
    adx: float = 0.0                # 0-100. <18 = sem tendência (chop). >25 = tendência forte


class MarketDataService:
    def __init__(self, client: BrokerClient) -> None:
        self.client = client

    def fetch_snapshot(
        self,
        symbol: str,
        timeframe: str = "1m",
        rsi_period: int = 14,
        sma_fast: int = 9,
        sma_slow: int = 21,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        limit: int = 300,
    ) -> MarketSnapshot:
        df = self.client.get_ohlcv(symbol, timeframe=timeframe, limit=limit)
        if df.empty:
            raise RuntimeError("OHLCV vazio retornado da exchange.")

        close = df["close"].astype(float)
        rsi = RSIIndicator(close=close, window=rsi_period).rsi()
        sma_f = SMAIndicator(close=close, window=sma_fast).sma_indicator()
        sma_s = SMAIndicator(close=close, window=sma_slow).sma_indicator()
        macd = MACD(close=close, window_fast=macd_fast,
                    window_slow=macd_slow, window_sign=macd_signal)

        ret = close.pct_change().tail(20)
        vol_pct = float(np.nan_to_num(ret.std()) * 100.0)

        # ADX (força da tendência) — 14 períodos é o padrão de Wilder.
        # high/low precisam existir; em alguns OHLCV vêm como float NaN: tratamos.
        adx_val = 0.0
        try:
            high = df["high"].astype(float)
            low = df["low"].astype(float)
            adx_series = ADXIndicator(high=high, low=low, close=close, window=14).adx()
            adx_val = float(np.nan_to_num(adx_series.iloc[-1], nan=0.0))
        except Exception:
            pass

        # volume_ratio: vol(última barra) / média(20 barras). >1 = picada; <1 = morno.
        vol_ratio = 1.0
        try:
            vol_series = df["volume"].astype(float)
            recent = float(vol_series.iloc[-1])
            avg20 = float(vol_series.tail(20).mean())
            if avg20 > 0:
                vol_ratio = recent / avg20
        except Exception:
            pass

        # ticker (volume 24h e variação) — best-effort, não falha o snapshot se a chamada cair
        quote_vol = 0.0
        change_pct = 0.0
        try:
            t = self.client.client.fetch_ticker(symbol)
            quote_vol = float(t.get("quoteVolume") or 0.0)
            change_pct = float(t.get("percentage") or 0.0)
        except Exception:
            pass

        def _safe_last(series, fallback: float = 0.0) -> float:
            """Pega último valor de uma Series, trocando NaN/None por fallback.

            Bug histórico: usar `series.iloc[-1] or fallback` NÃO funciona
            porque `float('nan')` é truthy em Python — então o NaN passava direto.
            """
            try:
                v = series.iloc[-1]
            except Exception:
                return float(fallback)
            if v is None:
                return float(fallback)
            try:
                fv = float(v)
            except (TypeError, ValueError):
                return float(fallback)
            if np.isnan(fv) or np.isinf(fv):
                return float(fallback)
            return fv

        last_close = float(close.iloc[-1])
        return MarketSnapshot(
            symbol=symbol,
            price=last_close,
            rsi=_safe_last(rsi, 50.0),
            sma_fast=_safe_last(sma_f, last_close),
            sma_slow=_safe_last(sma_s, last_close),
            macd=_safe_last(macd.macd(), 0.0),
            macd_signal=_safe_last(macd.macd_signal(), 0.0),
            macd_hist=_safe_last(macd.macd_diff(), 0.0),
            volatility_pct=vol_pct,
            df=df,
            quote_volume_24h=quote_vol,
            change_pct_24h=change_pct,
            volume_ratio=vol_ratio,
            adx=adx_val,
        )
