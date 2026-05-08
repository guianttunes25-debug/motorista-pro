"""Pytest config — adiciona raiz do projeto ao sys.path e isola data/.

Importante: redireciona ``AITRADER_DATA_DIR`` para uma pasta temporária por sessão
de teste para que ``data/trades.csv``, ``data/position.json`` etc. NÃO sejam
sobrescritos a cada execução de pytest. Isso elimina o bug em que rodar a suite
contaminava o estado real do app.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True, scope="session")
def _isolate_data_dir():
    """Redireciona AITRADER_DATA_DIR para um diretório temporário único."""
    tmp = Path(tempfile.mkdtemp(prefix="aitrader_tests_data_"))
    prev = os.environ.get("AITRADER_DATA_DIR")
    os.environ["AITRADER_DATA_DIR"] = str(tmp)
    try:
        yield tmp
    finally:
        if prev is None:
            os.environ.pop("AITRADER_DATA_DIR", None)
        else:
            os.environ["AITRADER_DATA_DIR"] = prev
