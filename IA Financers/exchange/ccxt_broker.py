"""Broker genérico via ccxt — desbloqueia 100+ exchanges cripto com 1 arquivo.

Suporta Binance, Bybit, Kraken, Coinbase, OKX, KuCoin, Bitget, MEXC, Gate.io,
Mercado Bitcoin, Foxbit, NovaDAX, Bitso e tudo mais que o ccxt expõe.

Uso:
    >>> from exchange.ccxt_broker import CCXTBroker, CCXTConfig
    >>> b = CCXTBroker(CCXTConfig(exchange_id="bybit", use_testnet=True))
    >>> b.get_price("BTC/USDT")

Modo público (sem chaves) funciona para preço/candles. Para ordens reais,
informe `api_key`/`api_secret` (e `password` se a exchange exigir).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

import ccxt
import pandas as pd

from exchange.base import BrokerClient, OrderSide

T = TypeVar("T")
log = logging.getLogger("exchange.ccxt_broker")

_TRANSIENT = (
    ccxt.NetworkError,
    ccxt.RequestTimeout,
    ccxt.DDoSProtection,
    ccxt.ExchangeNotAvailable,
    ccxt.RateLimitExceeded,
)


@dataclass
class CCXTConfig:
    exchange_id: str = "binance"     # 'binance', 'bybit', 'kraken', 'mercado'...
    api_key: str = ""
    api_secret: str = ""
    password: str = ""               # algumas exchanges (kucoin, okx) exigem
    use_testnet: bool = True
    max_retries: int = 4
    backoff_base_seconds: float = 0.8


class CCXTBroker(BrokerClient):
    """Adapter universal para qualquer exchange suportada pelo ccxt."""

    def __init__(self, cfg: CCXTConfig | None = None) -> None:
        self.cfg = cfg or CCXTConfig()
        if not hasattr(ccxt, self.cfg.exchange_id):
            raise ValueError(
                f"Exchange '{self.cfg.exchange_id}' não suportada pelo ccxt. "
                f"Veja ccxt.exchanges para a lista completa."
            )
        self.name = self.cfg.exchange_id

        params: dict = {"enableRateLimit": True, "timeout": 15000}
        if self.cfg.api_key and self.cfg.api_secret:
            params["apiKey"] = self.cfg.api_key
            params["secret"] = self.cfg.api_secret
            if self.cfg.password:
                params["password"] = self.cfg.password

        exchange_cls = getattr(ccxt, self.cfg.exchange_id)
        self.client = exchange_cls(params)

        # Sandbox só ativa com chaves válidas; sem chaves usa API pública normal.
        if self.cfg.use_testnet and self.cfg.api_key and self.cfg.api_secret:
            try:
                self.client.set_sandbox_mode(True)
            except Exception as e:  # noqa: BLE001
                log.info("%s não tem modo sandbox: %s", self.cfg.exchange_id, e)

    # ---------- Retry ----------
    def _with_retry(self, label: str, fn: Callable[[], T]) -> T:
        attempt = 0
        while True:
            try:
                return fn()
            except _TRANSIENT as e:
                attempt += 1
                if attempt > self.cfg.max_retries:
                    raise
                wait = self.cfg.backoff_base_seconds * (2 ** (attempt - 1))
                log.warning(
                    "%s [%s]: erro transitório (%s). Retry %d/%d em %.1fs",
                    self.cfg.exchange_id, label, type(e).__name__,
                    attempt, self.cfg.max_retries, wait,
                )
                time.sleep(wait)

    # ---------- BrokerClient ----------
    def get_price(self, symbol: str) -> float:
        ticker = self._with_retry(
            "fetch_ticker", lambda: self.client.fetch_ticker(symbol)
        )
        return float(ticker["last"])

    def get_ohlcv(self, symbol: str, timeframe: str = "1m", limit: int = 200) -> pd.DataFrame:
        raw = self._with_retry(
            "fetch_ohlcv",
            lambda: self.client.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit),
        )
        df = pd.DataFrame(
            raw, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df

    def get_balance(self) -> dict:
        if not (self.cfg.api_key and self.cfg.api_secret):
            return {}
        return self._with_retry("fetch_balance", self.client.fetch_balance)

    def place_market_order(self, symbol: str, side: OrderSide, amount: float) -> dict:
        s = side.lower()
        if s not in ("buy", "sell"):
            raise ValueError("side deve ser 'buy' ou 'sell'")
        if not (self.cfg.api_key and self.cfg.api_secret):
            raise PermissionError(
                f"Tentou enviar ordem em {self.cfg.exchange_id} sem API keys."
            )
        return self.client.create_order(symbol, "market", s, amount)
