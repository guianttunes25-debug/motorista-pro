"""Camada de proteções pra rodar 24h sem perder dinheiro.

Três guards independentes do RiskEngine:
1. WeeklyCircuitBreaker: trava operações por 7 dias se PnL semanal < -10%
2. Heartbeat: arquivo touchado a cada loop; se externo verifica age > 5min, mata
3. TradeRateLimiter: max N trades/h — protege contra loops de bug

Estado persiste em data/safety.json (sobrevive reboot).
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock

log = logging.getLogger(__name__)


@dataclass
class SafetyConfig:
    # Circuit breaker semanal
    weekly_max_loss_pct: float = 10.0   # >10% perda em 7d → trava
    weekly_lockout_hours: int = 168     # 7 dias
    # Heartbeat
    heartbeat_max_age_sec: int = 300    # 5 min sem heartbeat = problema
    # Rate limit
    max_trades_per_hour: int = 20
    max_trades_per_day: int = 100


@dataclass
class SafetyState:
    # Circuit breaker
    week_start_iso: str = ""             # ISO date do início da janela 7d
    week_start_equity: float = 0.0
    weekly_locked_until_iso: str = ""    # ISO datetime — vazio = não travado
    weekly_lock_reason: str = ""
    # Rate limit
    trades_log: list[float] = field(default_factory=list)   # timestamps unix
    rate_locked_until_iso: str = ""
    # Heartbeat (apenas em memória — não persiste)


class SafetyEngine:
    """Thread-safe. Estado persistido em data/safety.json."""

    def __init__(self, cfg: SafetyConfig | None = None,
                 persist_path: Path | None = None) -> None:
        self.cfg = cfg or SafetyConfig()
        self.persist_path = persist_path or Path("data/safety.json")
        self._lock = Lock()
        self._last_heartbeat: float = time.time()
        self.state = self._load()

    def _load(self) -> SafetyState:
        if not self.persist_path.exists():
            return SafetyState()
        try:
            data = json.loads(self.persist_path.read_text(encoding="utf-8"))
            return SafetyState(**data)
        except Exception as e:  # noqa: BLE001
            log.warning("safety.json corrompido (%s), recomeçando", e)
            return SafetyState()

    def _save(self) -> None:
        try:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            self.persist_path.write_text(
                json.dumps(asdict(self.state), indent=2), encoding="utf-8"
            )
        except Exception as e:  # noqa: BLE001
            log.warning("Falha ao persistir safety: %s", e)

    # ---------- Heartbeat ----------
    def beat(self) -> None:
        """Chamado a cada tick do worker — mostra que ainda está vivo."""
        self._last_heartbeat = time.time()

    def heartbeat_age(self) -> float:
        return time.time() - self._last_heartbeat

    def is_heartbeat_stale(self) -> bool:
        return self.heartbeat_age() > self.cfg.heartbeat_max_age_sec

    # ---------- Circuit breaker semanal ----------
    def update_equity(self, equity: float) -> tuple[bool, str]:
        """Atualiza janela 7d. Retorna (locked, reason)."""
        with self._lock:
            now = datetime.now()
            # Inicializa janela se vazia
            if not self.state.week_start_iso:
                self.state.week_start_iso = now.isoformat()
                self.state.week_start_equity = equity
                self._save()
                return False, "ok"
            # Rola janela se passou 7 dias
            try:
                start = datetime.fromisoformat(self.state.week_start_iso)
                if (now - start) > timedelta(days=7):
                    self.state.week_start_iso = now.isoformat()
                    self.state.week_start_equity = equity
                    self._save()
                    return False, "ok"
            except Exception:  # noqa: BLE001
                self.state.week_start_iso = now.isoformat()
                self.state.week_start_equity = equity
            # Calcula PnL semanal
            base = max(self.state.week_start_equity, 1e-9)
            loss_pct = (base - equity) / base * 100.0
            if loss_pct >= self.cfg.weekly_max_loss_pct:
                lock_until = now + timedelta(hours=self.cfg.weekly_lockout_hours)
                self.state.weekly_locked_until_iso = lock_until.isoformat()
                self.state.weekly_lock_reason = (
                    f"Perda semanal {loss_pct:.2f}% atingiu limite "
                    f"{self.cfg.weekly_max_loss_pct}%"
                )
                self._save()
                return True, self.state.weekly_lock_reason
            return False, "ok"

    def is_weekly_locked(self) -> tuple[bool, str]:
        if not self.state.weekly_locked_until_iso:
            return False, ""
        try:
            until = datetime.fromisoformat(self.state.weekly_locked_until_iso)
            if datetime.now() >= until:
                # Expirou — destrava e zera janela
                with self._lock:
                    self.state.weekly_locked_until_iso = ""
                    self.state.weekly_lock_reason = ""
                    self.state.week_start_iso = ""
                    self.state.week_start_equity = 0.0
                    self._save()
                return False, ""
            return True, self.state.weekly_lock_reason
        except Exception:  # noqa: BLE001
            return False, ""

    def reset_weekly_lock(self) -> None:
        with self._lock:
            self.state.weekly_locked_until_iso = ""
            self.state.weekly_lock_reason = ""
            self.state.week_start_iso = ""
            self.state.week_start_equity = 0.0
            self._save()

    # ---------- Rate limit ----------
    def record_trade(self) -> None:
        with self._lock:
            now = time.time()
            self.state.trades_log.append(now)
            # Mantém só últimas 24h
            cutoff = now - 86400
            self.state.trades_log = [t for t in self.state.trades_log if t >= cutoff]
            self._save()

    def trades_in_last_hour(self) -> int:
        now = time.time()
        return sum(1 for t in self.state.trades_log if t >= now - 3600)

    def trades_in_last_day(self) -> int:
        now = time.time()
        return sum(1 for t in self.state.trades_log if t >= now - 86400)

    def check_rate_limit(self) -> tuple[bool, str]:
        """Retorna (locked, reason). Trava por 1h se exceder."""
        with self._lock:
            # Se já tem lockout ativo, checa expiração
            if self.state.rate_locked_until_iso:
                try:
                    until = datetime.fromisoformat(self.state.rate_locked_until_iso)
                    if datetime.now() < until:
                        mins = int((until - datetime.now()).total_seconds() / 60)
                        return True, f"Rate limit ativo (destrava em {mins}min)"
                    self.state.rate_locked_until_iso = ""
                    self._save()
                except Exception:  # noqa: BLE001
                    self.state.rate_locked_until_iso = ""
        per_hour = self.trades_in_last_hour()
        per_day = self.trades_in_last_day()
        if per_hour >= self.cfg.max_trades_per_hour:
            with self._lock:
                until = datetime.now() + timedelta(hours=1)
                self.state.rate_locked_until_iso = until.isoformat()
                self._save()
            return True, (
                f"⛔ {per_hour} trades em 1h (limite {self.cfg.max_trades_per_hour}). "
                "Trava por 1h — possível loop de bug."
            )
        if per_day >= self.cfg.max_trades_per_day:
            with self._lock:
                until = datetime.now() + timedelta(hours=24)
                self.state.rate_locked_until_iso = until.isoformat()
                self._save()
            return True, (
                f"⛔ {per_day} trades em 24h (limite {self.cfg.max_trades_per_day})."
            )
        return False, "ok"

    # ---------- API consolidada ----------
    def can_trade(self, equity: float | None = None) -> tuple[bool, str]:
        """Chamado pelo engine antes de cada ordem."""
        if equity is not None:
            self.update_equity(equity)
        locked, reason = self.is_weekly_locked()
        if locked:
            return False, f"🔒 Circuit breaker semanal: {reason}"
        locked, reason = self.check_rate_limit()
        if locked:
            return False, reason
        return True, "ok"

    def status(self) -> dict:
        weekly_locked, weekly_reason = self.is_weekly_locked()
        return {
            "weekly_locked": weekly_locked,
            "weekly_reason": weekly_reason,
            "weekly_locked_until": self.state.weekly_locked_until_iso,
            "week_start_equity": self.state.week_start_equity,
            "trades_last_hour": self.trades_in_last_hour(),
            "trades_last_day": self.trades_in_last_day(),
            "rate_locked_until": self.state.rate_locked_until_iso,
            "heartbeat_age_sec": round(self.heartbeat_age(), 1),
            "heartbeat_stale": self.is_heartbeat_stale(),
        }
