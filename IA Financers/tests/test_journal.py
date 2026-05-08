"""Testes do journal CSV append-only."""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from core.journal import append_trade


def test_creates_file_with_header(tmp_path: Path):
    p = tmp_path / "out" / "trades.csv"
    append_trade(p, timestamp=datetime(2026, 1, 1, 12, 0, 0),
                 symbol="BTC/USDT", side="BUY", price=100.0,
                 amount=0.5, pnl=0.0, mode="simulation")
    assert p.exists()
    rows = list(csv.reader(p.open("r", encoding="utf-8")))
    assert rows[0] == ["timestamp", "symbol", "side", "price", "amount", "pnl", "mode"]
    assert rows[1][1:3] == ["BTC/USDT", "BUY"]


def test_append_preserves_previous(tmp_path: Path):
    p = tmp_path / "trades.csv"
    append_trade(p, timestamp=datetime(2026, 1, 1, 12, 0, 0),
                 symbol="BTC/USDT", side="BUY", price=100.0,
                 amount=0.5, pnl=0.0, mode="simulation")
    append_trade(p, timestamp=datetime(2026, 1, 1, 13, 0, 0),
                 symbol="BTC/USDT", side="SELL", price=102.0,
                 amount=0.5, pnl=1.0, mode="simulation")
    rows = list(csv.reader(p.open("r", encoding="utf-8")))
    assert len(rows) == 3  # header + 2
    assert rows[1][2] == "BUY"
    assert rows[2][2] == "SELL"


def test_thread_safe_concurrent_writes(tmp_path: Path):
    """20 threads escrevendo simultaneamente — todas linhas íntegras."""
    import threading
    p = tmp_path / "concurrent.csv"

    def worker(i: int):
        for j in range(10):
            append_trade(p, timestamp=datetime(2026, 1, 1, 12, i, j),
                         symbol="BTC/USDT", side="BUY",
                         price=100.0 + i, amount=0.01,
                         pnl=0.0, mode="simulation")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rows = list(csv.reader(p.open("r", encoding="utf-8")))
    # 1 header + 200 trades
    assert len(rows) == 201
    # nenhuma linha corrompida (sempre 7 colunas)
    assert all(len(r) == 7 for r in rows)
