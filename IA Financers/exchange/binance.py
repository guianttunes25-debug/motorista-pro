"""Wrapper Binance via ccxt.

- Modo público (sem chaves) para preço e candles.
- Sandbox/Testnet quando `use_testnet=True` e há chaves.
- Ordens reais SOMENTE via `place_market_order` (chamado apenas em modo live).
- Retry com backoff exponencial em falhas transitórias de rede / rate limit.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional, TypeVar

import ccxt
import pandas as pd

from exchange.base import BrokerClient, OrderSide

T = TypeVar("T")
log = logging.getLogger("exchange.binance")

# Erros considerados transitórios (vale tentar de novo)
_TRANSIENT = (
    ccxt.NetworkError,
    ccxt.RequestTimeout,
    ccxt.DDoSProtection,
    ccxt.ExchangeNotAvailable,
    ccxt.RateLimitExceeded,
)


@dataclass
class ExchangeConfig:
    api_key: str = ""
    api_secret: str = ""
    use_testnet: bool = True
    max_retries: int = 4
    backoff_base_seconds: float = 0.8


class BinanceClient(BrokerClient):
    name = "binance"

    def __init__(self, cfg: Optional[ExchangeConfig] = None) -> None:
        self.cfg = cfg or ExchangeConfig()
        params = {"enableRateLimit": True, "timeout": 15000}
        if self.cfg.api_key and self.cfg.api_secret:
            params["apiKey"] = self.cfg.api_key
            params["secret"] = self.cfg.api_secret
        self.client = ccxt.binance(params)
        # Sandbox só faz sentido (e só funciona) se houver chaves válidas.
        # Sem chaves em simulation, usa API pública normal para preços.
        if self.cfg.use_testnet and self.cfg.api_key and self.cfg.api_secret:
            try:
                self.client.set_sandbox_mode(True)
            except Exception:
                pass

    # ---------- Retry helper ----------
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
                log.warning("%s: erro transitório (%s). Retry %d/%d em %.1fs",
                            label, type(e).__name__, attempt, self.cfg.max_retries, wait)
                time.sleep(wait)

    # ---------- Mercado ----------
    def get_price(self, symbol: str = "BTC/USDT") -> float:
        ticker = self._with_retry("fetch_ticker", lambda: self.client.fetch_ticker(symbol))
        return float(ticker["last"])

    def get_ohlcv(
        self,
        symbol: str = "BTC/USDT",
        timeframe: str = "1m",
        limit: int = 200,
    ) -> pd.DataFrame:
        raw = self._with_retry(
            "fetch_ohlcv",
            lambda: self.client.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit),
        )
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df

    def get_balance(self) -> dict:
        # Sem chaves = modo simulation sem testnet; retorna vazio (engine usa saldo simulado).
        if not (self.cfg.api_key and self.cfg.api_secret):
            return {}
        return self._with_retry("fetch_balance", self.client.fetch_balance)

    # ---------- Ordens reais ----------
    def place_market_order(self, symbol: str, side: OrderSide, amount: float) -> dict:
        s = side.lower()
        if s not in ("buy", "sell"):
            raise ValueError("side deve ser 'buy' ou 'sell'")
        if not (self.cfg.api_key and self.cfg.api_secret):
            raise PermissionError("Tentou enviar ordem na Binance sem API keys.")
        return self.client.create_order(symbol, "market", s, amount)


# API funcional simples (compatível com o esboço do plano)
_default = BinanceClient()


def get_price(symbol: str = "BTC/USDT") -> float:
    return _default.get_price(symbol)


if __name__ == "__main__":
    print("Preço BTC/USDT:", get_price())
