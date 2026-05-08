"""Validação e arredondamento de ordens conforme limites da exchange.

Toda exchange tem regras: minNotional (valor mínimo em quote), step size
(precisão da quantidade), tick size (precisão do preço). Mandar ordem fora
desses limites = rejeitada. Este módulo lê os limites do mercado via ccxt
e devolve a ordem ajustada OU um motivo claro pra rejeitar.

Uso:
    limits = MarketLimits.from_ccxt(ccxt_market_dict)
    ok, amount_adj, reason = limits.validate_and_round(price=100.0, amount=0.05)
    if not ok:
        log.warning("ordem rejeitada localmente: %s", reason)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass
class MarketLimits:
    symbol: str
    min_amount: float = 0.0       # quantidade mínima
    max_amount: float = float("inf")
    amount_step: float = 0.0      # múltiplo (lot size)
    min_notional: float = 0.0     # valor mínimo em quote (price*amount)
    max_notional: float = float("inf")
    price_precision: int = 8
    amount_precision: int = 8

    @classmethod
    def from_ccxt(cls, market: dict[str, Any]) -> "MarketLimits":
        if not isinstance(market, dict):
            return cls(symbol="?")
        limits = market.get("limits", {}) or {}
        amt = limits.get("amount", {}) or {}
        cost = limits.get("cost", {}) or {}
        precision = market.get("precision", {}) or {}

        # ccxt expõe amount step via precision (pode ser int=decimais ou float=step)
        ap = precision.get("amount")
        if isinstance(ap, float):
            amount_step = ap
            amount_precision = max(0, -int(math.floor(math.log10(ap)))) if ap > 0 else 8
        elif isinstance(ap, int):
            amount_precision = ap
            amount_step = 10 ** (-ap) if ap > 0 else 0.0
        else:
            amount_precision = 8
            amount_step = 0.0

        pp = precision.get("price")
        if isinstance(pp, float):
            price_precision = max(0, -int(math.floor(math.log10(pp)))) if pp > 0 else 8
        elif isinstance(pp, int):
            price_precision = pp
        else:
            price_precision = 8

        return cls(
            symbol=str(market.get("symbol", "?")),
            min_amount=float(amt.get("min") or 0.0),
            max_amount=float(amt.get("max") or float("inf")),
            amount_step=float(amount_step),
            min_notional=float(cost.get("min") or 0.0),
            max_notional=float(cost.get("max") or float("inf")),
            price_precision=int(price_precision),
            amount_precision=int(amount_precision),
        )

    def round_amount(self, amount: float) -> float:
        """Arredonda para baixo no múltiplo do step (nunca pra cima — evita
        gastar mais do que disponível)."""
        if amount <= 0:
            return 0.0
        if self.amount_step > 0:
            steps = math.floor(amount / self.amount_step + 1e-12)
            return round(steps * self.amount_step, self.amount_precision)
        return round(amount, self.amount_precision)

    def validate_and_round(
        self, price: float, amount: float
    ) -> tuple[bool, float, str]:
        """Devolve (ok, amount_ajustado, motivo).

        - Arredonda para o step válido.
        - Verifica min_amount, max_amount, min_notional, max_notional.
        """
        if price <= 0 or amount <= 0:
            return False, 0.0, "preço/quantidade inválidos"

        adj = self.round_amount(amount)
        if adj <= 0:
            return False, 0.0, f"quantidade {amount:.10f} arredondou para 0 (step={self.amount_step})"

        if adj < self.min_amount:
            return False, adj, f"quantidade {adj} < min_amount {self.min_amount}"
        if adj > self.max_amount:
            return False, adj, f"quantidade {adj} > max_amount {self.max_amount}"

        notional = adj * price
        if notional < self.min_notional:
            return False, adj, f"notional {notional:.4f} < min_notional {self.min_notional}"
        if notional > self.max_notional:
            return False, adj, f"notional {notional:.4f} > max_notional {self.max_notional}"

        return True, adj, "OK"


def fetch_limits(ccxt_client, symbol: str) -> MarketLimits:
    """Busca os limites no ccxt (carrega markets se ainda não carregou)."""
    try:
        if not ccxt_client.markets:
            ccxt_client.load_markets()
        market = ccxt_client.market(symbol)
        return MarketLimits.from_ccxt(market)
    except Exception:
        return MarketLimits(symbol=symbol)
