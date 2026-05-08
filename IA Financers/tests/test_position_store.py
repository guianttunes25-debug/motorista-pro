"""Testes para core/position_store.py."""
from __future__ import annotations

import threading

from core.position_store import PersistedPosition, PositionStore


def test_save_and_load_roundtrip(tmp_path):
    store = PositionStore(tmp_path / "pos.json")
    pos = PersistedPosition(
        symbol="BTC/BRL", amount=0.0001, entry_price=380000,
        stop_loss=378000, take_profit=382000,
        opened_at=1730000000.0, high_watermark=380500.0,
        trailing_active=True,
    )
    store.save(pos)
    loaded = store.load()
    assert loaded is not None
    assert loaded.symbol == "BTC/BRL"
    assert loaded.amount == 0.0001
    assert loaded.entry_price == 380000
    assert loaded.trailing_active is True


def test_load_empty_returns_none(tmp_path):
    store = PositionStore(tmp_path / "pos.json")
    assert store.load() is None
    assert store.has_position() is False


def test_clear_removes_file(tmp_path):
    store = PositionStore(tmp_path / "pos.json")
    store.save(PersistedPosition(symbol="X", amount=1.0, entry_price=10))
    assert store.has_position()
    store.clear()
    assert store.load() is None
    assert not store.path.exists()


def test_clear_no_file_is_noop(tmp_path):
    store = PositionStore(tmp_path / "pos.json")
    store.clear()  # não deve lançar


def test_load_corrupt_json_returns_none(tmp_path):
    p = tmp_path / "pos.json"
    p.write_text("{not valid json", encoding="utf-8")
    store = PositionStore(p)
    assert store.load() is None


def test_atomic_save_does_not_leave_temp(tmp_path):
    store = PositionStore(tmp_path / "pos.json")
    store.save(PersistedPosition(symbol="X", amount=1.0))
    leftovers = [f for f in tmp_path.iterdir() if f.name.startswith(".pos_")]
    assert leftovers == []


def test_concurrent_saves_dont_corrupt(tmp_path):
    store = PositionStore(tmp_path / "pos.json")

    def writer(i):
        store.save(PersistedPosition(symbol=f"X{i}", amount=float(i)))

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # arquivo final deve ser legível e válido
    pos = store.load()
    assert pos is not None
    assert pos.symbol.startswith("X")
