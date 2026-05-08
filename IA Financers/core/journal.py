"""Persistência simples de trades para auditoria (CSV append-only)."""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from threading import Lock

_LOCK = Lock()
_HEADER = ["timestamp", "symbol", "side", "price", "amount", "pnl", "mode"]


def _ensure(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(_HEADER)


def append_trade(
    path: Path,
    *,
    timestamp: datetime,
    symbol: str,
    side: str,
    price: float,
    amount: float,
    pnl: float,
    mode: str,
) -> None:
    with _LOCK:
        _ensure(path)
        with path.open("a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([
                timestamp.isoformat(timespec="seconds"),
                symbol, side, f"{price:.8f}", f"{amount:.8f}",
                f"{pnl:.8f}", mode,
            ])
