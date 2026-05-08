"""Trader Sênior — camada de decisão com mentalidade de gestor de risco.

Princípios (mindset 25 anos):
    1. Proteção de capital ANTES de buscar lucro.
    2. Sem setup claro = sem trade. Esperar é uma operação válida.
    3. Não opera em mercado ruim (volatilidade extrema ou sem tendência).
    4. Probabilidade, não certeza — usa qualidade do setup (0-100).
    5. Cooldown entre trades (evita overtrading emocional).
    6. Stop-loss e take-profit definidos ANTES da entrada.

Fluxo:
    snapshot + decision (strategy bruta) -> SeniorTraderAgent.evaluate()
        -> AgentDecision com signal final, qualidade, regime, SL/TP, reasons
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

from core.market import MarketSnapshot
from core.sentiment import SentimentScore, market_sentiment_engine
from core.strategy import Decision, Signal

Regime = Literal["TRENDING_UP", "TRENDING_DOWN", "RANGING", "CHOPPY", "EXTREME_VOL"]


@dataclass
class AgentConfig:
    # Filtros de regime
    extreme_vol_pct: float = 2.5          # acima disso, mercado é "perigoso"
    calm_vol_pct: float = 0.15            # abaixo disso, mercado parado (sem oportunidade)
    trend_strength_min_pct: float = 0.05  # |sma_fast - sma_slow| / price em %

    # Qualidade mínima de setup (0-100) para operar com tamanho cheio.
    min_setup_quality: int = 60
    # Piso absoluto: setups abaixo disso não operam de jeito nenhum.
    # Entre `min_setup_quality_floor` e `min_setup_quality` a IA opera
    # com tamanho REDUZIDO proporcionalmente à qualidade (sanity-mode).
    min_setup_quality_floor: int = 35
    # Tamanho mínimo (fração do trade_size_pct) usado no piso de qualidade.
    # Ex.: 0.25 = quando qualidade == floor, opera com 25% do tamanho normal.
    risk_adjusted_min_size: float = 0.25

    # Cooldown entre trades (segundos) — evita overtrading
    cooldown_seconds: int = 60

    # Gestão por trade (% sobre preço de entrada)
    stop_loss_pct: float = 1.0
    take_profit_pct: float = 2.0

    # Trailing stop — protege lucro conforme o preço sobe.
    # Ativa quando o lucro >= activation_pct (ex.: 0.5%).
    # Mantém o stop a `distance_pct` abaixo do máximo atingido.
    # Defina trailing_enabled=False para desligar.
    trailing_enabled: bool = True
    trailing_activation_pct: float = 0.5
    trailing_distance_pct: float = 0.5

    # ---- Filtros anti-overtrading (introduzidos para banca pequena) ----
    # ADX abaixo deste valor = mercado lateral/ruidoso, sem força de tendência.
    # Trades nessas condições têm baixíssimo win-rate. Defina 0 para desligar.
    adx_min: float = 18.0
    # Volume da última barra precisa ser >= este múltiplo da média(20).
    # Filtra entradas em momentos sem convicção do mercado.
    volume_min_ratio: float = 0.8
    # Após um trade perdedor, multiplica o cooldown por este fator.
    # Evita revenge trading. Resetado quando há um trade vencedor.
    loss_cooldown_multiplier: float = 2.0
    # TP_pct deve ser >= este múltiplo das taxas round-trip estimadas.
    # Padrão: TP de 0.6% precisa cobrir 5x as taxas (5 * 0.2% = 1%) — falha,
    # ou seja, se TP < 1% com taxa 0.1% (round-trip 0.2%), trade é rejeitado.
    # Use 0 para desligar este filtro.
    min_tp_to_fee_ratio: float = 4.0
    fee_pct_per_side: float = 0.1   # Binance spot padrão
    # Entrada contra-tendência (somente para perfil agressivo).
    # Permite BUY com tamanho reduzido em oversold, mesmo fora de TRENDING_UP.
    countertrend_reversal_enabled: bool = False
    countertrend_rsi_max: float = 35.0
    countertrend_size_factor: float = 0.35
    # Duração máxima de uma posição aberta (segundos). 0 = desligado.
    # Quando ativo, força saída ao atingir o tempo mesmo sem SL/TP/trailing.
    # Útil para modo scalping: ex. 300 = fecha em até 5 minutos.
    max_trade_duration_seconds: int = 0


@dataclass
class AgentDecision:
    signal: Signal
    quality: int                  # 0-100, qualidade do setup
    regime: Regime
    reasons: list[str]
    stop_loss: float = 0.0        # preço absoluto; 0 = sem SL ativo
    take_profit: float = 0.0      # idem
    sentiment: int = 0            # -3..+3 do market_sentiment_engine
    # Multiplicador de tamanho de posição (0.0..1.0). Permite operar
    # setups medianos com tamanho menor (sanity-mode anti-aposta).
    size_factor: float = 1.0


@dataclass
class _PositionPlan:
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    opened_at: float = 0.0
    high_watermark: float = 0.0   # maior preço desde a abertura (para trailing)
    trailing_active: bool = False


class SeniorTraderAgent:
    """Cérebro do sistema — decide com viés de preservação de capital."""

    def __init__(self, cfg: AgentConfig | None = None) -> None:
        self.cfg = cfg or AgentConfig()
        self._last_trade_ts: float = 0.0
        self._plan: _PositionPlan = _PositionPlan()
        # Resultado do último trade fechado (% PnL). Usado para alongar o cooldown
        # após perdas (anti-revenge trading).
        self._last_trade_pnl_pct: float = 0.0

    # ---------- Hooks chamados pelo engine ----------
    def on_position_opened(self, entry_price: float) -> None:
        self._last_trade_ts = time.time()
        self._plan = _PositionPlan(
            entry_price=entry_price,
            stop_loss=entry_price * (1 - self.cfg.stop_loss_pct / 100.0),
            take_profit=entry_price * (1 + self.cfg.take_profit_pct / 100.0),
            opened_at=time.time(),
            high_watermark=entry_price,
            trailing_active=False,
        )

    def on_position_closed(self, pnl_pct: float = 0.0) -> None:
        self._last_trade_ts = time.time()
        self._last_trade_pnl_pct = float(pnl_pct)
        self._plan = _PositionPlan()

    def has_open_plan(self) -> bool:
        return self._plan.entry_price > 0

    def _update_trailing(self, price: float) -> str | None:
        """Atualiza high-watermark e o stop trailing. Retorna log se subiu."""
        if not self.cfg.trailing_enabled or not self.has_open_plan():
            return None
        plan = self._plan
        if price > plan.high_watermark:
            plan.high_watermark = price
        gain_pct = (plan.high_watermark / plan.entry_price - 1.0) * 100.0
        if not plan.trailing_active and gain_pct >= self.cfg.trailing_activation_pct:
            plan.trailing_active = True
        if plan.trailing_active:
            new_stop = plan.high_watermark * (1 - self.cfg.trailing_distance_pct / 100.0)
            if new_stop > plan.stop_loss:
                old = plan.stop_loss
                plan.stop_loss = new_stop
                return (
                    f"🔒 Trailing stop subiu: {old:.2f} → {new_stop:.2f} "
                    f"(pico {plan.high_watermark:.2f}, +{gain_pct:.2f}%)"
                )
        return None

    def check_exit(self, price: float) -> tuple[bool, str]:
        """Verifica SL/TP/trailing/tempo. Retorna (deve_sair, motivo)."""
        if not self.has_open_plan():
            return False, ""
        # Atualiza trailing antes de checar
        self._update_trailing(price)
        if price <= self._plan.stop_loss:
            tag = "TRAILING-STOP" if self._plan.trailing_active else "STOP-LOSS"
            return True, (
                f"{tag} @ {price:.2f} (entrada {self._plan.entry_price:.2f}, "
                f"pico {self._plan.high_watermark:.2f})"
            )
        if price >= self._plan.take_profit:
            return True, f"TAKE-PROFIT @ {price:.2f} (entrada {self._plan.entry_price:.2f})"
        # Limite de duração (scalping por tempo)
        if self.cfg.max_trade_duration_seconds > 0:
            elapsed = time.time() - self._plan.opened_at
            if elapsed >= self.cfg.max_trade_duration_seconds:
                pnl_pct = (price / self._plan.entry_price - 1.0) * 100.0
                return True, (
                    f"TEMPO-LIMITE @ {price:.2f} — posição aberta por "
                    f"{int(elapsed)}s >= {self.cfg.max_trade_duration_seconds}s "
                    f"(PnL estimado {pnl_pct:+.2f}%)"
                )
        return False, ""

    # ---------- Avaliação principal ----------
    def evaluate(self, snap: MarketSnapshot, raw: Decision, has_position: bool, news_score: int = 0) -> AgentDecision:
        regime = self._classify_regime(snap)
        sentiment: SentimentScore = market_sentiment_engine(
            change_pct_24h=snap.change_pct_24h,
            quote_volume_24h=snap.quote_volume_24h,
            news_score=news_score,
        )
        reasons: list[str] = [f"regime={regime}", f"sentimento={sentiment.score:+d}"]
        if sentiment.reasons:
            reasons.append("; ".join(sentiment.reasons))

        # 1) Mercado perigoso: nunca abrir posição
        if regime == "EXTREME_VOL":
            return AgentDecision(
                signal="HOLD", quality=0, regime=regime,
                reasons=reasons + [f"vol {snap.volatility_pct:.2f}% > {self.cfg.extreme_vol_pct}% — fora do mercado"],
                sentiment=sentiment.score,
            )

        # 2) Mercado parado: sem oportunidade
        if regime == "RANGING" and not has_position:
            return AgentDecision(
                signal="HOLD", quality=10, regime=regime,
                reasons=reasons + ["mercado parado — esperando setup"],
                sentiment=sentiment.score,
            )

        # 3) Cooldown: evita overtrading
        elapsed = time.time() - self._last_trade_ts
        # Cooldown dinâmico: dobra após perda (anti-revenge trading)
        cd = self.cfg.cooldown_seconds
        if self._last_trade_pnl_pct < 0:
            cd = int(cd * max(1.0, self.cfg.loss_cooldown_multiplier))
        if self._last_trade_ts > 0 and elapsed < cd and not has_position:
            return AgentDecision(
                signal="HOLD", quality=raw.score * 10, regime=regime,
                reasons=reasons + [
                    f"cooldown ativo ({int(cd - elapsed)}s restantes"
                    + (" — prolongado após perda" if self._last_trade_pnl_pct < 0 else "")
                    + ")"
                ],
                sentiment=sentiment.score,
            )

        quality = self._score_setup_quality(snap, raw, regime, sentiment)
        reasons.append(f"qualidade do setup: {quality}/100")

        # 4) Decisão de SAÍDA (se temos posição): estratégia disse SELL OU sentimento muito negativo
        if has_position and (raw.signal == "SELL" or sentiment.bearish):
            extra = "sentimento bearish" if sentiment.bearish else "estratégia indica saída"
            return AgentDecision(
                signal="SELL", quality=quality, regime=regime,
                reasons=reasons + [extra],
                sentiment=sentiment.score,
            )

        # 4b) Posição aberta sem motivo de saída: segura.
        if has_position:
            return AgentDecision(
                signal="HOLD", quality=quality, regime=regime,
                reasons=reasons + ["posição aberta — segurando"],
                sentiment=sentiment.score,
            )

        # 5) Decisão de ENTRADA: precisa qualidade mínima E sinal BUY E regime favorável
        if not has_position and raw.signal == "BUY":
            # 5a) Filtro ADX — sem força de tendência, não entra
            if self.cfg.adx_min > 0 and snap.adx > 0 and snap.adx < self.cfg.adx_min:
                return AgentDecision(
                    signal="HOLD", quality=quality, regime=regime,
                    reasons=reasons + [
                        f"BUY bloqueado — ADX {snap.adx:.1f} < mínimo {self.cfg.adx_min} "
                        f"(mercado lateral, sem força)"
                    ],
                    sentiment=sentiment.score,
                )
            # 5b) Filtro de volume — barra atual sem convicção, não entra
            if snap.volume_ratio > 0 and snap.volume_ratio < self.cfg.volume_min_ratio:
                return AgentDecision(
                    signal="HOLD", quality=quality, regime=regime,
                    reasons=reasons + [
                        f"BUY bloqueado — volume da barra {snap.volume_ratio:.2f}x "
                        f"abaixo do mínimo {self.cfg.volume_min_ratio:.2f}x da média"
                    ],
                    sentiment=sentiment.score,
                )
            # 5c) Filtro fee-aware — TP precisa cobrir múltiplas taxas
            if self.cfg.min_tp_to_fee_ratio > 0 and self.cfg.fee_pct_per_side > 0:
                fees_round_trip = 2.0 * self.cfg.fee_pct_per_side
                min_tp_required = fees_round_trip * self.cfg.min_tp_to_fee_ratio
                if self.cfg.take_profit_pct < min_tp_required:
                    return AgentDecision(
                        signal="HOLD", quality=quality, regime=regime,
                        reasons=reasons + [
                            f"BUY bloqueado — TP {self.cfg.take_profit_pct:.2f}% < mínimo "
                            f"{min_tp_required:.2f}% para superar taxas. Aumente take_profit_pct."
                        ],
                        sentiment=sentiment.score,
                    )
            if regime != "TRENDING_UP":
                if (
                    self.cfg.countertrend_reversal_enabled
                    and snap.rsi <= self.cfg.countertrend_rsi_max
                    and quality >= self.cfg.min_setup_quality_floor
                ):
                    size_factor = max(0.05, min(1.0, float(self.cfg.countertrend_size_factor)))
                    sl = snap.price * (1 - self.cfg.stop_loss_pct / 100.0)
                    tp = snap.price * (1 + self.cfg.take_profit_pct / 100.0)
                    return AgentDecision(
                        signal="BUY", quality=quality, regime=regime,
                        reasons=reasons + [
                            "entrada contra-tendência (perfil agressivo): oversold detectado",
                            f"tamanho reduzido {size_factor*100:.0f}% | RSI {snap.rsi:.1f} <= {self.cfg.countertrend_rsi_max:.1f}",
                            f"SL={sl:.6f} TP={tp:.6f}",
                        ],
                        stop_loss=sl,
                        take_profit=tp,
                        sentiment=sentiment.score,
                        size_factor=size_factor,
                    )
                return AgentDecision(
                    signal="HOLD", quality=quality, regime=regime,
                    reasons=reasons + ["BUY ignorado — não há tendência de alta confirmada"],
                    sentiment=sentiment.score,
                )
            if sentiment.bearish:
                return AgentDecision(
                    signal="HOLD", quality=quality, regime=regime,
                    reasons=reasons + ["BUY bloqueado — sentimento macro bearish"],
                    sentiment=sentiment.score,
                )
            if quality < self.cfg.min_setup_quality_floor:
                return AgentDecision(
                    signal="HOLD", quality=quality, regime=regime,
                    reasons=reasons + [
                        f"qualidade {quality} < piso {self.cfg.min_setup_quality_floor} "
                        f"— setup fraco demais até para tamanho reduzido"
                    ],
                    sentiment=sentiment.score,
                )
            # Sanity-mode: setup mediano → opera com tamanho proporcional
            if quality < self.cfg.min_setup_quality:
                floor = self.cfg.min_setup_quality_floor
                ceil = self.cfg.min_setup_quality
                ratio = (quality - floor) / max(1, ceil - floor)  # 0..1
                size_factor = self.cfg.risk_adjusted_min_size + \
                    (1.0 - self.cfg.risk_adjusted_min_size) * ratio
                size_factor = max(0.05, min(1.0, size_factor))
                sl = snap.price * (1 - self.cfg.stop_loss_pct / 100.0)
                tp = snap.price * (1 + self.cfg.take_profit_pct / 100.0)
                return AgentDecision(
                    signal="BUY", quality=quality, regime=regime,
                    reasons=reasons + [
                        f"setup mediano (q={quality}) — entrando com tamanho REDUZIDO "
                        f"({size_factor*100:.0f}% do normal) para limitar risco",
                        f"SL={sl:.6f} TP={tp:.6f}",
                    ],
                    stop_loss=sl, take_profit=tp,
                    sentiment=sentiment.score,
                    size_factor=size_factor,
                )
            sl = snap.price * (1 - self.cfg.stop_loss_pct / 100.0)
            tp = snap.price * (1 + self.cfg.take_profit_pct / 100.0)
            return AgentDecision(
                signal="BUY", quality=quality, regime=regime,
                reasons=reasons + [
                    f"setup confirmado: tendência + score {raw.score} + sentimento {sentiment.score:+d}",
                    f"SL={sl:.2f} TP={tp:.2f}",
                ],
                stop_loss=sl, take_profit=tp,
                sentiment=sentiment.score,
            )

        return AgentDecision(
            signal="HOLD", quality=quality, regime=regime,
            reasons=reasons + ["sem ação — aguardando contexto"],
            sentiment=sentiment.score,
        )

    # ---------- Internos ----------
    def _classify_regime(self, snap: MarketSnapshot) -> Regime:
        if snap.volatility_pct > self.cfg.extreme_vol_pct:
            return "EXTREME_VOL"
        trend_strength = abs(snap.sma_fast - snap.sma_slow) / max(snap.price, 1e-9) * 100.0
        if trend_strength < self.cfg.trend_strength_min_pct:
            if snap.volatility_pct < self.cfg.calm_vol_pct:
                return "RANGING"
            return "CHOPPY"
        if snap.sma_fast > snap.sma_slow:
            return "TRENDING_UP"
        return "TRENDING_DOWN"

    def _score_setup_quality(self, snap: MarketSnapshot, raw: Decision, regime: Regime, sentiment: SentimentScore) -> int:
        """Qualidade 0-100: quanto mais fatores alinhados, maior."""
        q = 0
        # Score bruto (até 4 fatores na strategy: RSI, SMA, MACD, news) -> escala
        q += min(35, abs(raw.score) * 11)
        # Tendência clara
        if regime in ("TRENDING_UP", "TRENDING_DOWN"):
            q += 22
        # MACD confirma direção
        if (raw.signal == "BUY" and snap.macd_hist > 0) or (raw.signal == "SELL" and snap.macd_hist < 0):
            q += 13
        # RSI em zona favorável (não nos extremos)
        if 35 <= snap.rsi <= 65:
            q += 8
        # Volatilidade saudável (entre calm e extreme)
        if self.cfg.calm_vol_pct <= snap.volatility_pct <= self.cfg.extreme_vol_pct * 0.7:
            q += 8
        # Contexto macro alinhado com a direção do trade
        if (raw.signal == "BUY" and sentiment.score > 0) or (raw.signal == "SELL" and sentiment.score < 0):
            q += 14
        elif (raw.signal == "BUY" and sentiment.score < 0) or (raw.signal == "SELL" and sentiment.score > 0):
            q -= 12  # contexto contra o trade
        return max(0, min(100, q))
