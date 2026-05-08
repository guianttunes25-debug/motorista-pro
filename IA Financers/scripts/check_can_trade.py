"""Diagnóstico: a IA pode comprar/vender? Tenho USDT pra trocar por outras moedas?

Uso:
    python scripts\check_can_trade.py [--symbol BTC/USDT]

Faz:
    1. Lê config.json (mode/use_testnet/symbol/api_keys do keyring)
    2. Conecta na Binance
    3. Mostra saldo real (todas moedas com saldo > 0)
    4. Verifica permissões da API key (spot trading?)
    5. Mostra min_notional / step_size de pares /USDT populares
    6. Diz claramente: pode operar? quais pares? quanto mínimo?
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.secrets import hydrate_config
from exchange.binance import BinanceClient, ExchangeConfig


def _fmt(v: float, decimals: int = 8) -> str:
    return f"{v:,.{decimals}f}".rstrip("0").rstrip(".")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default=None, help="Par para checar detalhes (ex: BTC/USDT)")
    p.add_argument("--config", default="config.json")
    args = p.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        cfg_path = ROOT / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    mode = cfg.get("mode", "simulation")
    use_testnet = bool(cfg.get("use_testnet", True))
    symbol = args.symbol or cfg.get("symbol", "BTC/USDT")

    print("=" * 70)
    print("🔍 DIAGNÓSTICO — A IA PODE OPERAR?")
    print("=" * 70)
    print(f"Modo configurado : {mode.upper()}")
    print(f"Testnet ativo    : {'SIM (sandbox, dinheiro fake)' if use_testnet else 'NÃO (Binance REAL)'}")
    print(f"Par configurado  : {symbol}")
    print()

    # 1) Credenciais
    cfg = hydrate_config(cfg)
    api_key = (cfg.get("broker") or {}).get("api_key") or cfg.get("api_key") or ""
    api_secret = (cfg.get("broker") or {}).get("api_secret") or cfg.get("api_secret") or ""
    if not api_key or not api_secret or api_key == "***KEYRING***":
        print("❌ API KEY/SECRET ausentes. Configure pelo botão 'Configurar API Key' no app.")
        print("   Sem keys, a IA NÃO PODE operar — só pode fazer simulação interna.")
        return
    print(f"✅ API key carregada (***{api_key[-4:]})")
    print()

    # 2) Conexão
    client = BinanceClient(ExchangeConfig(
        api_key=api_key, api_secret=api_secret, use_testnet=use_testnet,
    ))
    try:
        bal = client.get_balance()
    except Exception as e:  # noqa: BLE001
        print(f"❌ Falha ao conectar/autenticar: {e}")
        print("   Possíveis causas: keys inválidas, IP bloqueado, ou permissão de spot desativada.")
        return
    print("✅ Conectado e autenticado na Binance.")
    print()

    # 3) Saldos
    totals = bal.get("total", {}) or {}
    free = bal.get("free", {}) or {}
    nonzero = {k: float(v) for k, v in totals.items() if float(v) > 0}
    if not nonzero:
        print("⚠️  Conta sem saldo. Deposite alguma moeda antes de operar.")
    else:
        print("💰 Saldos atuais (com posição):")
        print(f"   {'Moeda':10s} {'Total':>18s} {'Livre':>18s}")
        for coin, total in sorted(nonzero.items(), key=lambda x: -x[1]):
            print(f"   {coin:10s} {_fmt(total):>18s} {_fmt(float(free.get(coin, 0))):>18s}")
    print()

    # 4) Permissões (Binance retorna em info)
    info = bal.get("info", {}) or {}
    perms = info.get("permissions") or info.get("accountType")
    can_trade = info.get("canTrade")
    print(f"🔑 Permissões da API key:")
    print(f"   canTrade   : {can_trade if can_trade is not None else '(não reportado)'}")
    print(f"   accountType: {info.get('accountType', '?')}")
    print(f"   permissions: {perms}")
    if can_trade is False:
        print("   ❌ A KEY NÃO TEM PERMISSÃO DE SPOT TRADING.")
        print("   Vá em Binance > API Management > edite a key > marque 'Enable Spot Trading'.")
    print()

    # 5) Detalhes do par + outras opções USDT
    try:
        markets = client.client.load_markets()
    except Exception as e:  # noqa: BLE001
        print(f"⚠️  Não foi possível carregar markets: {e}")
        markets = {}

    usdt_free = float(free.get("USDT", 0))
    print(f"💵 USDT livre para comprar: {_fmt(usdt_free)} USDT")
    print()

    if symbol in markets:
        m = markets[symbol]
        limits = m.get("limits", {}) or {}
        cost_min = (limits.get("cost") or {}).get("min")
        amount_min = (limits.get("amount") or {}).get("min")
        print(f"📊 Detalhes de {symbol}:")
        print(f"   Mínimo por ordem (cost) : {cost_min} USDT")
        print(f"   Mínimo de quantidade    : {amount_min}")
        if cost_min and usdt_free > 0:
            if usdt_free >= float(cost_min):
                print(f"   ✅ Você TEM USDT suficiente para abrir ordem mínima neste par.")
            else:
                print(f"   ❌ USDT INSUFICIENTE: precisa ≥ {cost_min}, você tem {_fmt(usdt_free)}.")
    else:
        print(f"⚠️  Par {symbol} não encontrado nos markets.")
    print()

    # 6) Lista pares /USDT possíveis dado o saldo
    if usdt_free > 0:
        print("🪙 Pares /USDT que você consegue comprar AGORA com seu USDT livre:")
        viable = []
        for sym, m in markets.items():
            if not sym.endswith("/USDT") or not m.get("active"):
                continue
            cmin = ((m.get("limits") or {}).get("cost") or {}).get("min")
            if cmin is None:
                continue
            try:
                cmin = float(cmin)
            except (TypeError, ValueError):
                continue
            if cmin <= usdt_free:
                viable.append((sym, cmin))
        viable.sort(key=lambda x: x[1])
        if not viable:
            print("   ❌ Nenhum par tem mínimo abaixo do seu saldo. Você precisa depositar mais USDT.")
        else:
            print(f"   {'Par':18s} {'Mín ordem':>12s}")
            for sym, cmin in viable[:25]:
                print(f"   {sym:18s} {cmin:>12.2f} USDT")
            if len(viable) > 25:
                print(f"   ... e mais {len(viable) - 25} pares.")
    print()

    # 7) Veredito
    print("=" * 70)
    print("🎯 VEREDITO:")
    print("=" * 70)
    issues = []
    if mode != "live":
        issues.append(f"Modo está '{mode}' — IA não envia ordens reais. Mude para 'live' no config.")
    if can_trade is False:
        issues.append("API key sem permissão de Spot Trading.")
    if usdt_free <= 0 and not nonzero:
        issues.append("Sem saldo nenhum.")
    if usdt_free <= 0 and nonzero:
        issues.append("Você tem cripto mas zero USDT — converta um pouco para USDT antes.")
    if not issues:
        print("✅ TUDO OK — A IA PODE COMPRAR E VENDER.")
        print(f"   Você tem {_fmt(usdt_free)} USDT livre para trocar por qualquer par /USDT acima.")
        if use_testnet:
            print("   ⚠️  Mas está em TESTNET — operações são em sandbox (não dinheiro real).")
    else:
        print("⚠️  PROBLEMAS DETECTADOS:")
        for i, msg in enumerate(issues, 1):
            print(f"   {i}. {msg}")


if __name__ == "__main__":
    main()
