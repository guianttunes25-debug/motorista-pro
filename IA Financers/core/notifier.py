"""Sistema de alertas externos — opt-in via config.

Suporta:
- log local (sempre ativo)
- Telegram (se configurado: bot_token + chat_id)
- Webhook genérico (POST JSON)

Uso:
    notifier = build_notifier(cfg.get("notifications", {}))
    notifier.notify("trade_opened", "Comprou BTC/BRL @ 381000", level="info")
    notifier.notify("emergency", "Kill switch acionado", level="critical")
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable, Literal, Optional

log = logging.getLogger(__name__)

Level = Literal["info", "warn", "error", "critical"]


@dataclass
class NotifierConfig:
    enabled: bool = True
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    webhook_url: str = ""
    min_level: Level = "info"


_LEVEL_ORDER = {"info": 0, "warn": 1, "error": 2, "critical": 3}


class Notifier:
    """Wrapper único — pode ter múltiplos backends."""

    def __init__(self, cfg: NotifierConfig):
        self.cfg = cfg
        self._sinks: list[Callable[[str, str, Level], None]] = []
        # Sempre log local
        self._sinks.append(self._sink_log)
        if cfg.telegram_bot_token and cfg.telegram_chat_id:
            self._sinks.append(self._sink_telegram)
        if cfg.webhook_url:
            self._sinks.append(self._sink_webhook)

    def notify(self, event: str, message: str, level: Level = "info") -> None:
        if not self.cfg.enabled:
            return
        if _LEVEL_ORDER[level] < _LEVEL_ORDER[self.cfg.min_level]:
            return
        for sink in self._sinks:
            # Cada sink em thread separada — alerta nunca trava o trade
            threading.Thread(
                target=self._safe_sink,
                args=(sink, event, message, level),
                daemon=True,
            ).start()

    @staticmethod
    def _safe_sink(sink, event, message, level):
        try:
            sink(event, message, level)
        except Exception as e:  # noqa: BLE001
            log.warning("notifier sink falhou: %s", e)

    # ----- backends -----
    @staticmethod
    def _sink_log(event: str, message: str, level: Level) -> None:
        fn = {"info": log.info, "warn": log.warning,
              "error": log.error, "critical": log.critical}[level]
        fn("[%s] %s", event, message)

    def _sink_telegram(self, event: str, message: str, level: Level) -> None:
        import urllib.parse
        import urllib.request
        emoji = {"info": "ℹ", "warn": "⚠", "error": "❌", "critical": "🚨"}[level]
        text = f"{emoji} *{event}*\n{message}"
        url = (
            f"https://api.telegram.org/bot{self.cfg.telegram_bot_token}/sendMessage"
        )
        data = urllib.parse.urlencode({
            "chat_id": self.cfg.telegram_chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        urllib.request.urlopen(req, timeout=10).read()

    def _sink_webhook(self, event: str, message: str, level: Level) -> None:
        import json
        import urllib.request
        payload = json.dumps({
            "event": event, "message": message, "level": level
        }).encode()
        req = urllib.request.Request(
            self.cfg.webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10).read()


def build_notifier(cfg_dict: dict | None) -> Notifier:
    cfg_dict = cfg_dict or {}
    return Notifier(NotifierConfig(
        enabled=bool(cfg_dict.get("enabled", True)),
        telegram_bot_token=str(cfg_dict.get("telegram_bot_token", "")),
        telegram_chat_id=str(cfg_dict.get("telegram_chat_id", "")),
        webhook_url=str(cfg_dict.get("webhook_url", "")),
        min_level=cfg_dict.get("min_level", "info"),
    ))
