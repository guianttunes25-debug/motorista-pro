"""Resolução de caminhos persistentes (funciona em dev e PyInstaller .exe).

Pode ser sobrescrito via variável de ambiente ``AITRADER_DATA_DIR`` —
útil em testes (conftest aponta para tmp_path) e para usuários que querem
guardar o estado em outro disco.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def app_dir() -> Path:
    """Diretório onde o config.json fica salvo de forma persistente.

    - Em dev: raiz do projeto (pasta que contém main.py).
    - Em .exe (PyInstaller): pasta onde está o .exe (NÃO o _MEIxxx temporário).
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def config_path() -> Path:
    """Caminho do config.json (persistente)."""
    return app_dir() / "config.json"


def data_dir() -> Path:
    """Diretório de dados persistentes (trades.csv, position.json, etc).

    Resolução, em ordem:
      1. ``$AITRADER_DATA_DIR`` (se definido) — usado pelos testes.
      2. ``app_dir() / "data"`` — comportamento padrão.
    Garante que o diretório exista.
    """
    env = os.environ.get("AITRADER_DATA_DIR")
    base = Path(env).expanduser() if env else (app_dir() / "data")
    base.mkdir(parents=True, exist_ok=True)
    return base
