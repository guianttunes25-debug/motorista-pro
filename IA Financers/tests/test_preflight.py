"""Testes do pré-flight checklist."""
from __future__ import annotations

from core.preflight import (
    has_blockers,
    has_warnings,
    run_preflight,
)


def _good_cfg() -> dict:
    return {
        "mode": "live",
        "broker": {"api_key": "K", "api_secret": "S", "use_testnet": False},
        "trade_size_pct": 5.0,
        "max_daily_loss_pct": 2.0,
        "initial_balance_usdt": 100.0,
        "require_live_confirmation": True,
        "agent": {"enabled": True, "min_setup_quality": 60},
    }


def test_config_bom_nao_tem_bloqueadores():
    items = run_preflight(_good_cfg())
    assert not has_blockers(items)


def test_falta_api_keys_eh_fatal():
    cfg = _good_cfg()
    cfg["broker"] = {"use_testnet": False}
    items = run_preflight(cfg)
    assert has_blockers(items)


def test_testnet_ligado_eh_warn_nao_fatal():
    cfg = _good_cfg()
    cfg["broker"]["use_testnet"] = True
    items = run_preflight(cfg)
    assert not has_blockers(items)
    assert has_warnings(items)


def test_trade_size_alto_eh_warn():
    cfg = _good_cfg()
    cfg["trade_size_pct"] = 30.0
    items = run_preflight(cfg)
    assert not has_blockers(items)
    assert has_warnings(items)


def test_perda_diaria_alta_eh_warn():
    cfg = _good_cfg()
    cfg["max_daily_loss_pct"] = 10.0
    items = run_preflight(cfg)
    assert has_warnings(items)


def test_saldo_inicial_alto_eh_warn():
    cfg = _good_cfg()
    cfg["initial_balance_usdt"] = 5000.0
    items = run_preflight(cfg)
    assert has_warnings(items)


def test_confirmacao_desligada_eh_warn():
    cfg = _good_cfg()
    cfg["require_live_confirmation"] = False
    items = run_preflight(cfg)
    assert has_warnings(items)


def test_agent_quality_baixa_eh_warn():
    cfg = _good_cfg()
    cfg["agent"]["min_setup_quality"] = 30
    items = run_preflight(cfg)
    assert has_warnings(items)


def test_modo_simulation_eh_warn_nao_fatal():
    cfg = _good_cfg()
    cfg["mode"] = "simulation"
    items = run_preflight(cfg)
    assert not has_blockers(items)
    assert has_warnings(items)


def test_keys_no_root_legacy_funciona():
    """Suporta config legado sem bloco broker."""
    cfg = {
        "mode": "live",
        "api_key": "K", "api_secret": "S",
        "use_testnet": False,
        "trade_size_pct": 5.0,
        "max_daily_loss_pct": 2.0,
        "initial_balance_usdt": 100.0,
        "require_live_confirmation": True,
    }
    items = run_preflight(cfg)
    assert not has_blockers(items)


def test_lista_completa_tem_todos_os_checks():
    items = run_preflight({})
    titles = [it.title for it in items]
    assert "Modo configurado" in titles
    assert "API Keys" in titles
    assert "Testnet desligado" in titles
    assert "Trava de Emergência" in titles
