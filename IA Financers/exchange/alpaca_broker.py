"""Adapter Alpaca — paper/live trading de ações USA + cripto.

Usa a SDK oficial `alpaca-py` (https://github.com/alpacahq/alpaca-py).
Instalação: `pip install alpaca-py`.

Alpaca oferece conta PAPER 100% gratuita com dinheiro fake e dados reais —
ideal para testar estratégias antes de ir pra live.

Uso:
    >>> from exchange.alpaca_broker import AlpacaBroker, AlpacaConfig
    >>> b = AlpacaBroker(AlpacaConfig(api_key="...", api_secret="...", paper=True))
    >>> b.get_price("AAPL")          # ações
    >>> b.get_price("BTC/USD")       # cripto

Símbolos:
    - Ações: "AAPL", "MSFT", "TSLA"...
    - Cripto: "BTC/USD", "ETH/USD"...

⚠ A SDK alpaca-py NÃO é importada no topo do módulo — só é carregada quando
o broker é realmente instanciado. Assim quem não usa Alpaca não precisa
instalar o pacote.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd

from exchange.base import BrokerClient, OrderSide

log = logging.getLogger("exchange.alpaca")


# Mapeamento timeframe-string → (TimeFrameUnit, amount). Lazy-resolved.
_TF_MAP = {
    "1m": (1, "Minute"),
    "5m": (5, "Minute"),
    "15m": (15, "Minute"),
    "30m": (30, "Minute"),
    "1h": (1, "Hour"),
    "4h": (4, "Hour"),
    "1d": (1, "Day"),
}


@dataclass
class AlpacaConfig:
    api_key: str = ""
    api_secret: str = ""
    paper: bool = True               # True = paper trading (fake $), False = real
    feed: str = "iex"                # "iex" (grátis) ou "sip" (paga, mais dados)


def _require_alpaca_py():
    """Importa alpaca-py preguiçosamente. Erro amigável se faltar."""
    try:
        from alpaca.data.historical import (  # type: ignore
            CryptoHistoricalDataClient,
            StockHistoricalDataClient,
        )
        from alpaca.data.requests import (  # type: ignore
            CryptoBarsRequest,
            CryptoLatestQuoteRequest,
            StockBarsRequest,
            StockLatestQuoteRequest,
        )
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit  # type: ignore
        from alpaca.trading.client import TradingClient  # type: ignore
        from alpaca.trading.enums import OrderSide as AlpacaOrderSide  # type: ignore
        from alpaca.trading.enums import TimeInForce  # type: ignore
        from alpaca.trading.requests import MarketOrderRequest  # type: ignore
    except ImportError as e:
        raise ImportError(
            "Pacote `alpaca-py` não está instalado. "
            "Rode: pip install alpaca-py"
        ) from e
    return {
        "TradingClient": TradingClient,
        "StockHistoricalDataClient": StockHistoricalDataClient,
        "CryptoHistoricalDataClient": CryptoHistoricalDataClient,
        "StockBarsRequest": StockBarsRequest,
        "CryptoBarsRequest": CryptoBarsRequest,
        "StockLatestQuoteRequest": StockLatestQuoteRequest,
        "CryptoLatestQuoteRequest": CryptoLatestQuoteRequest,
        "MarketOrderRequest": MarketOrderRequest,
        "AlpacaOrderSide": AlpacaOrderSide,
        "TimeInForce": TimeInForce,
        "TimeFrame": TimeFrame,
        "TimeFrameUnit": TimeFrameUnit,
    }


def _is_crypto(symbol: str) -> bool:
    return "/" in symbol


class AlpacaBroker(BrokerClient):
    """Broker para Alpaca (ações + cripto USA)."""

    name = "alpaca"

    def __init__(self, cfg: AlpacaConfig | None = None) -> None:
        self.cfg = cfg or AlpacaConfig()
        self._sdk = _require_alpaca_py()

        # Trading client (precisa de keys)
        if self.cfg.api_key and self.cfg.api_secret:
            self._trading = self._sdk["TradingClient"](
                self.cfg.api_key, self.cfg.api_secret, paper=self.cfg.paper
            )
        else:
            self._trading = None

        # Data clients funcionam sem keys p/ alguns endpoints, mas com keys
        # têm rate limit muito melhor.
        self._stock_data = self._sdk["StockHistoricalDataClient"](
            self.cfg.api_key or None, self.cfg.api_secret or None
        )
        self._crypto_data = self._sdk["CryptoHistoricalDataClient"](
            self.cfg.api_key or None, self.cfg.api_secret or None
        )

    # ---------- Mercado ----------
    def get_price(self, symbol: str) -> float:
        if _is_crypto(symbol):
            req = self._sdk["CryptoLatestQuoteRequest"](symbol_or_symbols=symbol)
            quotes = self._crypto_data.get_crypto_latest_quote(req)
        else:
            req = self._sdk["StockLatestQuoteRequest"](
                symbol_or_symbols=symbol, feed=self.cfg.feed
            )
            quotes = self._stock_data.get_stock_latest_quote(req)
        q = quotes[symbol]
        # Mid price quando disponível, senão ask, senão bid
        ask = float(getattr(q, "ask_price", 0) or 0)
        bid = float(getattr(q, "bid_price", 0) or 0)
        if ask and bid:
            return (ask + bid) / 2.0
        return ask or bid

    def _resolve_timeframe(self, timeframe: str):
        amount, unit_name = _TF_MAP.get(timeframe, (1, "Minute"))
        unit = getattr(self._sdk["TimeFrameUnit"], unit_name)
        return self._sdk["TimeFrame"](amount, unit)

    def get_ohlcv(self, symbol: str, timeframe: str = "1m", limit: int = 200) -> pd.DataFrame:
        tf = self._resolve_timeframe(timeframe)

        # Janela aproximada baseada no timeframe
        minutes_map = {"1m": 1, "5m": 5, "15m": 15, "30m": 30,
                       "1h": 60, "4h": 240, "1d": 1440}
        minutes = minutes_map.get(timeframe, 1)
        start = datetime.now(timezone.utc) - timedelta(minutes=minutes * (limit + 5))

        if _is_crypto(symbol):
            req = self._sdk["CryptoBarsRequest"](
                symbol_or_symbols=symbol, timeframe=tf, start=start, limit=limit,
            )
            bars = self._crypto_data.get_crypto_bars(req)
        else:
            req = self._sdk["StockBarsRequest"](
                symbol_or_symbols=symbol, timeframe=tf, start=start, limit=limit,
                feed=self.cfg.feed,
            )
            bars = self._stock_data.get_stock_bars(req)

        df = bars.df
        if df is None or df.empty:
            return pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            )
        # alpaca-py devolve MultiIndex (symbol, timestamp). Resetamos para flat.
        df = df.reset_index()
        # Garante a coluna timestamp
        if "timestamp" not in df.columns and "time" in df.columns:
            df = df.rename(columns={"time": "timestamp"})
        cols = ["timestamp", "open", "high", "low", "close", "volume"]
        out = df[[c for c in cols if c in df.columns]].copy()
        out["timestamp"] = pd.to_datetime(out["timestamp"])
        return out.tail(limit).reset_index(drop=True)

    def get_balance(self) -> dict:
        if not self._trading:
            return {}
        acct = self._trading.get_account()
        return {
            "cash": float(acct.cash),
            "equity": float(acct.equity),
            "buying_power": float(acct.buying_power),
            "currency": acct.currency,
        }

    # ---------- Ordens reais ----------
    def place_market_order(self, symbol: str, side: OrderSide, amount: float) -> dict:
        s = side.lower()
        if s not in ("buy", "sell"):
            raise ValueError("side deve ser 'buy' ou 'sell'")
        if not self._trading:
            raise PermissionError("Tentou enviar ordem na Alpaca sem API keys.")
        AlpacaOrderSide = self._sdk["AlpacaOrderSide"]
        TimeInForce = self._sdk["TimeInForce"]
        MarketOrderRequest = self._sdk["MarketOrderRequest"]

        req = MarketOrderRequest(
            symbol=symbol,
            qty=amount,
            side=AlpacaOrderSide.BUY if s == "buy" else AlpacaOrderSide.SELL,
            time_in_force=TimeInForce.GTC if _is_crypto(symbol) else TimeInForce.DAY,
        )
        order = self._trading.submit_order(req)
        return {
            "id": str(order.id),
            "symbol": order.symbol,
            "side": str(order.side),
            "qty": float(order.qty or 0),
            "status": str(order.status),
        }
