"""LearningEngine — aprendizado adaptativo por reforço heurístico.

Filosofia: trader sênior NÃO usa rede neural caixa-preta. Ele aprende
ajustando regras explícitas:
    - Acertou seguidas vezes em TRENDING_UP com sentimento bullish?
        → reduz min_setup_quality nesse contexto (mais agressivo).
    - Tomou stop seguidos em RANGING?
        → aumenta min_setup_quality (mais exigente).
    - Os SL estão muito apertados (atinge SL e depois sobe)?
        → afrouxa stop_loss_pct levemente.
    - TP raramente é atingido?
        → reduz take_profit_pct (mais realista).

Cada ajuste é LIMITADO (nunca foge muito do baseline) e EXPLICÁVEL
(toda mudança gera log). Estado persistido em data/learning.json.

Aplicação:
    engine fecha posição → learning.record_trade(outcome)
    learning.apply_to_agent(agent) → atualiza AgentConfig em runtime
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from threading import Lock
from typing import Literal

log = logging.getLogger("learning")

ExitReason = Literal["TP", "SL", "SELL_signal", "EOD", "manual"]


@dataclass
class TradeOutcome:
    """Resultado consolidado de um trade fechado."""
    pnl: float
    pnl_pct: float
    win: bool
    regime: str                  # TRENDING_UP, RANGING, etc
    sentiment_at_entry: int      # -3..+3
    quality_at_entry: int        # 0..100
    exit_reason: ExitReason
    duration_seconds: float


@dataclass
class _ContextStats:
    """Estatísticas agregadas por contexto (regime + bias de sentimento)."""
    trades: int = 0
    wins: int = 0
    total_pnl_pct: float = 0.0
    sum_quality: int = 0

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades else 0.0

    @property
    def avg_pnl_pct(self) -> float:
        return self.total_pnl_pct / self.trades if self.trades else 0.0

    @property
    def avg_quality(self) -> float:
        return self.sum_quality / self.trades if self.trades else 0.0


@dataclass
class LearningState:
    """Estado persistido. Tudo que sobrevive entre execuções."""
    total_trades: int = 0
    total_wins: int = 0
    total_pnl_pct: float = 0.0
    contexts: dict[str, _ContextStats] = field(default_factory=dict)

    # Multiplicadores aplicados sobre o baseline do AgentConfig
    quality_multiplier: float = 1.0       # 0.7 .. 1.3
    stop_loss_multiplier: float = 1.0     # 0.7 .. 1.5
    take_profit_multiplier: float = 1.0   # 0.7 .. 1.3
    # NOVO: tamanho de posição. Multiplicador sobre trade_size_pct do config.
    # Persistido em disco — sobrevive a reinícios. 0.5 = metade, 1.5 = 50% mais.
    size_multiplier: float = 1.0          # 0.5 .. 1.5

    # Contadores de "evidências" (quantos trades de cada tipo em sequência)
    consec_sl_hits: int = 0
    consec_tp_hits: int = 0
    last_adjustments: list[str] = field(default_factory=list)


@dataclass
class LearningConfig:
    persist_path: Path | None = None
    risk_profile: str = "bom"             # perfil de risco: risco, bom, seguro
    min_trades_to_adjust: int = 3         # ú só ajusta após N trades (era 5)
    review_every_n_trades: int = 1        # revisa a cada trade (era 3) — aprendizado contínuo
    max_quality_mult: float = 1.30        # +30% no min_setup_quality (max)
    min_quality_mult: float = 0.50        # -50% (era -30%) — deixa ser mais agressivo se ganhar
    max_sl_mult: float = 1.50
    min_sl_mult: float = 0.70
    max_tp_mult: float = 1.50             # era 1.30 — permite alvos maiores se for bom
    min_tp_mult: float = 0.50             # era 0.70 — permite alvos menores p/ scalping
    log_history_size: int = 50            # quantas explicações guardar (era 30)


class LearningEngine:
    """Cérebro adaptativo. Thread-safe."""

    def __init__(self, cfg: LearningConfig | None = None) -> None:
        self.cfg = cfg or LearningConfig()
        if self.cfg.persist_path is None:
            try:
                from core.paths import data_dir
                profile = getattr(self.cfg, "risk_profile", "bom") or "bom"
                self.cfg.persist_path = data_dir() / f"learning_{profile}.json"
            except Exception:
                profile = getattr(self.cfg, "risk_profile", "bom") or "bom"
                self.cfg.persist_path = Path(f"data/learning_{profile}.json")
        self._lock = Lock()
        self.state = self._load()

    # ---------- Persistência ----------
    def _load(self) -> LearningState:
        p = self.cfg.persist_path
        if not p.exists():
            return LearningState()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            contexts = {
                k: _ContextStats(**v) for k, v in data.get("contexts", {}).items()
            }
            return LearningState(
                total_trades=int(data.get("total_trades", 0)),
                total_wins=int(data.get("total_wins", 0)),
                total_pnl_pct=float(data.get("total_pnl_pct", 0.0)),
                contexts=contexts,
                quality_multiplier=float(data.get("quality_multiplier", 1.0)),
                stop_loss_multiplier=float(data.get("stop_loss_multiplier", 1.0)),
                take_profit_multiplier=float(data.get("take_profit_multiplier", 1.0)),
                size_multiplier=float(data.get("size_multiplier", 1.0)),
                consec_sl_hits=int(data.get("consec_sl_hits", 0)),
                consec_tp_hits=int(data.get("consec_tp_hits", 0)),
                last_adjustments=list(data.get("last_adjustments", [])),
            )
        except Exception as e:  # noqa: BLE001
            log.warning("Falha ao carregar learning state: %s — começando do zero", e)
            return LearningState()

    def _save(self) -> None:
        p = self.cfg.persist_path
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "total_trades": self.state.total_trades,
            "total_wins": self.state.total_wins,
            "total_pnl_pct": self.state.total_pnl_pct,
            "contexts": {k: asdict(v) for k, v in self.state.contexts.items()},
            "quality_multiplier": self.state.quality_multiplier,
            "stop_loss_multiplier": self.state.stop_loss_multiplier,
            "take_profit_multiplier": self.state.take_profit_multiplier,
            "size_multiplier": self.state.size_multiplier,
            "consec_sl_hits": self.state.consec_sl_hits,
            "consec_tp_hits": self.state.consec_tp_hits,
            "last_adjustments": self.state.last_adjustments[-self.cfg.log_history_size:],
        }
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # ---------- API pública ----------
    @staticmethod
    def context_key(regime: str, sentiment: int) -> str:
        bias = "bull" if sentiment >= 1 else ("bear" if sentiment <= -1 else "neutral")
        return f"{regime}|{bias}"

    def record_trade(self, outcome: TradeOutcome) -> list[str]:
        """Registra um trade fechado. Retorna lista de mensagens de ajuste (se houve)."""
        with self._lock:
            self.state.total_trades += 1
            self.state.total_pnl_pct += outcome.pnl_pct
            if outcome.win:
                self.state.total_wins += 1
                self.state.consec_tp_hits += 1 if outcome.exit_reason == "TP" else 0
                self.state.consec_sl_hits = 0
            else:
                self.state.consec_sl_hits += 1 if outcome.exit_reason == "SL" else 0
                self.state.consec_tp_hits = 0

            key = self.context_key(outcome.regime, outcome.sentiment_at_entry)
            ctx = self.state.contexts.setdefault(key, _ContextStats())
            ctx.trades += 1
            ctx.total_pnl_pct += outcome.pnl_pct
            ctx.sum_quality += outcome.quality_at_entry
            if outcome.win:
                ctx.wins += 1

            adjustments: list[str] = []
            if (self.state.total_trades >= self.cfg.min_trades_to_adjust
                    and self.state.total_trades % self.cfg.review_every_n_trades == 0):
                adjustments = self._review_adjustments()

            self._save()
            return adjustments

    def _review_adjustments(self) -> list[str]:
        """Heurística de ajuste — explicável e limitada."""
        msgs: list[str] = []
        wr = self.state.total_wins / self.state.total_trades if self.state.total_trades else 0.5

        # === Regra 1: muitos stops seguidos → aperta filtro de qualidade
        if self.state.consec_sl_hits >= 3:
            new = min(self.cfg.max_quality_mult, self.state.quality_multiplier + 0.05)
            if new != self.state.quality_multiplier:
                msgs.append(
                    f"3+ stops seguidos → ↑ qualidade mínima ({self.state.quality_multiplier:.2f} → {new:.2f})"
                )
                self.state.quality_multiplier = new
                self.state.consec_sl_hits = 0

        # === Regra 2: TPs seguidos → mercado favorável, pode afrouxar (mas com cautela)
        elif self.state.consec_tp_hits >= 4:
            new = max(self.cfg.min_quality_mult, self.state.quality_multiplier - 0.03)
            if new != self.state.quality_multiplier:
                msgs.append(
                    f"4+ wins seguidos → ↓ qualidade mínima ({self.state.quality_multiplier:.2f} → {new:.2f})"
                )
                self.state.quality_multiplier = new
                self.state.consec_tp_hits = 0

        # === Regra 3: win-rate global muito baixo → recolhe-se (filtro mais duro)
        if self.state.total_trades >= 10:
            if wr < 0.35 and self.state.quality_multiplier < self.cfg.max_quality_mult:
                new = min(self.cfg.max_quality_mult, self.state.quality_multiplier + 0.10)
                msgs.append(
                    f"Win-rate {wr*100:.0f}% baixo → ↑ qualidade mínima ({self.state.quality_multiplier:.2f} → {new:.2f})"
                )
                self.state.quality_multiplier = new
            elif wr > 0.65 and self.state.quality_multiplier > self.cfg.min_quality_mult:
                new = max(self.cfg.min_quality_mult, self.state.quality_multiplier - 0.05)
                msgs.append(
                    f"Win-rate {wr*100:.0f}% bom → ↓ qualidade mínima ({self.state.quality_multiplier:.2f} → {new:.2f})"
                )
                self.state.quality_multiplier = new

        # === Regra 4: stops batendo muito mas reverteria (proxy: muitos SL e win-rate < 50)
        if self.state.consec_sl_hits >= 2 and wr < 0.45:
            new = min(self.cfg.max_sl_mult, self.state.stop_loss_multiplier + 0.10)
            if new != self.state.stop_loss_multiplier:
                msgs.append(
                    f"Stops batendo → afrouxa SL ({self.state.stop_loss_multiplier:.2f} → {new:.2f})"
                )
                self.state.stop_loss_multiplier = new

        # === Regra 5: TPs raros (saídas quase sempre por SELL_signal/EOD) → reduz alvo
        sell_signal_exits = sum(
            1 for k, v in self.state.contexts.items() if v.trades > 0
        )  # placeholder simples; refinamento futuro

        # === Regra 6: PnL médio por trade — calibra TP/SL para micro-lucros
        if self.state.total_trades >= 8:
            avg_pnl = self.state.total_pnl_pct / self.state.total_trades
            # Lucro médio muito pequeno mas positivo → reduz TP (pega mais cedo)
            if 0 < avg_pnl < 0.3 and self.state.take_profit_multiplier > self.cfg.min_tp_mult:
                new = max(self.cfg.min_tp_mult, self.state.take_profit_multiplier - 0.05)
                msgs.append(
                    f"PnL médio {avg_pnl:+.2f}% baixo → ↓ TP ({self.state.take_profit_multiplier:.2f} → {new:.2f}) (scalping)"
                )
                self.state.take_profit_multiplier = new
            # Vencendo bem → aumenta TP pra capturar mais
            elif avg_pnl > 1.0 and self.state.take_profit_multiplier < self.cfg.max_tp_mult:
                new = min(self.cfg.max_tp_mult, self.state.take_profit_multiplier + 0.05)
                msgs.append(
                    f"PnL médio {avg_pnl:+.2f}% bom → ↑ TP ({self.state.take_profit_multiplier:.2f} → {new:.2f})"
                )
                self.state.take_profit_multiplier = new

        # === Regra 7: PnL global negativo → modo defensivo (aperta tudo)
        if self.state.total_trades >= 5 and self.state.total_pnl_pct < -2.0:
            if self.state.quality_multiplier < self.cfg.max_quality_mult:
                new_q = min(self.cfg.max_quality_mult, self.state.quality_multiplier + 0.05)
                if new_q != self.state.quality_multiplier:
                    msgs.append(
                        f"PnL total {self.state.total_pnl_pct:+.1f}% negativo → modo defensivo "
                        f"(qualidade {self.state.quality_multiplier:.2f} → {new_q:.2f})"
                    )
                    self.state.quality_multiplier = new_q

        if msgs:
            for m in msgs:
                self.state.last_adjustments.append(m)
                log.info("[learning] %s", m)
        return msgs

    def apply_to_agent(self, agent, baseline_cfg) -> None:
        """Aplica multiplicadores ao agente em runtime, sem mexer no baseline."""
        with self._lock:
            agent.cfg.min_setup_quality = int(round(
                baseline_cfg.min_setup_quality * self.state.quality_multiplier
            ))
            agent.cfg.stop_loss_pct = round(
                baseline_cfg.stop_loss_pct * self.state.stop_loss_multiplier, 4
            )
            agent.cfg.take_profit_pct = round(
                baseline_cfg.take_profit_pct * self.state.take_profit_multiplier, 4
            )

    def reset(self) -> None:
        with self._lock:
            self.state = LearningState()
            self._save()

    # ---------- Métricas para UI ----------
    def stats(self) -> dict:
        with self._lock:
            wr = (self.state.total_wins / self.state.total_trades * 100.0) \
                if self.state.total_trades else 0.0
            return {
                "trades": self.state.total_trades,
                "wins": self.state.total_wins,
                "win_rate_pct": wr,
                "total_pnl_pct": self.state.total_pnl_pct,
                "quality_mult": self.state.quality_multiplier,
                "sl_mult": self.state.stop_loss_multiplier,
                "tp_mult": self.state.take_profit_multiplier,
                "best_context": self._best_context(),
                "worst_context": self._worst_context(),
                "recent_adjustments": list(self.state.last_adjustments[-5:]),
            }

    def _best_context(self) -> str | None:
        items = [(k, v) for k, v in self.state.contexts.items() if v.trades >= 3]
        if not items:
            return None
        items.sort(key=lambda x: x[1].avg_pnl_pct, reverse=True)
        k, v = items[0]
        return f"{k} ({v.win_rate*100:.0f}% wr, {v.avg_pnl_pct:+.2f}% avg)"

    def _worst_context(self) -> str | None:
        items = [(k, v) for k, v in self.state.contexts.items() if v.trades >= 3]
        if not items:
            return None
        items.sort(key=lambda x: x[1].avg_pnl_pct)
        k, v = items[0]
        return f"{k} ({v.win_rate*100:.0f}% wr, {v.avg_pnl_pct:+.2f}% avg)"
