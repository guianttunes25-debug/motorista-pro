"""Pré-flight checklist: valida o config antes de liberar modo LIVE.

Roda uma série de verificações de segurança e devolve uma lista de itens:

    [{"ok": True/False, "level": "fatal"|"warn"|"info", "title": "...", "msg": "..."}, ...]

`level`:
    - "fatal": bloqueia ativação de live
    - "warn":  permite ativar mas alerta
    - "info":  apenas informativo

A UI exibe a lista num diálogo e só libera o "Ativar IA Live" se NENHUM
item for fatal.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Level = Literal["fatal", "warn", "info"]


@dataclass
class CheckItem:
    ok: bool
    level: Level
    title: str
    msg: str

    def icon(self) -> str:
        if self.ok:
            return "✅"
        if self.level == "fatal":
            return "❌"
        if self.level == "warn":
            return "⚠️"
        return "ℹ️"


# ---------- Verificações individuais ----------

def _check_mode(cfg: dict) -> CheckItem:
    mode = cfg.get("mode", "simulation")
    if mode == "live":
        return CheckItem(True, "info", "Modo configurado",
                         "config.json está em mode=live.")
    return CheckItem(False, "warn", "Modo configurado",
                     f"config.json está em mode={mode!r} — você pode operar, "
                     "mas o app subiu em modo simulação.")


def _check_api_keys(cfg: dict) -> CheckItem:
    block = cfg.get("broker") or {}
    has_key = bool(block.get("api_key") or cfg.get("api_key"))
    has_sec = bool(block.get("api_secret") or cfg.get("api_secret"))
    if has_key and has_sec:
        return CheckItem(True, "fatal", "API Keys",
                         "API key e secret estão configurados.")
    return CheckItem(False, "fatal", "API Keys",
                     "Faltam api_key ou api_secret no config. "
                     "Sem isso, ordens reais NÃO podem ser enviadas.")


def _check_testnet_off(cfg: dict) -> CheckItem:
    block = cfg.get("broker") or {}
    use_testnet = bool(block.get("use_testnet", cfg.get("use_testnet", True)))
    if not use_testnet:
        return CheckItem(True, "warn", "Testnet desligado",
                         "use_testnet=false → ordens vão para a corretora REAL.")
    return CheckItem(False, "warn", "Testnet desligado",
                     "use_testnet=true → você ainda está em testnet (dinheiro fake). "
                     "Para operar real, mude use_testnet para false.")


def _check_trade_size(cfg: dict) -> CheckItem:
    pct = float(cfg.get("trade_size_pct", 10.0))
    # Regra do veterano: 1-2% por trade. Acima disso = amador.
    if pct <= 2.0:
        return CheckItem(True, "info", "Tamanho por ordem",
                         f"trade_size_pct={pct}% — regra dos profissionais (1-2%). ✓")
    if pct <= 5.0:
        return CheckItem(True, "warn", "Tamanho por ordem",
                         f"trade_size_pct={pct}% — aceitável, mas o ideal é 1-2%.")
    if pct <= 15.0:
        return CheckItem(False, "warn", "Tamanho por ordem",
                         f"trade_size_pct={pct}% é ALTO. Veterano arrisca 1-2% por trade. "
                         "Reduza antes de operar a sério.")
    return CheckItem(False, "warn", "Tamanho por ordem",
                     f"trade_size_pct={pct}% é MUITO ALTO — comportamento de cassino. "
                     "Reduza para 1-2% imediatamente. Esse é O fator que separa "
                     "trader de apostador.")


def _check_max_daily_loss(cfg: dict) -> CheckItem:
    pct = float(cfg.get("max_daily_loss_pct", 2.0))
    if pct <= 2.0:
        return CheckItem(True, "info", "Perda diária máxima",
                         f"max_daily_loss_pct={pct}% — kill switch razoável. ✓")
    if pct <= 5.0:
        return CheckItem(True, "warn", "Perda diária máxima",
                         f"max_daily_loss_pct={pct}% — tolerância média.")
    return CheckItem(False, "warn", "Perda diária máxima",
                     f"max_daily_loss_pct={pct}% é alto. "
                     "Recomendado ≤2% para começar.")


def _check_initial_balance(cfg: dict) -> CheckItem:
    bal = float(cfg.get("initial_balance_usdt", 1000.0))
    if bal <= 100.0:
        return CheckItem(True, "info", "Saldo inicial",
                         f"initial_balance_usdt={bal} — pequeno, ideal para testar real.")
    if bal <= 500.0:
        return CheckItem(True, "warn", "Saldo inicial",
                         f"initial_balance_usdt={bal} — moderado.")
    return CheckItem(False, "warn", "Saldo inicial",
                     f"initial_balance_usdt={bal} é alto para um primeiro live. "
                     "Considere começar com ≤100 USDT.")


def _check_confirmation(cfg: dict) -> CheckItem:
    req = bool(cfg.get("require_live_confirmation", True))
    if req:
        return CheckItem(True, "info", "Confirmação ao ativar IA",
                         "Diálogo de confirmação está ATIVO. ✓")
    return CheckItem(False, "warn", "Confirmação ao ativar IA",
                     "require_live_confirmation=false — IA vai ativar sem perguntar. "
                     "Recomendado deixar true.")


def _check_kill_switch(cfg: dict) -> CheckItem:
    return CheckItem(True, "info", "Trava de Emergência",
                     "Botão ⛔ TRAVA DE EMERGÊNCIA disponível na UI a qualquer momento.")


def _check_agent_enabled(cfg: dict) -> CheckItem:
    agent = cfg.get("agent") or {}
    if agent.get("enabled", True):
        q = int(agent.get("min_setup_quality", 60))
        if q < 50:
            return CheckItem(False, "warn", "Agente IA",
                             f"min_setup_quality={q}% baixo — IA pode entrar em "
                             "setups fracos. Recomendado ≥60%.")
        return CheckItem(True, "info", "Agente IA",
                         f"Agente sênior ativo (min_setup_quality={q}%). ✓")
    return CheckItem(True, "info", "Agente IA",
                     "Agente sênior desativado — apenas a strategy básica decide.")


# ---------- Orquestração ----------

CHECKS = (
    _check_mode,
    _check_api_keys,
    _check_testnet_off,
    _check_trade_size,
    _check_max_daily_loss,
    _check_initial_balance,
    _check_confirmation,
    _check_agent_enabled,
    _check_kill_switch,
)


def run_preflight(cfg: dict) -> list[CheckItem]:
    """Roda todos os checks e devolve a lista."""
    return [check(cfg) for check in CHECKS]


def has_blockers(items: list[CheckItem]) -> bool:
    """True se há algum item fatal não-ok (bloqueia ativar live)."""
    return any((not it.ok) and it.level == "fatal" for it in items)


def has_warnings(items: list[CheckItem]) -> bool:
    return any((not it.ok) and it.level == "warn" for it in items)
