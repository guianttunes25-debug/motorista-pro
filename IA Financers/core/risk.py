"""Risk Engine — camada crítica.

Regras:
    * Limite de perda diária (% sobre patrimônio do início do dia).
    * Bloqueio em alta volatilidade (volatility_pct > max_volatility_pct).
    * Tamanho máximo por trade (% do patrimônio).
    * Posição máxima permitida (% do patrimônio em ativo).
    * Kill switch global (emergência) e pause.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

Side = Literal["BUY", "SELL"]


@dataclass
class RiskConfig:
    initial_balance_usdt: float = 1000.0
    trade_size_pct: float = 10.0
    max_daily_loss_pct: float = 2.0
    max_position_pct: float = 25.0
    max_volatility_pct: float = 3.0


@dataclass
class RiskState:
    day: date = field(default_factory=date.today)
    day_start_equity: float = 0.0
    kill_switch: bool = False
    paused: bool = False
    last_reason: str = ""


class RiskEngine:
    def __init__(self, cfg: RiskConfig) -> None:
        self.cfg = cfg
        self.state = RiskState(day_start_equity=cfg.initial_balance_usdt)

    # ---------- Controle ----------
    def trip_kill_switch(self, reason: str = "Manual") -> None:
        self.state.kill_switch = True
        self.state.last_reason = reason

    def reset_kill_switch(self) -> None:
        self.state.kill_switch = False
        self.state.last_reason = ""

    def pause(self) -> None:
        self.state.paused = True

    def resume(self) -> None:
        self.state.paused = False

    def roll_day_if_needed(self, equity: float) -> None:
        today = date.today()
        if today != self.state.day:
            self.state.day = today
            self.state.day_start_equity = equity
            # Novo dia, novo começo: libera o kill-switch acionado por perda diária.
            # (kill-switch manual deve ser resetado explicitamente pelo usuário)
            if self.state.kill_switch and self.state.last_reason.startswith("Perda diária"):
                self.reset_kill_switch()

    # ---------- Validações ----------
    def allow_trade(self, equity: float, volatility_pct: float) -> tuple[bool, str]:
        if self.state.kill_switch:
            return False, f"Kill switch: {self.state.last_reason}"
        if self.state.paused:
            return False, "Operações pausadas"
        if volatility_pct > self.cfg.max_volatility_pct:
            return False, f"Volatilidade {volatility_pct:.2f}% > limite {self.cfg.max_volatility_pct}%"

        loss_pct = (self.state.day_start_equity - equity) / max(self.state.day_start_equity, 1e-9) * 100.0
        if loss_pct >= self.cfg.max_daily_loss_pct:
            self.trip_kill_switch(f"Perda diária {loss_pct:.2f}% atingiu limite")
            return False, self.state.last_reason
        return True, "ok"

    def order_size(self, side: Side, equity: float, position_value: float, price: float) -> float:
        if price <= 0:
            return 0.0
        size_usdt = equity * (self.cfg.trade_size_pct / 100.0)
        if side == "BUY":
            max_pos_usdt = equity * (self.cfg.max_position_pct / 100.0)
            allowed = max(0.0, max_pos_usdt - position_value)
            size_usdt = min(size_usdt, allowed)
        else:
            size_usdt = min(size_usdt, position_value)
        return round(size_usdt / price, 8)

    def status(self, equity: float) -> dict:
        loss_pct = (self.state.day_start_equity - equity) / max(self.state.day_start_equity, 1e-9) * 100.0
        return {
            "kill_switch": self.state.kill_switch,
            "paused": self.state.paused,
            "day_start_equity": self.state.day_start_equity,
            "daily_pnl_pct": -loss_pct,
            "max_daily_loss_pct": self.cfg.max_daily_loss_pct,
            "last_reason": self.state.last_reason,
        }


# Função simples (compatível com o snippet do plano)
MAX_DAILY_LOSS = 0.02


def allow_trade(current_loss: float) -> bool:
    return current_loss < MAX_DAILY_LOSS
