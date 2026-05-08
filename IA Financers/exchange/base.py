"""Interface abstrata BrokerClient.

Qualquer corretora (cripto, ações, forex) que queira ser plugada no
robô deve implementar esta interface. O `EngineWorker`, `MarketDataService`
e `Backtester` dependem APENAS deste contrato — nunca de uma exchange
específica.

Métodos obrigatórios:
    - get_price(symbol)              → último preço (float)
    - get_ohlcv(symbol, tf, limit)   → DataFrame com colunas
                                       [timestamp, open, high, low, close, volume]
    - place_market_order(...)        → executa ordem real

Métodos opcionais (com default seguro):
    - get_balance()                  → dict do saldo
    - name                           → identificador da corretora

Implementações disponíveis:
    - exchange/binance.py        → Binance (cripto, ccxt, com testnet)
    - exchange/ccxt_broker.py    → genérico p/ qualquer exchange ccxt
                                   (Bybit, Kraken, Coinbase, OKX, KuCoin,
                                    Mercado Bitcoin, Foxbit, NovaDAX, etc.)

Quem quiser adicionar Alpaca, OANDA ou MT5 implementa esta interface.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

import pandas as pd

OrderSide = Literal["buy", "sell"]


class BrokerClient(ABC):
    """Contrato mínimo que todo broker deve implementar."""

    name: str = "broker"

    @abstractmethod
    def get_price(self, symbol: str) -> float:
        """Retorna o último preço negociado do `symbol`."""

    @abstractmethod
    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        limit: int = 200,
    ) -> pd.DataFrame:
        """Retorna candles em DataFrame.

        Colunas obrigatórias: timestamp, open, high, low, close, volume.
        """

    @abstractmethod
    def place_market_order(self, symbol: str, side: OrderSide, amount: float) -> dict:
        """Envia ordem a mercado. SOMENTE chamado em modo live."""

    # ---------- Opcionais (default seguro) ----------
    def get_balance(self) -> dict:
        """Saldo da conta. Default: vazio (sem keys)."""
        return {}

    def __repr__(self) -> str:  # pragma: no cover - cosmético
        return f"<{self.__class__.__name__} name={self.name!r}>"
