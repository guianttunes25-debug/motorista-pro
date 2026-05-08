"""Aprendizado adaptativo do Gatekeeper LLM.

Filosofia (mesma do LearningEngine): regras explícitas, ajustes limitados,
toda decisão é EXPLICÁVEL.

O que rastreamos
----------------
Para cada AÇÃO do Gatekeeper sobre uma BUY proposta:
- ``pass``    → LLM aprovou a heurística → trade abre → mede outcome.
- ``rescue``  → LLM resgatou setup que heurística rejeitou → trade abre (size
                 reduzido) → mede outcome.
- ``veto``    → LLM bloqueou BUY → SEM outcome (não dá pra saber se teria
                 vencido). Apenas contamos pra estatística.

Como ajustamos
--------------
- Se ``pass.win_rate`` é BAIXO (LLM concorda demais com setups ruins) →
  ABAIXAMOS ``min_confidence_to_veto`` (LLM passa a vetar com mais facilidade).
- Se ``pass.win_rate`` é ALTO (LLM está certo ao não vetar) → AUMENTAMOS
  ``min_confidence_to_veto`` (menos interferência da IA).
- Se ``rescue.win_rate`` é BAIXO (resgates costumam dar prejuízo) →
  AUMENTAMOS ``min_confidence_to_rescue`` (mais exigente para resgatar).
- Se ``rescue.win_rate`` é ALTO → ABAIXAMOS ``min_confidence_to_rescue``
  (libera mais resgates).

Cada ajuste é limitado por ``max_*_delta`` em pontos percentuais sobre o
baseline e SEMPRE gera entrada em ``last_adjustments`` (auditável).

Estado é persistido em ``data/gatekeeper_learning.json`` e carregado no
startup. Sobrevive a reinícios do app.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from threading import Lock
from typing import Literal

log = logging.getLogger("gatekeeper_learning")

GkAction = Literal["pass", "veto", "rescue", "skip"]


@dataclass
class GkActionStats:
    """Estatísticas agregadas de uma ação do Gatekeeper."""
    count: int = 0
    wins: int = 0
    total_pnl_pct: float = 0.0
    sum_confidence: int = 0

    @property
    def win_rate(self) -> float:
        return self.wins / self.count if self.count else 0.0

    @property
    def avg_pnl_pct(self) -> float:
        return self.total_pnl_pct / self.count if self.count else 0.0

    @property
    def avg_confidence(self) -> float:
        return self.sum_confidence / self.count if self.count else 0.0


@dataclass
class GkLearningState:
    """Estado persistido. Tudo que sobrevive entre execuções."""
    pass_stats: GkActionStats = field(default_factory=GkActionStats)
    rescue_stats: GkActionStats = field(default_factory=GkActionStats)
    veto_count: int = 0
    veto_sum_confidence: int = 0

    # Deltas aplicados sobre o baseline dos thresholds (em pontos percentuais).
    # Positivo = mais conservador. Negativo = mais agressivo.
    veto_threshold_delta: int = 0
    rescue_threshold_delta: int = 0

    last_adjustments: list[str] = field(default_factory=list)


@dataclass
class GkLearningConfig:
    persist_path: Path | None = None
    # Mínimo de outcomes registrados antes de tentar ajustar.
    min_samples_to_adjust: int = 5
    # Revisa thresholds a cada N novos outcomes.
    review_every_n: int = 1
    # Limite (em pontos %) de quanto o threshold pode se afastar do baseline.
    max_veto_delta: int = 20
    max_rescue_delta: int = 20
    # Limites duros (clamp) — segurança contra valores absurdos.
    veto_floor: int = 50
    veto_ceiling: int = 95
    rescue_floor: int = 55
    rescue_ceiling: int = 95
    # Win-rate alvos para ajustar.
    win_rate_low: float = 0.35
    win_rate_high: float = 0.55
    # Quantas mensagens de ajuste guardar no histórico.
    log_history_size: int = 50


class GatekeeperLearning:
    """Cérebro adaptativo do Gatekeeper. Thread-safe.

    Uso típico (no engine):
        gk_learn = GatekeeperLearning()
        gk_learn.apply_to_gatekeeper(self.gatekeeper, baseline)  # no startup
        # ... ao receber verdict:
        gk_learn.record_decision(verdict.action, verdict.confidence)
        # ... ao fechar trade originado de pass/rescue:
        gk_learn.record_outcome(action, win, pnl_pct, confidence)
    """

    def __init__(self, cfg: GkLearningConfig | None = None) -> None:
        self.cfg = cfg or GkLearningConfig()
        if self.cfg.persist_path is None:
            try:
                from core.paths import data_dir
                self.cfg.persist_path = data_dir() / "gatekeeper_learning.json"
            except Exception:  # noqa: BLE001
                self.cfg.persist_path = Path("data/gatekeeper_learning.json")
        self._lock = Lock()
        self.state = self._load()
        self._outcomes_since_review = 0

    # ---------- Persistência ----------
    def _load(self) -> GkLearningState:
        p = self.cfg.persist_path
        if not p.exists():
            return GkLearningState()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return GkLearningState(
                pass_stats=GkActionStats(**data.get("pass_stats", {})),
                rescue_stats=GkActionStats(**data.get("rescue_stats", {})),
                veto_count=int(data.get("veto_count", 0)),
                veto_sum_confidence=int(data.get("veto_sum_confidence", 0)),
                veto_threshold_delta=int(data.get("veto_threshold_delta", 0)),
                rescue_threshold_delta=int(data.get("rescue_threshold_delta", 0)),
                last_adjustments=list(data.get("last_adjustments", [])),
            )
        except Exception as e:  # noqa: BLE001
            log.warning("Falha ao carregar gatekeeper_learning state: %s — começando do zero", e)
            return GkLearningState()

    def _save(self) -> None:
        p = self.cfg.persist_path
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "pass_stats": asdict(self.state.pass_stats),
                "rescue_stats": asdict(self.state.rescue_stats),
                "veto_count": self.state.veto_count,
                "veto_sum_confidence": self.state.veto_sum_confidence,
                "veto_threshold_delta": self.state.veto_threshold_delta,
                "rescue_threshold_delta": self.state.rescue_threshold_delta,
                "last_adjustments": self.state.last_adjustments[-self.cfg.log_history_size:],
            }
            p.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            log.warning("Falha ao salvar gatekeeper_learning: %s", e)

    # ---------- API pública ----------
    def record_decision(self, action: GkAction, confidence: int) -> None:
        """Registra que o Gatekeeper tomou uma decisão.

        Para ``veto`` é o único lugar onde contamos (não tem outcome depois).
        Para ``pass`` / ``rescue`` o contador é incrementado em ``record_outcome``
        para evitar contagem dupla.
        """
        if action == "veto":
            with self._lock:
                self.state.veto_count += 1
                self.state.veto_sum_confidence += int(max(0, min(100, confidence)))
                self._save()

    def record_outcome(
        self,
        action: GkAction,
        win: bool,
        pnl_pct: float,
        confidence: int,
        baseline_veto: int | None = None,
        baseline_rescue: int | None = None,
        gatekeeper=None,  # opcional: aplica ajustes em runtime
    ) -> list[str]:
        """Registra resultado de um trade originado de uma decisão do Gatekeeper.

        Só faz sentido para ``pass`` e ``rescue``. Para outras ações é no-op.

        Retorna lista de mensagens de ajuste (vazia se nada mudou).
        """
        if action not in ("pass", "rescue"):
            return []
        with self._lock:
            stats = self.state.pass_stats if action == "pass" else self.state.rescue_stats
            stats.count += 1
            stats.total_pnl_pct += float(pnl_pct)
            stats.sum_confidence += int(max(0, min(100, confidence)))
            if win:
                stats.wins += 1

            self._outcomes_since_review += 1
            adjustments: list[str] = []
            total_outcomes = self.state.pass_stats.count + self.state.rescue_stats.count
            if (total_outcomes >= self.cfg.min_samples_to_adjust
                    and self._outcomes_since_review >= self.cfg.review_every_n):
                self._outcomes_since_review = 0
                adjustments = self._review(baseline_veto, baseline_rescue)
                if adjustments and gatekeeper is not None and baseline_veto is not None and baseline_rescue is not None:
                    self._apply(gatekeeper, baseline_veto, baseline_rescue)

            self._save()
            return adjustments

    def _review(self, baseline_veto: int | None, baseline_rescue: int | None) -> list[str]:
        """Heurística de ajuste — explicável e limitada. Chamado COM lock."""
        msgs: list[str] = []
        ps = self.state.pass_stats
        rs = self.state.rescue_stats

        # ---- veto threshold (depende de pass) ----
        if ps.count >= self.cfg.min_samples_to_adjust:
            wr = ps.win_rate
            old = self.state.veto_threshold_delta
            new = old
            if wr < self.cfg.win_rate_low:
                new = max(-self.cfg.max_veto_delta, old - 5)
            elif wr > self.cfg.win_rate_high:
                new = min(self.cfg.max_veto_delta, old + 5)
            if new != old:
                self.state.veto_threshold_delta = new
                base = baseline_veto if baseline_veto is not None else 70
                msgs.append(
                    f"veto_threshold {base + old}→{base + new} "
                    f"(pass win_rate={wr:.0%} em {ps.count} trades)"
                )

        # ---- rescue threshold (depende de rescue) ----
        if rs.count >= self.cfg.min_samples_to_adjust:
            wr = rs.win_rate
            old = self.state.rescue_threshold_delta
            new = old
            if wr < self.cfg.win_rate_low:
                new = min(self.cfg.max_rescue_delta, old + 5)
            elif wr > self.cfg.win_rate_high:
                new = max(-self.cfg.max_rescue_delta, old - 5)
            if new != old:
                self.state.rescue_threshold_delta = new
                base = baseline_rescue if baseline_rescue is not None else 75
                msgs.append(
                    f"rescue_threshold {base + old}→{base + new} "
                    f"(rescue win_rate={wr:.0%} em {rs.count} trades)"
                )

        if msgs:
            self.state.last_adjustments.extend(msgs)
        return msgs

    def apply_to_gatekeeper(self, gatekeeper, baseline_veto: int, baseline_rescue: int) -> None:
        """Aplica os deltas atuais sobre o baseline no Gatekeeper em runtime."""
        with self._lock:
            self._apply(gatekeeper, baseline_veto, baseline_rescue)

    def _apply(self, gatekeeper, baseline_veto: int, baseline_rescue: int) -> None:
        """Aplica deltas — chamado COM lock."""
        if gatekeeper is None or not hasattr(gatekeeper, "cfg"):
            return
        new_veto = max(self.cfg.veto_floor, min(self.cfg.veto_ceiling,
                       baseline_veto + self.state.veto_threshold_delta))
        new_rescue = max(self.cfg.rescue_floor, min(self.cfg.rescue_ceiling,
                         baseline_rescue + self.state.rescue_threshold_delta))
        gatekeeper.cfg.min_confidence_to_veto = new_veto
        gatekeeper.cfg.min_confidence_to_rescue = new_rescue

    def stats(self) -> dict:
        """Snapshot legível para UI/log."""
        with self._lock:
            return {
                "pass": {
                    "count": self.state.pass_stats.count,
                    "win_rate": round(self.state.pass_stats.win_rate, 3),
                    "avg_pnl_pct": round(self.state.pass_stats.avg_pnl_pct, 3),
                    "avg_confidence": round(self.state.pass_stats.avg_confidence, 1),
                },
                "rescue": {
                    "count": self.state.rescue_stats.count,
                    "win_rate": round(self.state.rescue_stats.win_rate, 3),
                    "avg_pnl_pct": round(self.state.rescue_stats.avg_pnl_pct, 3),
                    "avg_confidence": round(self.state.rescue_stats.avg_confidence, 1),
                },
                "veto": {
                    "count": self.state.veto_count,
                    "avg_confidence": round(
                        self.state.veto_sum_confidence / self.state.veto_count, 1
                    ) if self.state.veto_count else 0.0,
                },
                "veto_threshold_delta": self.state.veto_threshold_delta,
                "rescue_threshold_delta": self.state.rescue_threshold_delta,
                "last_adjustments": list(self.state.last_adjustments[-10:]),
            }
