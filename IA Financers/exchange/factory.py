"""Factory de brokers — escolhe a implementação a partir do config.

Bloco esperado em config.json:

    "broker": {
        "type": "binance",                  // ou "ccxt", "alpaca"
        "exchange_id": "binance",           // só p/ type=ccxt (bybit, kraken, mercado, foxbit...)
        "api_key": "",
        "api_secret": "",
        "password": "",                     // só p/ algumas exchanges (kucoin, okx)
        "use_testnet": true,                // Alpaca: equivale a paper=true
        "feed": "iex"                       // só p/ type=alpaca ("iex" grátis, "sip" pago)
    }

Se o bloco "broker" não existe, faz fallback para os campos legados
(`api_key`, `api_secret`, `use_testnet` no nível raiz) e usa Binance.
"""
from __future__ import annotations

import logging

from exchange.base import BrokerClient
from exchange.binance import BinanceClient, ExchangeConfig
from exchange.ccxt_broker import CCXTBroker, CCXTConfig

log = logging.getLogger("exchange.factory")


def build_broker(cfg: dict) -> BrokerClient:
    """Cria o broker apropriado a partir do dicionário de config raiz."""
    block = cfg.get("broker") or {}
    btype = (block.get("type") or "binance").lower()

    api_key = block.get("api_key") or cfg.get("api_key", "")
    api_secret = block.get("api_secret") or cfg.get("api_secret", "")
    use_testnet = bool(block.get("use_testnet", cfg.get("use_testnet", True)))

    if btype == "binance":
        log.info("Broker: Binance (testnet=%s)", use_testnet)
        return BinanceClient(ExchangeConfig(
            api_key=api_key,
            api_secret=api_secret,
            use_testnet=use_testnet,
        ))

    if btype == "ccxt":
        exchange_id = (block.get("exchange_id") or "binance").lower()
        log.info("Broker: CCXT/%s (testnet=%s)", exchange_id, use_testnet)
        return CCXTBroker(CCXTConfig(
            exchange_id=exchange_id,
            api_key=api_key,
            api_secret=api_secret,
            password=block.get("password", ""),
            use_testnet=use_testnet,
        ))

    if btype == "alpaca":
        # Import preguiçoso — só carrega alpaca-py se realmente solicitado.
        from exchange.alpaca_broker import AlpacaBroker, AlpacaConfig
        # Em Alpaca, "use_testnet" mapeia para "paper".
        paper = bool(block.get("paper", use_testnet))
        log.info("Broker: Alpaca (paper=%s)", paper)
        return AlpacaBroker(AlpacaConfig(
            api_key=api_key,
            api_secret=api_secret,
            paper=paper,
            feed=block.get("feed", "iex"),
        ))

    raise ValueError(
        f"Tipo de broker desconhecido: '{btype}'. Use 'binance', 'ccxt' ou 'alpaca'."
    )
