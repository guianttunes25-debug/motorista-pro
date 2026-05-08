"""Auto-update do executável.

Fluxo:
    1. App inicia e baixa um manifest JSON (URL configurada em config.json
       como `update_manifest_url`). Se vazio, atualização é desativada.
    2. Manifest tem o formato:
            {
              "version": "1.0.1",
              "url":     "https://meusite.com/releases/AITraderCopilot-1.0.1.exe",
              "sha256":  "<hash hex opcional>",
              "notes":   "Texto opcional"
            }
    3. Se a versão remota > local, baixa o .exe novo para um arquivo temporário
       ao lado do executável, valida sha256 (se fornecido) e dispara um .bat
       que substitui o .exe atual e relança o app.

Funciona apenas quando rodando como executável congelado (PyInstaller).
Em modo `python main.py` o updater fica desativado para não bagunçar o dev.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from version import __version__

log = logging.getLogger("updater")


@dataclass
class UpdateInfo:
    version: str
    url: str
    sha256: Optional[str] = None
    notes: str = ""


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def _exe_path() -> Path:
    return Path(sys.executable).resolve()


def _parse_version(v: str) -> tuple[int, ...]:
    """Converte '1.2.3' ou 'v1.2.3-rc1' em tupla de inteiros para comparação.

    Para sufixos não-numéricos (ex.: '3-rc1'), extrai apenas o prefixo numérico.
    """
    parts: list[int] = []
    for chunk in v.strip().lstrip("v").split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def fetch_manifest(url: str, timeout: int = 8) -> Optional[UpdateInfo]:
    if not url:
        return None
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        return UpdateInfo(
            version=str(data["version"]),
            url=str(data["url"]),
            sha256=data.get("sha256"),
            notes=data.get("notes", ""),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("Falha ao ler manifest de update: %s", e)
        return None


def has_update(info: UpdateInfo) -> bool:
    return _parse_version(info.version) > _parse_version(__version__)


def _download(url: str, dest: Path, timeout: int = 60) -> None:
    with requests.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_content(chunk_size=64 * 1024):
                if chunk:
                    f.write(chunk)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def apply_update(info: UpdateInfo) -> None:
    """Baixa e aplica a atualização. Encerra o processo atual ao final.

    Só funciona em modo congelado (executável). Em dev, levanta RuntimeError.
    """
    if not _is_frozen():
        raise RuntimeError("apply_update só funciona com o app empacotado (.exe).")

    current = _exe_path()
    new_path = current.with_name(current.stem + f"-{info.version}.new.exe")
    log.info("Baixando update %s -> %s", info.version, new_path)
    _download(info.url, new_path)

    if info.sha256:
        digest = _sha256(new_path)
        if digest.lower() != info.sha256.lower():
            new_path.unlink(missing_ok=True)
            raise RuntimeError(f"Hash sha256 não confere. esperado={info.sha256} obtido={digest}")

    # Cria um .bat que: aguarda o app sair, substitui o exe e relança.
    bat = tempfile.NamedTemporaryFile(
        prefix="ait_update_", suffix=".bat", delete=False, mode="w", encoding="utf-8"
    )
    bat_path = Path(bat.name)
    pid = os.getpid()
    script = f"""@echo off
setlocal
echo Aguardando AI Trader fechar...
:wait
tasklist /FI "PID eq {pid}" 2>NUL | find "{pid}" >NUL
if "%ERRORLEVEL%"=="0" (
    timeout /t 1 /nobreak >NUL
    goto wait
)
echo Aplicando atualizacao...
move /Y "{new_path}" "{current}" >NUL
if errorlevel 1 (
    echo Falha ao substituir o executavel.
    pause
    exit /b 1
)
start "" "{current}"
del "%~f0"
"""
    bat.write(script)
    bat.close()

    log.info("Disparando script de troca: %s", bat_path)
    # CREATE_NEW_PROCESS_GROUP + DETACHED_PROCESS para sobreviver ao fim deste processo
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    subprocess.Popen(
        ["cmd", "/c", str(bat_path)],
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    # Sai imediatamente para liberar o .exe
    sys.exit(0)


def check_and_get_update(manifest_url: str) -> Optional[UpdateInfo]:
    """Retorna UpdateInfo se houver versão maior. None caso contrário."""
    info = fetch_manifest(manifest_url)
    if info and has_update(info):
        return info
    return None
