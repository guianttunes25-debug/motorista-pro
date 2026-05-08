"""Gerencia dois perfis de API key (simulacao e real) no Windows Keyring.

Uso rapido:
  python scripts/key_profiles.py save --profile sim --api-key "..." --api-secret "..."
  python scripts/key_profiles.py save --profile real --api-key "..." --api-secret "..."
  python scripts/key_profiles.py activate --profile sim --config dist/AITraderCopilot/config.json
  python scripts/key_profiles.py activate --profile real --config dist/AITraderCopilot/config.json
  python scripts/key_profiles.py status --config dist/AITraderCopilot/config.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.secrets import get_secret, set_secret  # noqa: E402

ACTIVE_KEYS = {
    "api_key": "binance_api_key",
    "api_secret": "binance_api_secret",
    "broker_api_key": "broker_api_key",
    "broker_api_secret": "broker_api_secret",
}

PROFILE_MAP = {
    "sim": {
        "api_key": "binance_api_key_sim",
        "api_secret": "binance_api_secret_sim",
        "broker_api_key": "broker_api_key_sim",
        "broker_api_secret": "broker_api_secret_sim",
        "use_testnet": True,
        "mode": "live",
    },
    "real": {
        "api_key": "binance_api_key_real",
        "api_secret": "binance_api_secret_real",
        "broker_api_key": "broker_api_key_real",
        "broker_api_secret": "broker_api_secret_real",
        "use_testnet": False,
        "mode": "live",
    },
}


def _mask(v: str) -> str:
    if not v:
        return "(vazio)"
    if len(v) <= 4:
        return "*" * len(v)
    return "*" * (len(v) - 4) + v[-4:]


def _load_cfg(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_cfg(path: Path, cfg: dict) -> None:
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def cmd_save(args: argparse.Namespace) -> int:
    p = PROFILE_MAP[args.profile]
    ok = True
    ok &= set_secret(p["api_key"], args.api_key)
    ok &= set_secret(p["api_secret"], args.api_secret)
    ok &= set_secret(p["broker_api_key"], args.api_key)
    ok &= set_secret(p["broker_api_secret"], args.api_secret)
    if not ok:
        print("Falha ao salvar no keyring.")
        return 1
    print(f"Perfil '{args.profile}' salvo no keyring com sucesso.")
    return 0


def cmd_activate(args: argparse.Namespace) -> int:
    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"Arquivo de config nao encontrado: {cfg_path}")
        return 1

    p = PROFILE_MAP[args.profile]
    k = get_secret(p["api_key"])
    s = get_secret(p["api_secret"])
    if not k or not s:
        print(
            f"Perfil '{args.profile}' sem credenciais. Rode o comando save primeiro."
        )
        return 1

    ok = True
    ok &= set_secret(ACTIVE_KEYS["api_key"], k)
    ok &= set_secret(ACTIVE_KEYS["api_secret"], s)
    ok &= set_secret(ACTIVE_KEYS["broker_api_key"], k)
    ok &= set_secret(ACTIVE_KEYS["broker_api_secret"], s)
    if not ok:
        print("Falha ao promover credenciais ativas no keyring.")
        return 1

    cfg = _load_cfg(cfg_path)
    cfg["mode"] = p["mode"]
    cfg["use_testnet"] = p["use_testnet"]
    broker = cfg.setdefault("broker", {})
    broker["use_testnet"] = p["use_testnet"]

    # Mantem placeholders para evitar segredos em arquivo.
    cfg["api_key"] = "***KEYRING***"
    cfg["api_secret"] = "***KEYRING***"
    broker["api_key"] = "***KEYRING***"
    broker["api_secret"] = "***KEYRING***"

    _save_cfg(cfg_path, cfg)

    print(f"Perfil '{args.profile}' ativado.")
    print(f"Config atualizada: {cfg_path}")
    print(f"use_testnet={p['use_testnet']} | mode={p['mode']}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"Arquivo de config nao encontrado: {cfg_path}")
        return 1
    cfg = _load_cfg(cfg_path)

    print("Status de configuracao")
    print("-" * 60)
    print(f"config      : {cfg_path}")
    print(f"mode        : {cfg.get('mode')}")
    print(f"use_testnet : {cfg.get('use_testnet')}")
    print(f"broker.test : {(cfg.get('broker') or {}).get('use_testnet')}")
    print()

    for name, mp in PROFILE_MAP.items():
        k = get_secret(mp["api_key"])
        s = get_secret(mp["api_secret"])
        state = "OK" if (k and s) else "FALTANDO"
        print(f"perfil {name:4s}: {state:8s} key={_mask(k)} secret={_mask(s)}")

    ak = get_secret(ACTIVE_KEYS["api_key"])
    asec = get_secret(ACTIVE_KEYS["api_secret"])
    print()
    print(f"ativo       : key={_mask(ak)} secret={_mask(asec)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Perfis de key para simulacao e real")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_save = sub.add_parser("save", help="Salva credenciais em um perfil")
    p_save.add_argument("--profile", choices=["sim", "real"], required=True)
    p_save.add_argument("--api-key", required=True)
    p_save.add_argument("--api-secret", required=True)
    p_save.set_defaults(func=cmd_save)

    p_activate = sub.add_parser("activate", help="Ativa perfil e ajusta config")
    p_activate.add_argument("--profile", choices=["sim", "real"], required=True)
    p_activate.add_argument("--config", required=True)
    p_activate.set_defaults(func=cmd_activate)

    p_status = sub.add_parser("status", help="Mostra status dos perfis")
    p_status.add_argument("--config", required=True)
    p_status.set_defaults(func=cmd_status)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
