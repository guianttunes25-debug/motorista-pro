"""Backtest comparativo de pares para banca pequena."""
from exchange.binance import BinanceClient
from core.backtest import Backtester, BacktestConfig

bt = Backtester(BinanceClient())

cenarios = [
    ('DOGE/USDT', '5m',  14*24*12),
    ('DOGE/USDT', '15m', 14*24*4),
    ('DOGE/USDT', '1h',  30*24),
    ('BTC/USDT',  '15m', 14*24*4),
    ('BTC/USDT',  '1h',  30*24),
    ('ETH/USDT',  '1h',  30*24),
    ('SOL/USDT',  '15m', 14*24*4),
    ('SOL/USDT',  '1h',  30*24),
    ('XRP/USDT',  '1h',  30*24),
    ('AVAX/USDT', '1h',  30*24),
]

PAR = "Par"; TF = "TF"; TRADES = "Trades"; WIN = "Win%"; PF = "PF"; PNL = "PnL%"; DD = "DD%"
print(f"{PAR:12s} {TF:5s} {TRADES:>7s} {WIN:>6s} {PF:>6s} {PNL:>8s} {DD:>6s}")
print('-' * 60)
for sym, tf, lim in cenarios:
    try:
        r = bt.run(BacktestConfig(
            symbol=sym, timeframe=tf, limit=lim,
            initial_balance=100.0, trade_size_pct=95.0,
            fee_pct=0.1, slippage_pct=0.05,
        ))
        pnl_pct = r.total_pnl_pct
        wr = r.win_rate
        pf = r.profit_factor
        print(f"{sym:12s} {tf:5s} {r.total_trades:>7d} {wr:>5.1f}% {pf:>6.2f} {pnl_pct:>+7.2f}% {r.max_drawdown_pct:>5.2f}%")
    except Exception as e:
        print(f"{sym:12s} {tf:5s} ERRO: {e}")
