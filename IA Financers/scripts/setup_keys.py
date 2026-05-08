"""Setup inicial — salva API keys uma única vez no Windows Keyring.

Uso:
  python scripts/setup_keys.py

Pede para digitar:
  - Binance API Key (SIMULAÇÃO/Testnet)
  - Binance API Secret (SIMULAÇÃO/Testnet)
  - Binance API Key (REAL)
  - Binance API Secret (REAL)

Salva tudo no Keyring e depois nunca mais pede!
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.secrets import set_secret


def main() -> int:
    print("\n" + "="*70)
    print("🔐 SETUP INICIAL — Salvar API Keys")
    print("="*70)
    print("""
Você vai digitar suas chaves da Binance UMA ÚNICA VEZ.
Depois ficam salvas no Windows Credential Manager (criptografadas).

⚠️  IMPORTANTE:
  - Nunca exponha suas chaves! Este script as protege.
  - Use chaves com permissão LEITURA apenas (para segurança).
  - Se quiser, crie 2 chaves: uma para SIM (testnet) outra para REAL.
""")

    # Binance SIM (Testnet)
    print("\n" + "-"*70)
    print("1️⃣  BINANCE SIMULAÇÃO (Testnet com dinheiro fake)")
    print("-"*70)
    api_key_sim = getpass.getpass("   API Key (Simulação): ").strip()
    if not api_key_sim:
        print("   ❌ Cancelado — API Key vazia.")
        return 1
    api_secret_sim = getpass.getpass("   API Secret (Simulação): ").strip()
    if not api_secret_sim:
        print("   ❌ Cancelado — API Secret vazio.")
        return 1

    # Binance REAL
    print("\n" + "-"*70)
    print("2️⃣  BINANCE REAL (Com seu dinheiro verdadeiro ⚠️)")
    print("-"*70)
    api_key_real = getpass.getpass("   API Key (REAL): ").strip()
    if not api_key_real:
        print("   ❌ Cancelado — API Key vazia.")
        return 1
    api_secret_real = getpass.getpass("   API Secret (REAL): ").strip()
    if not api_secret_real:
        print("   ❌ Cancelado — API Secret vazio.")
        return 1

    # Confirmar antes de salvar
    print("\n" + "="*70)
    print("⚠️  CONFIRMAÇÃO")
    print("="*70)
    print(f"""
  Simulação (Testnet):
    - Key: {api_key_sim[:10]}{'...' if len(api_key_sim) > 10 else ''}
    - Secret: {api_secret_sim[:10]}{'...' if len(api_secret_sim) > 10 else ''}

  REAL (Dinheiro verdadeiro):
    - Key: {api_key_real[:10]}{'...' if len(api_key_real) > 10 else ''}
    - Secret: {api_secret_real[:10]}{'...' if len(api_secret_real) > 10 else ''}
""")
    confirm = input("Salvar essas chaves? (s/n): ").strip().lower()
    if confirm != "s":
        print("❌ Cancelado pelo usuário.")
        return 1

    # Salvar no Keyring
    print("\n💾 Salvando no Windows Credential Manager...")
    ok = True
    try:
        ok &= set_secret("binance_api_key_sim", api_key_sim)
        ok &= set_secret("binance_api_secret_sim", api_secret_sim)
        ok &= set_secret("broker_api_key_sim", api_key_sim)
        ok &= set_secret("broker_api_secret_sim", api_secret_sim)
        
        ok &= set_secret("binance_api_key_real", api_key_real)
        ok &= set_secret("binance_api_secret_real", api_secret_real)
        ok &= set_secret("broker_api_key_real", api_key_real)
        ok &= set_secret("broker_api_secret_real", api_secret_real)
        
        if not ok:
            print("❌ Erro ao salvar algumas chaves no Keyring!")
            return 1
        
        print("\n" + "="*70)
        print("✅ SUCESSO! Chaves salvas no Windows Credential Manager")
        print("="*70)
        print("""
Agora você pode:
  1. Abrir AITraderCopilot.exe
  2. Escolher SIMULAÇÃO ou REAL no wizard
  3. Usar o app!

As chaves estão seguras no Windows (criptografadas pelo DPAPI).
Você nunca mais precisa digitar elas!
""")
        return 0

    except Exception as e:
        print(f"❌ Erro ao salvar no Keyring: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
