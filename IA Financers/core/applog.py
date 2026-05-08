"""Logger central da aplicação — grava TUDO em ``data/app.log``.

Captura:
  - ``logging`` Python (todos os loggers, root incluso) com nível INFO.
  - ``print()`` (via redirect de ``sys.stdout``).
  - ``sys.stderr`` (warnings, tracebacks impressos por libs).
  - Exceções não-tratadas (``sys.excepthook``).
  - Exceções não-tratadas em threads (``threading.excepthook``).
  - Hook do Qt para mensagens do framework (``qInstallMessageHandler``).

Uso (chamar UMA vez no startup, antes de qualquer outra coisa):
    from core.applog import install
    install()

Arquivo é rotativo: 5 arquivos de até 2 MB cada (~10 MB total).
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
import threading
import traceback
from pathlib import Path
from typing import IO

from core.paths import data_dir

_LOG_PATH: Path | None = None
_INSTALLED = False


def log_path() -> Path:
    """Caminho atual do app.log (resolve a primeira vez que é chamado)."""
    global _LOG_PATH
    if _LOG_PATH is None:
        _LOG_PATH = data_dir() / "app.log"
    return _LOG_PATH


class _StreamToLogger(IO[str]):
    """Wrapper que redireciona writes para um logger sem perder o stream original."""

    def __init__(self, logger: logging.Logger, level: int, original: IO[str] | None) -> None:
        self._logger = logger
        self._level = level
        self._buffer = ""
        self._original = original

    def write(self, msg: str) -> int:  # type: ignore[override]
        try:
            if self._original is not None:
                try:
                    self._original.write(msg)
                except Exception:  # noqa: BLE001
                    pass
            self._buffer += msg
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                line = line.rstrip()
                if line:
                    self._logger.log(self._level, line)
        except Exception:  # noqa: BLE001
            pass
        return len(msg)

    def flush(self) -> None:  # type: ignore[override]
        try:
            if self._original is not None:
                self._original.flush()
        except Exception:  # noqa: BLE001
            pass
        if self._buffer.strip():
            try:
                self._logger.log(self._level, self._buffer.rstrip())
            except Exception:  # noqa: BLE001
                pass
            self._buffer = ""

    def isatty(self) -> bool:  # type: ignore[override]
        return False


def _qt_message_handler(mode, context, message) -> None:  # pragma: no cover - depends on Qt runtime
    """Mapeia QtMsgType para nível do logging."""
    try:
        from PyQt6.QtCore import QtMsgType
        lvl = logging.INFO
        if mode == QtMsgType.QtWarningMsg:
            lvl = logging.WARNING
        elif mode == QtMsgType.QtCriticalMsg:
            lvl = logging.ERROR
        elif mode == QtMsgType.QtFatalMsg:
            lvl = logging.CRITICAL
        elif mode == QtMsgType.QtDebugMsg:
            lvl = logging.DEBUG
        logging.getLogger("qt").log(lvl, str(message))
    except Exception:  # noqa: BLE001
        pass


def install() -> Path:
    """Instala todos os handlers. Idempotente. Retorna o caminho do log."""
    global _INSTALLED
    p = log_path()
    if _INSTALLED:
        return p
    _INSTALLED = True

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Handler de arquivo rotativo: 5 arquivos x 2 MB
    file_h = logging.handlers.RotatingFileHandler(
        p, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_h.setFormatter(fmt)
    file_h.setLevel(logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Evita handlers duplicados se install() for chamado mais de uma vez por engano.
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) and getattr(h, "baseFilename", "") == str(p)
               for h in root.handlers):
        root.addHandler(file_h)

    # Console handler (só se houver console — em .exe sem console isso é no-op).
    try:
        if sys.stderr is not None and sys.stderr.isatty():
            ch = logging.StreamHandler(sys.stderr)
            ch.setFormatter(fmt)
            ch.setLevel(logging.WARNING)
            root.addHandler(ch)
    except Exception:  # noqa: BLE001
        pass

    # Reduz ruído de libs barulhentas
    for noisy in ("urllib3", "ccxt", "PIL", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Redireciona print() / stderr
    sys.stdout = _StreamToLogger(logging.getLogger("stdout"), logging.INFO, sys.stdout)
    sys.stderr = _StreamToLogger(logging.getLogger("stderr"), logging.ERROR, sys.stderr)

    # Hook para exceções não-tratadas
    def _excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            return
        logging.getLogger("uncaught").error(
            "Exceção não-tratada:\n%s",
            "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
        )

    sys.excepthook = _excepthook

    # Hook para exceções em threads (Python 3.8+)
    def _thread_excepthook(args: threading.ExceptHookArgs) -> None:
        if issubclass(args.exc_type, KeyboardInterrupt):
            return
        logging.getLogger("uncaught.thread").error(
            "Exceção em thread '%s':\n%s",
            args.thread.name if args.thread else "?",
            "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)),
        )

    threading.excepthook = _thread_excepthook

    # Qt message handler (faz isso depois do QApplication ser criado, idealmente,
    # mas a chamada aqui é segura — se Qt ainda não estiver carregado, ignora).
    try:
        from PyQt6.QtCore import qInstallMessageHandler
        qInstallMessageHandler(_qt_message_handler)
    except Exception:  # noqa: BLE001
        pass

    logging.getLogger("applog").info(
        "logger instalado — arquivo: %s (rotativo, 5x2MB)", p
    )
    return p
