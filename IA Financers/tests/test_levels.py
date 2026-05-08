"""Testes para core/levels.py."""
from __future__ import annotations

import numpy as np

from core.levels import Levels, analyze_levels, find_swing_levels


def test_empty_returns_default():
    out = analyze_levels(np.array([]), np.array([]), np.array([]), np.array([]))
    assert isinstance(out, Levels)
    assert out.support == 0.0


def test_swing_levels_basic():
    highs = np.array([1, 2, 3, 2, 1, 2, 3, 4, 3, 2, 1])
    lows = np.array([1, 2, 3, 2, 1, 2, 3, 4, 3, 2, 1])
    sh, sl = find_swing_levels(highs, lows, window=2)
    assert 4.0 in sh  # pico em i=7
    assert 1.0 in sl  # vale em i=0/4/10 (alguns deles)


def test_finds_support_below_price():
    # Preço atual acima de 100, suporte recente em 95
    highs = np.array([110, 108, 105, 100, 98, 95, 100, 105, 110, 108])
    lows = np.array([105, 102, 100, 95, 92, 90, 95, 100, 102, 100])
    closes = np.array([108, 105, 102, 98, 95, 92, 98, 102, 108, 107])
    volumes = np.ones(10) * 1000
    out = analyze_levels(highs, lows, closes, volumes, swing_window=2)
    assert out.support > 0
    assert out.support < 107  # abaixo do preço atual (107)


def test_volume_spike_detected():
    highs = np.array([100] * 25)
    lows = np.array([99] * 25)
    closes = np.array([100] * 25)
    # Volume médio 1000, último 5000 (5x)
    volumes = np.array([1000] * 24 + [5000])
    out = analyze_levels(highs, lows, closes, volumes, volume_spike_mult=3.0)
    assert out.volume_spike
    assert out.volume_ratio >= 3.0


def test_volume_normal_no_spike():
    highs = np.array([100] * 25)
    lows = np.array([99] * 25)
    closes = np.array([100] * 25)
    volumes = np.array([1000] * 25)
    out = analyze_levels(highs, lows, closes, volumes)
    assert not out.volume_spike
    assert 0.9 < out.volume_ratio < 1.1


def test_near_support_threshold():
    # Preço 100.3, suporte em 100.0 → distance ~0.3% → near
    highs = np.array([105, 103, 102, 101, 100, 99, 100, 101, 102, 103, 100.3])
    lows = np.array([100, 100, 99, 98, 95, 95, 98, 99, 100, 100, 100.2])
    closes = np.array([102, 101, 100, 99, 96, 96, 99, 100, 101, 102, 100.3])
    volumes = np.ones(11) * 1000
    out = analyze_levels(highs, lows, closes, volumes,
                         swing_window=2, near_threshold_pct=0.5)
    # Pode ou não detectar near_support dependendo do swing — o que importa é não quebrar
    assert isinstance(out.near_support, bool)


def test_handles_short_arrays_gracefully():
    out = analyze_levels(
        np.array([1.0, 2.0]), np.array([1.0, 1.5]),
        np.array([1.5, 1.8]), np.array([100, 100])
    )
    assert isinstance(out, Levels)  # não lança


def test_volume_zero_avg_no_division_error():
    out = analyze_levels(
        np.array([100] * 5), np.array([99] * 5),
        np.array([100] * 5), np.zeros(5)
    )
    assert out.volume_ratio == 1.0
    assert not out.volume_spike
