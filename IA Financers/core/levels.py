"""Detecção de Suporte/Resistência e anomalias de volume.

Módulo standalone — recebe OHLCV e retorna níveis e flags. NÃO faz I/O.
Pensado pra ser barato (chamado a cada tick) e robusto.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Levels:
    """Resultado da análise de níveis e volume."""
    support: float = 0.0          # nível de suporte mais próximo (abaixo do preço)
    resistance: float = 0.0       # nível de resistência mais próximo (acima)
    distance_to_support_pct: float = 0.0    # % de distância pro suporte
    distance_to_resistance_pct: float = 0.0
    near_support: bool = False    # True se preço < 0.5% do suporte (zona de compra)
    near_resistance: bool = False # True se preço < 0.5% da resistência (zona de venda)
    volume_spike: bool = False    # volume atual > 3x média
    volume_ratio: float = 1.0     # quanto o volume atual é vs média


def find_swing_levels(
    highs: np.ndarray,
    lows: np.ndarray,
    window: int = 3,
) -> tuple[list[float], list[float]]:
    """Identifica swing highs (resistências) e swing lows (suportes).

    Um swing high é um candle com máxima maior que `window` candles à direita E esquerda.
    Análogo pro swing low. Quanto maior o `window`, mais "fortes" os níveis.
    """
    if len(highs) < (2 * window + 1):
        return [], []

    swing_highs = []
    swing_lows = []
    for i in range(window, len(highs) - window):
        h = highs[i]
        l = lows[i]
        is_high = all(h >= highs[i - k] for k in range(1, window + 1)) and \
                  all(h >= highs[i + k] for k in range(1, window + 1))
        is_low = all(l <= lows[i - k] for k in range(1, window + 1)) and \
                 all(l <= lows[i + k] for k in range(1, window + 1))
        if is_high:
            swing_highs.append(float(h))
        if is_low:
            swing_lows.append(float(l))
    return swing_highs, swing_lows


def analyze_levels(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    *,
    swing_window: int = 3,
    near_threshold_pct: float = 0.5,
    volume_spike_mult: float = 3.0,
    vol_lookback: int = 20,
) -> Levels:
    """Análise completa: S/R + anomalia de volume. Tolerante a arrays curtos."""
    out = Levels()
    if len(closes) == 0:
        return out

    price = float(closes[-1])
    if price <= 0:
        return out

    # ---------- S/R via swings ----------
    swing_highs, swing_lows = find_swing_levels(highs, lows, window=swing_window)
    # Suporte = maior swing low ABAIXO do preço atual
    sups_below = [s for s in swing_lows if s < price]
    if sups_below:
        out.support = max(sups_below)
        out.distance_to_support_pct = (price - out.support) / price * 100.0
        out.near_support = out.distance_to_support_pct <= near_threshold_pct
    # Resistência = menor swing high ACIMA do preço atual
    res_above = [r for r in swing_highs if r > price]
    if res_above:
        out.resistance = min(res_above)
        out.distance_to_resistance_pct = (out.resistance - price) / price * 100.0
        out.near_resistance = out.distance_to_resistance_pct <= near_threshold_pct

    # ---------- Anomalia de volume ----------
    if len(volumes) >= 2:
        recent = volumes[-1]
        # Média dos últimos vol_lookback (excluindo o atual)
        lookback = min(vol_lookback, len(volumes) - 1)
        if lookback > 0:
            avg = float(np.mean(volumes[-(lookback + 1):-1]))
            if avg > 0:
                out.volume_ratio = float(recent) / avg
                out.volume_spike = out.volume_ratio >= volume_spike_mult

    return out
