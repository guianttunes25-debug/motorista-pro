"""Treino offline da IA com histórico real.

Uso:
    python scripts\train_offline.py [--epochs N] [--reset]

O que faz:
    1. Cria um LearningEngine com state isolado em data/learning_offline.json
    2. Roda backtest em N pares/timeframes, cada trade alimenta o cérebro
    3. A cada época, repete os mesmos cenários — agora com IA já calibrada
    4. Mostra evolução: WR/PF/PnL antes vs depois do aprendizado
    5. Ao final, opcionalmente promove para data/learning.json (live/paper)
"""
from __future__ import annotations

import argparse
import shutil
from copy import deepcopy
from pathlib import Path

from exchange.binance import BinanceClient
from core.backtest import Backtester, BacktestConfig
from core.learning import LearningEngine, LearningConfig
from core.trader_agent import AgentConfig, SeniorTraderAgent
from core.strategy import ScoringStrategy, StrategyConfig

CENARIOS = [
    ("DOGE/USDT", "1h", 30 * 24),
    ("BTC/USDT",  "1h", 30 * 24),
    ("ETH/USDT",  "1h", 30 * 24),
    ("SOL/USDT",  "1h", 30 * 24),
    ("XRP/USDT",  "1h", 30 * 24),
    ("AVAX/USDT", "1h", 30 * 24),
]

OFFLINE_PATH = Path("data/learning_offline.json")
LIVE_PATH = Path("data/learning.json")


def run_epoch(epoch: int, learning: LearningEngine) -> dict:
    """Roda todos cenários uma vez. Retorna métricas agregadas."""
    client = BinanceClient()
    totals = {"trades": 0, "wins": 0, "pnl_pct_sum": 0.0, "pf_sum": 0.0, "n": 0}

    print(f"\n=== Época {epoch} ===")
    print(f"{'Par':12s} {'TF':5s} {'Trades':>7s} {'Win%':>6s} {'PF':>6s} {'PnL%':>8s}")
    print("-" * 55)

    for sym, tf, lim in CENARIOS:
        # Cria agente novo a cada cenário (mas o learning é compartilhado).
        agent = SeniorTraderAgent(AgentConfig(cooldown_seconds=0))
        bt = Backtester(
            client=client,
            strategy=ScoringStrategy(StrategyConfig()),
            agent=agent,
            learning=learning,
        )
        try:
            r = bt.run(BacktestConfig(
                symbol=sym, timeframe=tf, limit=lim,
                initial_balance=100.0, trade_size_pct=95.0,
                fee_pct=0.1, slippage_pct=0.05,
            ))
            print(f"{sym:12s} {tf:5s} {r.total_trades:>7d} {r.win_rate:>5.1f}% "
                  f"{r.profit_factor:>6.2f} {r.total_pnl_pct:>+7.2f}%")
            totals["trades"] += r.total_trades
            totals["wins"] += r.wins
            totals["pnl_pct_sum"] += r.total_pnl_pct
            totals["pf_sum"] += r.profit_factor
            totals["n"] += 1
        except Exception as e:
            print(f"{sym:12s} {tf:5s}  ERRO: {e}")

    if totals["n"] > 0:
        wr = (totals["wins"] / totals["trades"] * 100.0) if totals["trades"] else 0.0
        print("-" * 55)
        print(f"{'AGREGADO':12s} {'':5s} {totals['trades']:>7d} {wr:>5.1f}% "
              f"{totals['pf_sum'] / totals['n']:>6.2f} "
              f"{totals['pnl_pct_sum'] / totals['n']:>+7.2f}%")
    return totals


def main() -> None:
    p = argparse.ArgumentParser(description="Treino offline da IA")
    p.add_argument("--epochs", type=int, default=3, help="Número de passadas (default 3)")
    p.add_argument("--reset", action="store_true", help="Apaga aprendizado anterior")
    p.add_argument("--promote", action="store_true",
                   help="Copia learning_offline.json → learning.json ao final")
    args = p.parse_args()

    OFFLINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if args.reset and OFFLINE_PATH.exists():
        OFFLINE_PATH.unlink()
        print(f"🧹 Reset: {OFFLINE_PATH} apagado.")

    cfg = LearningConfig(
        persist_path=OFFLINE_PATH,
        min_trades_to_adjust=3,
        review_every_n_trades=1,
    )
    learning = LearningEngine(cfg)

    print(f"🧠 LearningEngine carregado de {OFFLINE_PATH}")
    print(f"   Estado inicial: trades={learning.state.total_trades} "
          f"wins={learning.state.total_wins} "
          f"q_mult={learning.state.quality_multiplier:.2f} "
          f"sl_mult={learning.state.stop_loss_multiplier:.2f} "
          f"tp_mult={learning.state.take_profit_multiplier:.2f}")

    history = []
    for ep in range(1, args.epochs + 1):
        totals = run_epoch(ep, learning)
        history.append(totals)

    print("\n=== Evolução por época ===")
    print(f"{'Época':>6s} {'PnL Médio%':>12s} {'PF Médio':>10s} {'Trades':>8s}")
    for i, h in enumerate(history, 1):
        if h["n"]:
            print(f"{i:>6d} {h['pnl_pct_sum']/h['n']:>+11.2f}% "
                  f"{h['pf_sum']/h['n']:>10.2f} {h['trades']:>8d}")

    print(f"\n🧠 Estado final: q_mult={learning.state.quality_multiplier:.2f} "
          f"sl_mult={learning.state.stop_loss_multiplier:.2f} "
          f"tp_mult={learning.state.take_profit_multiplier:.2f} "
          f"size_mult={learning.state.size_multiplier:.2f}")
    if learning.state.last_adjustments:
        print("\nÚltimos ajustes da IA:")
        for msg in learning.state.last_adjustments[-10:]:
            print(f"  • {msg}")

    if args.promote:
        shutil.copy2(OFFLINE_PATH, LIVE_PATH)
        print(f"\n✅ Aprendizado promovido para {LIVE_PATH} (será usado em paper/live).")
    else:
        print(f"\nℹ️  Para usar este aprendizado em paper/live, rode com --promote")


if __name__ == "__main__":
    main()
