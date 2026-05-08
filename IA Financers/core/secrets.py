"""Camada de segredos: API keys cifradas no Windows Credential Manager.

Usa `keyring` (cross-platform). No Windows, persiste no Credential Manager
(cifrado por DPAPI vinculado ao usuário). config.json deixa de armazenar
chave em texto plano — fica apenas um placeholder "***KEYRING***".

Migração automática: se config.json ainda tiver chave em texto, na 1ª
chamada `hydrate_config()` move pro keyring e reescreve o JSON limpo.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

SERVICE_NAME = "AITraderCopilot"
PLACEHOLDER = "***KEYRING***"

# Mapeamento campo no JSON → username no keyring
# Formato: (caminho_no_dict, username_keyring)
SECRET_FIELDS: list[tuple[tuple[str, ...], str]] = [
    (("api_key",), "binance_api_key"),
    (("api_secret",), "binance_api_secret"),
    (("broker", "api_key"), "broker_api_key"),
    (("broker", "api_secret"), "broker_api_secret"),
    (("broker", "password"), "broker_password"),
    # Perfis separados (sim = testnet, real = mainnet)
    (("api_key_sim",), "binance_api_key_sim"),
    (("api_secret_sim",), "binance_api_secret_sim"),
    (("api_key_real",), "binance_api_key_real"),
    (("api_secret_real",), "binance_api_secret_real"),
]


def _kr():
    """Lazy import — keyring pode não estar instalado em ambientes mínimos."""
    try:
        import keyring  # type: ignore
        return keyring
    except Exception as e:  # noqa: BLE001
        log.warning("keyring indisponível: %s", e)
        return None


def get_secret(username: str) -> str:
    kr = _kr()
    if kr is None:
        return ""
    try:
        return kr.get_password(SERVICE_NAME, username) or ""
    except Exception as e:  # noqa: BLE001
        log.warning("get_secret(%s) falhou: %s", username, e)
        return ""


def set_secret(username: str, value: str) -> bool:
    kr = _kr()
    if kr is None:
        return False
    try:
        if value:
            kr.set_password(SERVICE_NAME, username, value)
        else:
            try:
                kr.delete_password(SERVICE_NAME, username)
            except Exception:  # noqa: BLE001
                pass
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("set_secret(%s) falhou: %s", username, e)
        return False


def _get_nested(d: dict, path: tuple[str, ...]):
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def _set_nested(d: dict, path: tuple[str, ...], value) -> None:
    cur = d
    for k in path[:-1]:
        if k not in cur or not isinstance(cur[k], dict):
            cur[k] = {}
        cur = cur[k]
    cur[path[-1]] = value


def hydrate_config(cfg: dict) -> dict:
    """Substitui placeholders no cfg por valores reais do keyring.

    Não modifica o arquivo de config — só o dicionário em memória.
    Returns: o mesmo cfg com segredos preenchidos.
    """
    for path, username in SECRET_FIELDS:
        cur = _get_nested(cfg, path)
        if cur == PLACEHOLDER:
            real = get_secret(username)
            if real:
                _set_nested(cfg, path, real)
    return cfg


def migrate_config_to_keyring(cfg: dict, config_path: Path) -> bool:
    """Detecta segredos em texto no config.json e move pro keyring.

    Reescreve o arquivo atomicamente substituindo valores por PLACEHOLDER.
    Returns: True se houve migração (arquivo foi reescrito).
    """
    # Mapeamento extra: chaves de perfil também espelham nos slots broker_*
    _PROFILE_MIRROR: dict[str, str] = {
        "binance_api_key_sim": "broker_api_key_sim",
        "binance_api_secret_sim": "broker_api_secret_sim",
        "binance_api_key_real": "broker_api_key_real",
        "binance_api_secret_real": "broker_api_secret_real",
    }

    migrated = False
    cfg_disk = dict(cfg)  # copy raso pra ajustar
    for path, username in SECRET_FIELDS:
        cur = _get_nested(cfg_disk, path)
        if cur and cur != PLACEHOLDER and isinstance(cur, str):
            if set_secret(username, cur):
                _set_nested(cfg_disk, path, PLACEHOLDER)
                migrated = True
                log.info("Segredo migrado pro keyring: %s", username)
                # Espelha para slot broker equivalente (se houver)
                mirror = _PROFILE_MIRROR.get(username)
                if mirror:
                    set_secret(mirror, cur)
                    log.info("Espelhado pro keyring: %s", mirror)
    if migrated:
        try:
            d = config_path.parent
            d.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".cfg_", suffix=".json", dir=str(d))
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(cfg_disk, f, indent=2, ensure_ascii=False)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            os.replace(tmp, str(config_path))
        except Exception as e:  # noqa: BLE001
            log.error("Falha ao reescrever config após migração: %s", e)
            return False
    return migrated


def store_secrets_from_dict(cfg: dict) -> dict:
    """Persiste segredos do cfg no keyring e retorna cfg com placeholders.

    Usado pelo dialog de API Key: usuário digita, salvamos no keyring e
    no JSON gravamos só o placeholder.
    """
    out = dict(cfg)
    for path, username in SECRET_FIELDS:
        cur = _get_nested(out, path)
        if cur and cur != PLACEHOLDER and isinstance(cur, str):
            if set_secret(username, cur):
                _set_nested(out, path, PLACEHOLDER)
    return out
