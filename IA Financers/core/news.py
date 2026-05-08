"""Sentimento de notícias - CryptoPanic (com token) + fallback RSS gratuito.

Estratégia de fonte:
    1. Se `cfg.token` configurado → CryptoPanic API (preciso, com votos).
    2. Senão → RSS público do CoinTelegraph (gratuito, sem chave).

Em ambos os casos:
    - Pontua palavras-chave positivas/negativas no título.
    - Retorna (score normalizado -2..+2, headlines).
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import requests

CRYPTOPANIC_URL = "https://cryptopanic.com/api/v1/posts/"
COINTELEGRAPH_RSS = "https://cointelegraph.com/rss"

log = logging.getLogger("news")

_POSITIVE = {
    "surge", "rally", "bull", "bullish", "rise", "soar", "gain", "up",
    "approve", "etf", "adoption", "high", "record", "breakout", "moon",
    "pump", "buy", "support", "alta", "subida", "aprovado",
}
_NEGATIVE = {
    "crash", "drop", "bear", "bearish", "fall", "ban", "hack", "exploit",
    "sell-off", "down", "dump", "scam", "fraud", "lawsuit", "fud",
    "rejected", "decline", "queda", "baixa", "rejeitado",
}


@dataclass
class NewsConfig:
    token: str = ""
    currencies: str = "BTC,ETH"
    enabled: bool = True
    use_free_fallback: bool = True


class NewsService:
    def __init__(self, cfg: NewsConfig) -> None:
        self.cfg = cfg

    def fetch_score(self, limit: int = 20) -> tuple[int, list[str]]:
        """Retorna (score, headlines). Score: -2..+2 aprox."""
        if not self.cfg.enabled:
            return 0, []

        if self.cfg.token:
            try:
                return self._fetch_cryptopanic(limit)
            except Exception as e:  # noqa: BLE001
                log.warning("CryptoPanic falhou (%s) — caindo no fallback gratuito", e)

        if self.cfg.use_free_fallback:
            try:
                return self._fetch_rss(limit)
            except Exception as e:  # noqa: BLE001
                log.warning("RSS fallback falhou: %s", e)
                return 0, []

        return 0, []

    def _fetch_cryptopanic(self, limit: int) -> tuple[int, list[str]]:
        params = {
            "auth_token": self.cfg.token,
            "currencies": self.cfg.currencies,
            "public": "true",
        }
        r = requests.get(CRYPTOPANIC_URL, params=params, timeout=8)
        r.raise_for_status()
        data = r.json().get("results", [])[:limit]

        score = 0
        headlines: list[str] = []
        for item in data:
            title = (item.get("title") or "").lower()
            headlines.append(item.get("title") or "")
            votes = item.get("votes") or {}
            score += int(votes.get("positive", 0)) - int(votes.get("negative", 0))
            score += _keyword_score(title)
        return _clamp(score), headlines[:5]

    def _fetch_rss(self, limit: int) -> tuple[int, list[str]]:
        """RSS do CoinTelegraph — sem chave, sem rate limit agressivo."""
        r = requests.get(
            COINTELEGRAPH_RSS,
            timeout=8,
            headers={"User-Agent": "AI-Trader-Copilot/1.0"},
        )
        r.raise_for_status()
        root = ET.fromstring(r.content)

        titles: list[str] = []
        for item in root.iter("item"):
            t = item.find("title")
            if t is not None and t.text:
                titles.append(t.text.strip())
            if len(titles) >= limit:
                break

        if self.cfg.currencies:
            tokens = [c.strip().upper() for c in self.cfg.currencies.split(",") if c.strip()]
            if tokens:
                pattern = re.compile(r"\b(" + "|".join(tokens) + r")\b", re.IGNORECASE)
                filtered = [t for t in titles if pattern.search(t)]
                titles = filtered or titles

        score = 0
        for t in titles:
            score += _keyword_score(t.lower())
        return _clamp(score), titles[:5]


def _keyword_score(title_lower: str) -> int:
    s = 0
    for w in _POSITIVE:
        if w in title_lower:
            s += 1
    for w in _NEGATIVE:
        if w in title_lower:
            s -= 1
    return s


def _clamp(score: int) -> int:
    if score > 2:
        return 2
    if score < -2:
        return -2
    return score
