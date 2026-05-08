"""Persistência de posição aberta — sobrevive a crash/restart do app.

Salva em data/position.json sempre que uma posição é aberta/atualizada/fechada.
Ao reiniciar, o engine pode chamar `load()` e reconstruir o estado.

Estrutura mínima — só o que é necessário pra reconciliar:
    {
        "symbol": "BTC/BRL",
        "amount": 0.0001,
        "entry_price": 380000.0,
        "stop_loss": 378000.0,
        "take_profit": 382000.0,
        "opened_at": 1730000000.0,
        "high_watermark": 380500.0,
        "trailing_active": true
    }
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class PersistedPosition:
    symbol: str = ""
    amount: float = 0.0
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    opened_at: float = 0.0
    high_watermark: float = 0.0
    trailing_active: bool = False
    extra: dict = field(default_factory=dict)


class PositionStore:
    """Thread-safe, escrita atômica (tmp + rename)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def save(self, pos: PersistedPosition) -> None:
        with self._lock:
            data = asdict(pos)
            tmp_fd, tmp_name = tempfile.mkstemp(prefix=".pos_", dir=str(self.path.parent))
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                os.replace(tmp_name, self.path)
            except Exception:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise

    def load(self) -> PersistedPosition | None:
        with self._lock:
            if not self.path.exists():
                return None
            try:
                with self.path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                return None
            try:
                return PersistedPosition(**{
                    k: data.get(k, getattr(PersistedPosition(), k))
                    for k in ("symbol", "amount", "entry_price", "stop_loss",
                              "take_profit", "opened_at", "high_watermark",
                              "trailing_active")
                })
            except Exception:
                return None

    def clear(self) -> None:
        with self._lock:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass

    def has_position(self) -> bool:
        pos = self.load()
        return bool(pos and pos.amount > 0)
