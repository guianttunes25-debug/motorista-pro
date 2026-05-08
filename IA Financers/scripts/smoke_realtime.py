"""Smoke test real-time: roda 5 ticks contra a Binance pública."""
import json
import time
from pathlib import Path

from core.market import MarketDataService
from core.strategy import ScoringStrategy, StrategyConfig
from core.trader_agent import AgentConfig, SeniorTraderAgent
from exchange.binance import BinanceClient, ExchangeConfig

cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
client = BinanceClient(ExchangeConfig(use_testnet=False))
market = MarketDataService(client)
strategy = ScoringStrategy(StrategyConfig(
    rsi_buy=cfg["rsi_buy"], rsi_sell=cfg["rsi_sell"],
    score_buy_threshold=cfg["score_buy_threshold"],
    score_sell_threshold=cfg["score_sell_threshold"],
))
ag = cfg["agent"]
agent = SeniorTraderAgent(AgentConfig(
    extreme_vol_pct=ag["extreme_vol_pct"],
    calm_vol_pct=ag["calm_vol_pct"],
    trend_strength_min_pct=ag["trend_strength_min_pct"],
    min_setup_quality=ag["min_setup_quality"],
    cooldown_seconds=0,
    stop_loss_pct=ag["stop_loss_pct"],
    take_profit_pct=ag["take_profit_pct"],
))

print("=== TESTE REAL-TIME (BTC/USDT 1m) ===")
print(f"Filtros: vol_extrema>{ag['extreme_vol_pct']}%  min_quality>={ag['min_setup_quality']}")
print("-" * 90)
for i in range(5):
    snap = market.fetch_snapshot("BTC/USDT", "1m", 14, 9, 21, 12, 26, 9)
    raw = strategy.decide(snap, news_score=0)
    dec = agent.evaluate(snap, raw, has_position=False, news_score=0)
    print(
        f"[{i+1}] price={snap.price:>10.2f} | RSI={snap.rsi:5.1f} | "
        f"MACDh={snap.macd_hist:+.3f} | vol={snap.volatility_pct:.3f}% | "
        f"ch24h={snap.change_pct_24h:+.2f}% | volQ24h={snap.quote_volume_24h:,.0f}"
    )
    print(
        f"    strategy: {raw.signal:<4} score={raw.score:+d}  |  "
        f"agent: {dec.signal:<4} regime={dec.regime} q={dec.quality}/100 sent={dec.sentiment:+d}"
    )
    reasons = " | ".join(dec.reasons[:4])
    print(f"    razões: {reasons}")
    if i < 4:
        time.sleep(8)
print("-" * 90)
print("OK — pipeline real-time funcionando.")
