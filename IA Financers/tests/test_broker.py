"""Testes da camada de broker abstrato + factory + CCXTBroker."""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pandas as pd
import pytest

from exchange.base import BrokerClient
from exchange.binance import BinanceClient
from exchange.factory import build_broker
from exchange.ccxt_broker import CCXTBroker, CCXTConfig


# ---------- BrokerClient como contrato ----------
def test_binance_implementa_brokerclient():
    assert isinstance(BinanceClient(), BrokerClient)


def test_ccxt_broker_implementa_brokerclient():
    b = CCXTBroker(CCXTConfig(exchange_id="binance", use_testnet=False))
    assert isinstance(b, BrokerClient)
    assert b.name == "binance"


def test_ccxt_broker_rejeita_exchange_inexistente():
    with pytest.raises(ValueError, match="não suportada"):
        CCXTBroker(CCXTConfig(exchange_id="exchange_que_nao_existe_xyz"))


def test_ccxt_broker_aceita_outras_exchanges():
    """Garante que múltiplas exchanges populares podem ser instanciadas."""
    for ex in ("bybit", "kraken", "kucoin", "okx", "mercado", "foxbit"):
        b = CCXTBroker(CCXTConfig(exchange_id=ex, use_testnet=False))
        assert b.name == ex


# ---------- Ordens sem keys devem ser bloqueadas ----------
def test_binance_bloqueia_ordem_sem_keys():
    c = BinanceClient()
    with pytest.raises(PermissionError):
        c.place_market_order("BTC/USDT", "buy", 0.001)


def test_ccxt_bloqueia_ordem_sem_keys():
    c = CCXTBroker(CCXTConfig(exchange_id="binance", use_testnet=False))
    with pytest.raises(PermissionError):
        c.place_market_order("BTC/USDT", "sell", 0.001)


def test_side_invalido_levanta():
    c = BinanceClient()
    with pytest.raises(ValueError):
        c.place_market_order("BTC/USDT", "long", 0.001)


# ---------- Factory ----------
def test_factory_default_binance():
    b = build_broker({})
    assert isinstance(b, BinanceClient)


def test_factory_explicit_binance():
    b = build_broker({"broker": {"type": "binance", "use_testnet": True}})
    assert isinstance(b, BinanceClient)


def test_factory_ccxt_bybit():
    b = build_broker({
        "broker": {"type": "ccxt", "exchange_id": "bybit", "use_testnet": False}
    })
    assert isinstance(b, CCXTBroker)
    assert b.name == "bybit"


def test_factory_tipo_invalido():
    with pytest.raises(ValueError, match="desconhecido"):
        build_broker({"broker": {"type": "metatrader_xpto"}})


def test_factory_alpaca_sem_lib_falha_com_msg_amigavel():
    """Se alpaca-py não está instalado, factory deve dar erro amigável."""
    try:
        import alpaca  # noqa: F401
        pytest.skip("alpaca-py instalado — não dá pra testar caminho de erro")
    except ImportError:
        pass
    with pytest.raises(ImportError, match="alpaca-py"):
        build_broker({"broker": {"type": "alpaca"}})


def test_factory_legado_funciona():
    """Configs antigos sem bloco 'broker' continuam funcionando."""
    b = build_broker({"api_key": "x", "api_secret": "y", "use_testnet": True})
    assert isinstance(b, BinanceClient)


# ---------- get_ohlcv normaliza colunas ----------
def test_ccxt_get_ohlcv_formato_dataframe():
    """Mocka o client interno e verifica colunas/timestamp."""
    b = CCXTBroker(CCXTConfig(exchange_id="binance", use_testnet=False))
    fake_raw = [
        [1700000000000, 100.0, 110.0, 95.0, 105.0, 1.5],
        [1700000060000, 105.0, 115.0, 100.0, 112.0, 2.0],
    ]
    b.client.fetch_ohlcv = MagicMock(return_value=fake_raw)
    df = b.get_ohlcv("BTC/USDT", "1m", 2)
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert len(df) == 2
    assert pd.api.types.is_datetime64_any_dtype(df["timestamp"])


def test_ccxt_get_price_extrai_last():
    b = CCXTBroker(CCXTConfig(exchange_id="binance", use_testnet=False))
    b.client.fetch_ticker = MagicMock(return_value={"last": 67450.5})
    assert b.get_price("BTC/USDT") == 67450.5


# ---------- Balance vazio sem keys ----------
def test_get_balance_sem_keys_retorna_vazio():
    b = CCXTBroker(CCXTConfig(exchange_id="binance", use_testnet=False))
    assert b.get_balance() == {}
