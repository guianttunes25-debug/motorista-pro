"""Testes do GatekeeperLearning — aprendizado adaptativo do Gatekeeper LLM."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.gatekeeper_learning import (
    GatekeeperLearning,
    GkLearningConfig,
    GkActionStats,
)


class _FakeGkCfg:
    def __init__(self, veto: int = 70, rescue: int = 75) -> None:
        self.min_confidence_to_veto = veto
        self.min_confidence_to_rescue = rescue


class _FakeGatekeeper:
    def __init__(self, veto: int = 70, rescue: int = 75) -> None:
        self.cfg = _FakeGkCfg(veto, rescue)


def _new(tmp_path: Path, **kw) -> GatekeeperLearning:
    cfg = GkLearningConfig(
        persist_path=tmp_path / "gk.json",
        min_samples_to_adjust=kw.pop("min_samples", 3),
        review_every_n=kw.pop("review_every", 1),
        max_veto_delta=kw.pop("max_veto_delta", 20),
        max_rescue_delta=kw.pop("max_rescue_delta", 20),
        win_rate_low=kw.pop("wr_low", 0.35),
        win_rate_high=kw.pop("wr_high", 0.55),
    )
    return GatekeeperLearning(cfg)


def test_record_decision_veto_only_increments_veto_count(tmp_path: Path) -> None:
    gk = _new(tmp_path)
    gk.record_decision("veto", 80)
    gk.record_decision("veto", 60)
    gk.record_decision("pass", 90)  # no-op aqui
    assert gk.state.veto_count == 2
    assert gk.state.veto_sum_confidence == 140
    assert gk.state.pass_stats.count == 0


def test_record_outcome_pass_loses_lowers_veto_threshold(tmp_path: Path) -> None:
    """Pass com win_rate baixo → abaixa min_confidence_to_veto (LLM bloqueia mais)."""
    gk = _new(tmp_path, min_samples=3, review_every=1)
    fake = _FakeGatekeeper(veto=70, rescue=75)
    # 4 trades de pass, todos perdas → win_rate = 0%
    for _ in range(4):
        gk.record_outcome(
            "pass", win=False, pnl_pct=-1.0, confidence=80,
            baseline_veto=70, baseline_rescue=75, gatekeeper=fake,
        )
    assert gk.state.veto_threshold_delta < 0
    assert fake.cfg.min_confidence_to_veto < 70
    assert gk.state.pass_stats.count == 4


def test_record_outcome_pass_wins_raises_veto_threshold(tmp_path: Path) -> None:
    gk = _new(tmp_path, min_samples=3, review_every=1)
    fake = _FakeGatekeeper(veto=70, rescue=75)
    for _ in range(4):
        gk.record_outcome(
            "pass", win=True, pnl_pct=1.0, confidence=80,
            baseline_veto=70, baseline_rescue=75, gatekeeper=fake,
        )
    assert gk.state.veto_threshold_delta > 0
    assert fake.cfg.min_confidence_to_veto > 70


def test_record_outcome_rescue_loses_raises_rescue_threshold(tmp_path: Path) -> None:
    """Rescue com win_rate baixo → AUMENTA min_confidence_to_rescue (mais exigente)."""
    gk = _new(tmp_path, min_samples=3, review_every=1)
    fake = _FakeGatekeeper(veto=70, rescue=75)
    for _ in range(4):
        gk.record_outcome(
            "rescue", win=False, pnl_pct=-2.0, confidence=85,
            baseline_veto=70, baseline_rescue=75, gatekeeper=fake,
        )
    assert gk.state.rescue_threshold_delta > 0
    assert fake.cfg.min_confidence_to_rescue > 75


def test_record_outcome_rescue_wins_lowers_rescue_threshold(tmp_path: Path) -> None:
    gk = _new(tmp_path, min_samples=3, review_every=1)
    fake = _FakeGatekeeper(veto=70, rescue=75)
    for _ in range(4):
        gk.record_outcome(
            "rescue", win=True, pnl_pct=2.0, confidence=85,
            baseline_veto=70, baseline_rescue=75, gatekeeper=fake,
        )
    assert gk.state.rescue_threshold_delta < 0
    assert fake.cfg.min_confidence_to_rescue < 75


def test_outcome_skip_or_veto_is_noop(tmp_path: Path) -> None:
    gk = _new(tmp_path)
    msgs = gk.record_outcome("veto", win=True, pnl_pct=1.0, confidence=80)
    assert msgs == []
    assert gk.state.pass_stats.count == 0
    assert gk.state.rescue_stats.count == 0


def test_persist_and_reload(tmp_path: Path) -> None:
    """Estado sobrevive a reinício (carregamento do JSON)."""
    cfg = GkLearningConfig(persist_path=tmp_path / "gk.json", min_samples_to_adjust=3)
    gk = GatekeeperLearning(cfg)
    fake = _FakeGatekeeper()
    for _ in range(4):
        gk.record_outcome(
            "pass", win=False, pnl_pct=-1.0, confidence=80,
            baseline_veto=70, baseline_rescue=75, gatekeeper=fake,
        )
    delta = gk.state.veto_threshold_delta
    assert delta < 0
    assert (tmp_path / "gk.json").exists()

    # Recarrega
    gk2 = GatekeeperLearning(cfg)
    assert gk2.state.veto_threshold_delta == delta
    assert gk2.state.pass_stats.count == 4

    # apply_to_gatekeeper aplica os deltas persistidos no novo gatekeeper
    fake2 = _FakeGatekeeper(veto=70, rescue=75)
    gk2.apply_to_gatekeeper(fake2, baseline_veto=70, baseline_rescue=75)
    assert fake2.cfg.min_confidence_to_veto == 70 + delta


def test_threshold_clamped_to_floor_ceiling(tmp_path: Path) -> None:
    """Mesmo com deltas grandes, thresholds não passam dos limites duros."""
    gk = _new(tmp_path, min_samples=3, max_veto_delta=200)
    gk.state.veto_threshold_delta = -200  # absurdo intencional
    fake = _FakeGatekeeper(veto=70, rescue=75)
    gk.apply_to_gatekeeper(fake, baseline_veto=70, baseline_rescue=75)
    # Floor para veto = 50
    assert fake.cfg.min_confidence_to_veto == 50

    gk.state.rescue_threshold_delta = 200
    gk.apply_to_gatekeeper(fake, baseline_veto=70, baseline_rescue=75)
    # Ceiling para rescue = 95
    assert fake.cfg.min_confidence_to_rescue == 95


def test_min_samples_required_before_adjustment(tmp_path: Path) -> None:
    """Não ajusta antes de min_samples_to_adjust outcomes."""
    gk = _new(tmp_path, min_samples=10, review_every=1)
    fake = _FakeGatekeeper()
    for _ in range(5):  # menos que min_samples
        gk.record_outcome(
            "pass", win=False, pnl_pct=-1.0, confidence=80,
            baseline_veto=70, baseline_rescue=75, gatekeeper=fake,
        )
    assert gk.state.veto_threshold_delta == 0
    assert fake.cfg.min_confidence_to_veto == 70


def test_stats_snapshot(tmp_path: Path) -> None:
    gk = _new(tmp_path)
    gk.record_decision("veto", 80)
    gk.record_outcome(
        "pass", win=True, pnl_pct=1.5, confidence=70,
        baseline_veto=70, baseline_rescue=75,
    )
    s = gk.stats()
    assert s["pass"]["count"] == 1
    assert s["pass"]["win_rate"] == 1.0
    assert s["veto"]["count"] == 1
    assert "veto_threshold_delta" in s
    assert "last_adjustments" in s
