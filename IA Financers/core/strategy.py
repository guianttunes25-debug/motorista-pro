"""Strategy Engine — sistema de score multifator.

Cada indicador contribui com +1 (alta), -1 (baixa) ou 0 (neutro).
A soma é comparada a thresholds para gerar o sinal final.

Indicadores usados:
    - RSI (sobrecompra/sobrevenda)
    - Cruzamento SMA fast x slow
    - MACD vs Signal
    - Sentimento de notícias (opcional, vindo de core/news.py)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from core.market import MarketSnapshot

Signal = Literal["BUY", "SELL", "HOLD"]


@dataclass
class StrategyConfig:
    rsi_buy: float = 30.0
    rsi_sell: float = 70.0
    score_buy_threshold: int = 2
    score_sell_threshold: int = -2


@dataclass
class Decision:
    signal: Signal
    score: int
    reasons: list[str]
    snapshot: MarketSnapshot


class ScoringStrategy:
    def __init__(self, cfg: StrategyConfig | None = None) -> None:
        self.cfg = cfg or StrategyConfig()

    def decide(self, snap: MarketSnapshot, news_score: int = 0) -> Decision:
        score = 0
        reasons: list[str] = []

        # ---- RSI ----
        if snap.rsi < self.cfg.rsi_buy:
            score += 1
            reasons.append(f"RSI {snap.rsi:.1f} < {self.cfg.rsi_buy} (sobrevenda)")
        elif snap.rsi > self.cfg.rsi_sell:
            score -= 1
            reasons.append(f"RSI {snap.rsi:.1f} > {self.cfg.rsi_sell} (sobrecompra)")

        # ---- SMA cross ----
        if snap.sma_fast > snap.sma_slow:
            score += 1
            reasons.append("SMA rápida acima da lenta (tendência alta)")
        elif snap.sma_fast < snap.sma_slow:
            score -= 1
            reasons.append("SMA rápida abaixo da lenta (tendência baixa)")

        # ---- MACD ----
        if snap.macd > snap.macd_signal and snap.macd_hist > 0:
            score += 1
            reasons.append("MACD acima do signal (momentum alta)")
        elif snap.macd < snap.macd_signal and snap.macd_hist < 0:
            score -= 1
            reasons.append("MACD abaixo do signal (momentum baixa)")

        # ---- Notícias ----
        if news_score > 0:
            score += 1
            reasons.append(f"Sentimento notícias positivo (+{news_score})")
        elif news_score < 0:
            score -= 1
            reasons.append(f"Sentimento notícias negativo ({news_score})")

        if score >= self.cfg.score_buy_threshold:
            sig: Signal = "BUY"
        elif score <= self.cfg.score_sell_threshold:
            sig = "SELL"
        else:
            sig = "HOLD"

        return Decision(signal=sig, score=score, reasons=reasons, snapshot=snap)


# Função simples (compatibilidade com o snippet do plano)
def simple_strategy(rsi: float) -> Signal:
    if rsi < 30:
        return "BUY"
    if rsi > 70:
        return "SELL"
    return "HOLD"
