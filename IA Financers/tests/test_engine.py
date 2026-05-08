"""Testes da lógica de portfolio do EngineWorker (sem rodar o thread Qt).

Testa _execute_trade diretamente: contabilidade do caixa, posição e PnL.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from core.engine import EngineConfig, EngineWorker, Portfolio


def _make_worker(initial=1000.0):
    cfg = EngineConfig(initial_balance_usdt=initial)
    w = EngineWorker(
        cfg=cfg,
        client=MagicMock(),
        market=MagicMock(),
        strategy=MagicMock(),
        risk=MagicMock(),
        news=MagicMock(),
        agent=None,
    )
    return w


def test_buy_updates_cash_and_position():
    w = _make_worker(initial=1000.0)
    w._execute_trade("BUY", price=100.0, amount=2.0)
    assert w.portfolio.base_amount == 2.0
    assert abs(w.portfolio.cash_usdt - 800.0) < 1e-9
    assert w.portfolio.avg_entry == 100.0


def test_buy_caps_to_available_cash():
    w = _make_worker(initial=100.0)
    w._execute_trade("BUY", price=50.0, amount=10.0)  # quer 500 mas só tem 100
    assert abs(w.portfolio.base_amount - 2.0) < 1e-9
    assert abs(w.portfolio.cash_usdt) < 1e-9


def test_buy_with_zero_cash_does_nothing():
    w = _make_worker(initial=0.0)
    w._execute_trade("BUY", price=100.0, amount=1.0)
    assert w.portfolio.base_amount == 0.0
    assert w.portfolio.cash_usdt == 0.0


def test_sell_realizes_pnl():
    w = _make_worker(initial=1000.0)
    w._execute_trade("BUY", price=100.0, amount=2.0)
    w._execute_trade("SELL", price=110.0, amount=2.0)
    assert w.portfolio.base_amount == 0.0
    # vendeu 2 @ 110 → 220 USDT proceeds, caixa = 800 + 220 = 1020
    assert abs(w.portfolio.cash_usdt - 1020.0) < 1e-9
    last = w.portfolio.trades[-1]
    assert abs(last.pnl - 20.0) < 1e-9
    assert w.portfolio.avg_entry == 0.0  # zerado ao fechar tudo


def test_sell_partial_keeps_avg_entry():
    w = _make_worker(initial=1000.0)
    w._execute_trade("BUY", price=100.0, amount=2.0)
    w._execute_trade("SELL", price=110.0, amount=1.0)
    assert abs(w.portfolio.base_amount - 1.0) < 1e-9
    assert w.portfolio.avg_entry == 100.0  # mantém preço médio


def test_sell_more_than_position_caps():
    w = _make_worker()
    w._execute_trade("BUY", price=100.0, amount=1.0)
    w._execute_trade("SELL", price=110.0, amount=5.0)  # tenta vender mais que tem
    assert w.portfolio.base_amount == 0.0


def test_sell_without_position_noop():
    w = _make_worker()
    before_cash = w.portfolio.cash_usdt
    w._execute_trade("SELL", price=100.0, amount=1.0)
    assert w.portfolio.cash_usdt == before_cash
    # nenhum trade emitido
    assert len(w.portfolio.trades) == 0


def test_avg_entry_correct_after_two_buys():
    w = _make_worker(initial=10000.0)
    w._execute_trade("BUY", price=100.0, amount=1.0)  # avg=100
    w._execute_trade("BUY", price=200.0, amount=1.0)  # avg=(100+200)/2=150
    assert abs(w.portfolio.avg_entry - 150.0) < 1e-9
    assert w.portfolio.base_amount == 2.0


def test_portfolio_equity():
    p = Portfolio(cash_usdt=500.0, base_amount=2.0)
    assert p.equity(price=100.0) == 700.0
    assert p.position_value(price=100.0) == 200.0
