"""Testes para core/notifier.py."""
from __future__ import annotations

import time

from core.notifier import Notifier, NotifierConfig, build_notifier


def test_disabled_does_nothing():
    cfg = NotifierConfig(enabled=False)
    n = Notifier(cfg)
    n.notify("x", "y")  # não deve lançar nada


def test_min_level_filters_lower():
    n = Notifier(NotifierConfig(min_level="error"))
    sent: list[tuple] = []
    n._sinks = [lambda e, m, l: sent.append((e, m, l))]
    n.notify("x", "info msg", level="info")
    n.notify("x", "warn msg", level="warn")
    n.notify("x", "err msg", level="error")
    time.sleep(0.05)  # threads são daemon
    levels = [s[2] for s in sent]
    assert "info" not in levels
    assert "warn" not in levels
    assert "error" in levels


def test_sink_failure_is_isolated():
    n = Notifier(NotifierConfig())
    called = []

    def bad(e, m, l):
        raise RuntimeError("boom")

    def good(e, m, l):
        called.append((e, m, l))

    n._sinks = [bad, good]
    n.notify("x", "test", level="info")
    time.sleep(0.1)
    # good ainda foi chamado mesmo com bad lançando
    assert len(called) == 1


def test_build_notifier_from_dict():
    n = build_notifier({
        "enabled": True,
        "telegram_bot_token": "abc",
        "telegram_chat_id": "123",
    })
    assert n.cfg.telegram_bot_token == "abc"
    assert n.cfg.telegram_chat_id == "123"


def test_build_notifier_handles_none():
    n = build_notifier(None)
    assert n.cfg.enabled is True
