"""Backtesting — roda a estratégia + trader sênior em dados históricos.

Simula tick a tick: cada candle vira um snapshot, agent decide, executa
em carteira fictícia, aplica SL/TP, registra trades. No final calcula
métricas-chave (PnL, win-rate, drawdown, Sharpe simplificado).

Uso:
    from core.backtest import Backtester, BacktestConfig
    from exchange.binance import BinanceClient
    bt = Backtester(BinanceClient())
    result = bt.run(BacktestConfig(symbol="BTC/USDT", timeframe="1h", limit=720))
    print(result.summary())
"""
from __future__ import annotations

import math
import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import MACD, SMAIndicator, ADXIndicator

from core.market import MarketSnapshot
from core.strategy import ScoringStrategy, StrategyConfig
from core.trader_agent import AgentConfig, SeniorTraderAgent
from exchange.base import BrokerClient


@dataclass
class BacktestConfig:
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    limit: int = 720          # 720 candles 1h ≈ 30 dias
    initial_balance: float = 1000.0
    trade_size_pct: float = 10.0
    fee_pct: float = 0.1      # 0.1% taxa Binance spot
    slippage_pct: float = 0.05  # slippage simulado
    rsi_period: int = 14
    sma_fast: int = 9
    sma_slow: int = 21
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    warmup: int = 30          # candles iniciais ignorados (indicadores ainda NaN)


@dataclass
class BacktestTrade:
    entry_idx: int
    exit_idx: int
    entry_price: float
    exit_price: float
    amount: float
    pnl: float
    pnl_pct: float
    exit_reason: str          # "TP", "SL", "SELL_signal", "EOD"
    duration_bars: int


@dataclass
class BacktestResult:
    cfg: BacktestConfig
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    final_equity: float = 0.0

    # métricas calculadas
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    profit_factor: float = 0.0
    sharpe: float = 0.0       # simplificado (retorno/volatilidade)
    bars_in_market_pct: float = 0.0

    def summary(self) -> str:
        lines = [
            f"=== Backtest {self.cfg.symbol} {self.cfg.timeframe} ({self.cfg.limit} candles) ===",
            f"Período simulado: {self.cfg.limit} candles | warmup: {self.cfg.warmup}",
            f"Capital inicial: {self.cfg.initial_balance:.2f} USDT",
            f"Capital final  : {self.final_equity:.2f} USDT  ({self.total_pnl_pct:+.2f}%)",
            f"Total trades   : {self.total_trades}  (wins {self.wins} / losses {self.losses})",
            f"Win rate       : {self.win_rate:.1f}%",
            f"Avg win        : {self.avg_win_pct:+.2f}% | Avg loss: {self.avg_loss_pct:+.2f}%",
            f"Profit factor  : {self.profit_factor:.2f}",
            f"Max drawdown   : {self.max_drawdown_pct:.2f}%",
            f"Sharpe (simpl) : {self.sharpe:.2f}",
            f"Tempo em mercado: {self.bars_in_market_pct:.1f}%",
        ]
        return "\n".join(lines)


class Backtester:
    """Replay tick-a-tick em candles históricos."""

    def __init__(
        self,
        client: BrokerClient,
        strategy: Optional[ScoringStrategy] = None,
        agent: Optional[SeniorTraderAgent] = None,
        learning=None,
    ) -> None:
        self.client = client
        self.strategy = strategy or ScoringStrategy(StrategyConfig())
        # No backtest desligamos cooldown (tempo é simulado em barras, não em segundos)
        self.agent = agent or SeniorTraderAgent(AgentConfig(cooldown_seconds=0))
        # LearningEngine opcional — se passado, registra trades para aprender
        # com a simulação histórica (mesmo cérebro usado em paper/live).
        self.learning = learning
        # Baseline imutável do AgentConfig — necessário para apply_to_agent
        from copy import deepcopy
        self._agent_baseline = deepcopy(self.agent.cfg) if hasattr(self.agent, "cfg") else None

    # ---------- Pipeline ----------
    def run(self, cfg: BacktestConfig) -> BacktestResult:
        df = self.client.get_ohlcv(cfg.symbol, timeframe=cfg.timeframe, limit=cfg.limit)
        if df.empty or len(df) < cfg.warmup + 5:
            raise RuntimeError("Histórico insuficiente para backtest.")

        # pré-calcula indicadores em toda a série (vetorizado)
        close = df["close"].astype(float)
        rsi = RSIIndicator(close=close, window=cfg.rsi_period).rsi()
        sma_f = SMAIndicator(close=close, window=cfg.sma_fast).sma_indicator()
        sma_s = SMAIndicator(close=close, window=cfg.sma_slow).sma_indicator()
        macd = MACD(close=close, window_fast=cfg.macd_fast,
                    window_slow=cfg.macd_slow, window_sign=cfg.macd_signal)
        macd_line = macd.macd()
        macd_sig = macd.macd_signal()
        macd_hist = macd.macd_diff()

        # ADX e volume_ratio para filtros anti-overtrading
        try:
            adx_series = ADXIndicator(
                high=df["high"].astype(float),
                low=df["low"].astype(float),
                close=close, window=14,
            ).adx()
        except Exception:
            adx_series = pd.Series([0.0] * len(close), index=close.index)
        vol_series = df["volume"].astype(float) if "volume" in df.columns else pd.Series([1.0] * len(close), index=close.index)
        vol_ma20 = vol_series.rolling(20).mean()

        cash = cfg.initial_balance
        position_amount = 0.0
        position_entry = 0.0
        position_entry_idx = 0
        # Contexto do trade aberto (para LearningEngine ao fechar)
        entry_ctx: dict | None = None
        trades: list[BacktestTrade] = []
        equity_curve: list[float] = []
        bars_in_market = 0

        result = BacktestResult(cfg=cfg)

        for i in range(cfg.warmup, len(df)):
            price = float(close.iloc[i])
            equity = cash + position_amount * price
            equity_curve.append(equity)

            if position_amount > 1e-9:
                bars_in_market += 1

            # janela de retornos para volatilidade (últimas 20 barras)
            ret_window = close.iloc[max(0, i - 19): i + 1].pct_change().dropna()
            vol_pct = float(ret_window.std() * 100.0) if len(ret_window) > 1 else 0.0
            if math.isnan(vol_pct):
                vol_pct = 0.0

            snap = MarketSnapshot(
                symbol=cfg.symbol, price=price,
                rsi=_safe(rsi.iloc[i], 50.0),
                sma_fast=_safe(sma_f.iloc[i], price),
                sma_slow=_safe(sma_s.iloc[i], price),
                macd=_safe(macd_line.iloc[i], 0.0),
                macd_signal=_safe(macd_sig.iloc[i], 0.0),
                macd_hist=_safe(macd_hist.iloc[i], 0.0),
                volatility_pct=vol_pct,
                df=df,
                quote_volume_24h=0.0,
                change_pct_24h=0.0,
                adx=_safe(adx_series.iloc[i], 0.0),
                volume_ratio=(_safe(vol_series.iloc[i], 1.0) / _safe(vol_ma20.iloc[i], 1.0)) if _safe(vol_ma20.iloc[i], 0.0) > 0 else 1.0,
            )
            raw = self.strategy.decide(snap, news_score=0)
            has_position = position_amount > 1e-9

            # 1) Saída forçada por SL/TP (prioridade absoluta)
            if has_position:
                must, reason = self.agent.check_exit(price)
                if must:
                    fill_price = price * (1 - cfg.slippage_pct / 100.0)
                    proceeds = position_amount * fill_price
                    fee = proceeds * (cfg.fee_pct / 100.0)
                    cash += proceeds - fee
                    pnl = (fill_price - position_entry) * position_amount - fee
                    pnl_pct = (fill_price / position_entry - 1) * 100.0
                    trades.append(BacktestTrade(
                        entry_idx=position_entry_idx, exit_idx=i,
                        entry_price=position_entry, exit_price=fill_price,
                        amount=position_amount, pnl=pnl, pnl_pct=pnl_pct,
                        exit_reason="TP" if "TAKE-PROFIT" in reason else "SL",
                        duration_bars=i - position_entry_idx,
                    ))
                    position_amount = 0.0
                    position_entry = 0.0
                    self.agent.on_position_closed(pnl_pct)
                    self._learn_from_trade(entry_ctx, pnl, pnl_pct,
                                           "TP" if "TAKE-PROFIT" in reason else "SL",
                                           bars=i - position_entry_idx, tf=cfg.timeframe)
                    entry_ctx = None
                    continue

            # 2) Decisão do agent
            dec = self.agent.evaluate(snap, raw, has_position=has_position, news_score=0)

            if dec.signal == "BUY" and not has_position:
                size_usdt = equity * (cfg.trade_size_pct / 100.0)
                # Sanity-mode: respeita size_factor do agent (qualidade mediana → menor)
                sf = float(getattr(dec, "size_factor", 1.0))
                if sf < 1.0:
                    size_usdt *= sf
                if size_usdt < 1e-3 or cash < size_usdt:
                    continue
                fill_price = price * (1 + cfg.slippage_pct / 100.0)
                amount = size_usdt / fill_price
                fee = size_usdt * (cfg.fee_pct / 100.0)
                cash -= size_usdt + fee
                position_amount = amount
                position_entry = fill_price
                position_entry_idx = i
                self.agent.on_position_opened(fill_price)
                entry_ctx = {
                    "regime": getattr(dec, "regime", "RANGING"),
                    "sentiment": 0,
                    "quality": int(getattr(dec, "quality", 0)),
                }

            elif dec.signal == "SELL" and has_position:
                fill_price = price * (1 - cfg.slippage_pct / 100.0)
                proceeds = position_amount * fill_price
                fee = proceeds * (cfg.fee_pct / 100.0)
                cash += proceeds - fee
                pnl = (fill_price - position_entry) * position_amount - fee
                pnl_pct = (fill_price / position_entry - 1) * 100.0
                trades.append(BacktestTrade(
                    entry_idx=position_entry_idx, exit_idx=i,
                    entry_price=position_entry, exit_price=fill_price,
                    amount=position_amount, pnl=pnl, pnl_pct=pnl_pct,
                    exit_reason="SELL_signal",
                    duration_bars=i - position_entry_idx,
                ))
                position_amount = 0.0
                position_entry = 0.0
                self.agent.on_position_closed(pnl_pct)
                self._learn_from_trade(entry_ctx, pnl, pnl_pct, "SELL_signal",
                                       bars=i - position_entry_idx, tf=cfg.timeframe)
                entry_ctx = None

        # fecha posição aberta no fim do período (mark-to-market)
        if position_amount > 1e-9:
            last_price = float(close.iloc[-1])
            proceeds = position_amount * last_price
            fee = proceeds * (cfg.fee_pct / 100.0)
            cash += proceeds - fee
            pnl = (last_price - position_entry) * position_amount - fee
            pnl_pct = (last_price / position_entry - 1) * 100.0
            trades.append(BacktestTrade(
                entry_idx=position_entry_idx, exit_idx=len(df) - 1,
                entry_price=position_entry, exit_price=last_price,
                amount=position_amount, pnl=pnl, pnl_pct=pnl_pct,
                exit_reason="EOD", duration_bars=len(df) - 1 - position_entry_idx,
            ))
            position_amount = 0.0

        result.trades = trades
        result.equity_curve = equity_curve
        result.final_equity = cash + position_amount * float(close.iloc[-1])
        _compute_metrics(result, cfg, bars_in_market, len(df) - cfg.warmup)
        return result

    # ---------- Learning hook ----------
    def _learn_from_trade(self, ctx: dict | None, pnl: float, pnl_pct: float,
                          exit_reason: str, bars: int, tf: str) -> None:
        """Alimenta o LearningEngine com o resultado de um trade simulado.

        Permite que a IA aprenda com a simulação histórica usando o mesmo
        cérebro adaptativo de paper/live. Após cada trade, reaplica os
        ajustes ao agent para que o próximo trade já use o aprendizado.
        """
        if self.learning is None or ctx is None:
            return
        try:
            from core.learning import TradeOutcome
            # Estimativa grosseira de duração em segundos (apenas para métricas)
            tf_secs = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800,
                       "1h": 3600, "4h": 14400, "1d": 86400}.get(tf, 3600)
            outcome = TradeOutcome(
                pnl=pnl,
                pnl_pct=pnl_pct,
                win=pnl_pct > 0,
                regime=str(ctx.get("regime", "RANGING")),
                sentiment_at_entry=int(ctx.get("sentiment", 0)),
                quality_at_entry=int(ctx.get("quality", 0)),
                exit_reason=exit_reason,  # type: ignore[arg-type]
                duration_seconds=float(bars * tf_secs),
            )
            self.learning.record_trade(outcome)
            if self._agent_baseline is not None:
                self.learning.apply_to_agent(self.agent, self._agent_baseline)
        except Exception as e:  # noqa: BLE001
            log = logging.getLogger("backtest")
            log.warning("LearningEngine falhou no backtest: %s", e)


def _safe(v, fallback: float) -> float:
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return float(fallback)
    if math.isnan(fv) or math.isinf(fv):
        return float(fallback)
    return fv


def _compute_metrics(r: BacktestResult, cfg: BacktestConfig,
                     bars_in_market: int, total_bars: int) -> None:
    r.total_trades = len(r.trades)
    wins = [t for t in r.trades if t.pnl > 0]
    losses = [t for t in r.trades if t.pnl <= 0]
    r.wins = len(wins)
    r.losses = len(losses)
    r.win_rate = (r.wins / r.total_trades * 100.0) if r.total_trades else 0.0
    r.total_pnl = r.final_equity - cfg.initial_balance
    r.total_pnl_pct = r.total_pnl / cfg.initial_balance * 100.0
    r.avg_win_pct = float(np.mean([t.pnl_pct for t in wins])) if wins else 0.0
    r.avg_loss_pct = float(np.mean([t.pnl_pct for t in losses])) if losses else 0.0
    gross_win = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    r.profit_factor = (gross_win / gross_loss) if gross_loss > 1e-9 else float("inf") if gross_win > 0 else 0.0

    # max drawdown
    peak = -float("inf")
    max_dd = 0.0
    for eq in r.equity_curve:
        peak = max(peak, eq)
        if peak > 0:
            dd = (peak - eq) / peak * 100.0
            max_dd = max(max_dd, dd)
    r.max_drawdown_pct = max_dd

    # sharpe simplificado: retorno médio / desvio dos retornos por barra
    if len(r.equity_curve) > 2:
        rets = pd.Series(r.equity_curve).pct_change().dropna()
        if rets.std() > 1e-12:
            r.sharpe = float(rets.mean() / rets.std() * math.sqrt(len(rets)))
    r.bars_in_market_pct = (bars_in_market / total_bars * 100.0) if total_bars else 0.0
