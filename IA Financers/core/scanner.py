"""Market Scanner — varre múltiplos pares e ranqueia o melhor pra IA operar.

Reaproveita MarketDataService + ScoringStrategy + SeniorTraderAgent (o MESMO
cérebro que o engine usa em runtime). Assim, o briefing reflete EXATAMENTE
o que a IA pensa de cada par.

Uso:
    scanner = MarketScanner(client, agent_cfg, strategy_cfg)
    report = scanner.scan(["BTC/BRL", "ETH/BRL", "SOL/BRL", ...])
    print(report.to_text())
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from core.market import MarketDataService
from core.strategy import ScoringStrategy, StrategyConfig
from core.trader_agent import AgentConfig, SeniorTraderAgent


@dataclass
class PairAnalysis:
    symbol: str
    price: float = 0.0
    quality: int = 0
    regime: str = "—"
    signal: str = "HOLD"
    score: int = 0
    rsi: float = 0.0
    macd_hist: float = 0.0
    volatility_pct: float = 0.0
    change_24h: float = 0.0
    quote_volume_24h: float = 0.0
    sentiment: int = 0
    reasons: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def confidence_label(self) -> str:
        if self.error:
            return "❌ ERRO"
        if self.quality >= 75:
            return "🟢 ALTA"
        if self.quality >= 55:
            return "🟡 MÉDIA"
        if self.quality >= 35:
            return "🟠 BAIXA"
        return "🔴 MUITO BAIXA"


@dataclass
class ScanReport:
    analyses: list[PairAnalysis]
    timestamp: float = 0.0

    @property
    def best(self) -> PairAnalysis | None:
        ok = [a for a in self.analyses if not a.error]
        if not ok:
            return None
        return max(ok, key=lambda a: a.quality)

    @property
    def top(self) -> list[PairAnalysis]:
        ok = [a for a in self.analyses if not a.error]
        return sorted(ok, key=lambda a: a.quality, reverse=True)

    def to_text(self) -> str:
        lines = []
        lines.append("=" * 78)
        lines.append("🔍  BRIEFING DE MERCADO — análise multi-par")
        lines.append("=" * 78)

        ranked = self.top
        if not ranked:
            lines.append("❌ Nenhum par pôde ser analisado. Verifique conexão/API.")
            return "\n".join(lines)

        # Cabeçalho de ranking
        lines.append(
            f"\n{'#':<3} {'PAR':<12} {'CONFIANÇA':<14} {'Q':>4} {'SINAL':<6} "
            f"{'REGIME':<14} {'24h%':>7} {'Vol%':>6}"
        )
        lines.append("-" * 78)
        for i, a in enumerate(ranked, 1):
            lines.append(
                f"{i:<3} {a.symbol:<12} {a.confidence_label:<14} {a.quality:>4} "
                f"{a.signal:<6} {a.regime:<14} {a.change_24h:>+6.2f}% {a.volatility_pct:>5.2f}%"
            )

        # Erros
        bad = [a for a in self.analyses if a.error]
        if bad:
            lines.append("\nFalhas:")
            for a in bad:
                lines.append(f"  ⚠ {a.symbol}: {a.error}")

        # Recomendação
        best = ranked[0]
        lines.append("\n" + "=" * 78)
        lines.append("🎯  RECOMENDAÇÃO DA IA")
        lines.append("=" * 78)
        lines.append(f"Melhor par agora: {best.symbol}  ({best.confidence_label})")
        lines.append(f"  Preço: {best.price:.6f}   Sinal: {best.signal}   Regime: {best.regime}")
        lines.append(f"  RSI={best.rsi:.1f}   MACD_hist={best.macd_hist:+.4f}   "
                     f"Volatilidade={best.volatility_pct:.2f}%")
        lines.append(f"  Variação 24h={best.change_24h:+.2f}%   Volume 24h={best.quote_volume_24h:,.0f}")
        if best.reasons:
            lines.append("  Razões:")
            for r in best.reasons:
                lines.append(f"    • {r}")

        # Veredito final — voz do veterano
        lines.append("\n" + "-" * 78)
        if best.quality >= 75 and best.signal == "BUY":
            verdict = (
                f"✅ SETUP CONFIRMADO em {best.symbol}.\n"
                "   Entrada técnica, não emocional. SL/TP automáticos.\n"
                "   Veterano diz: \"Quando o setup aparece, executa. Sem hesitar, sem dobrar.\""
            )
        elif best.quality >= 55 and best.signal == "BUY":
            verdict = (
                f"⚠ SETUP MEDIANO em {best.symbol}.\n"
                "   Entrada aqui é 50% técnica, 50% torcida. Reduza o tamanho.\n"
                "   Veterano diz: \"Confiança média = posição menor. O mercado não vai a lugar nenhum.\""
            )
        elif best.signal == "HOLD":
            verdict = (
                "⏸  SEM SETUP CLARO. Mercado está pedindo paciência.\n"
                "   Veterano diz: \"O melhor trade do dia pode ser não fazer trade.\n"
                "   Capital preservado é capital pronto pra próxima oportunidade.\""
            )
        else:
            verdict = (
                "🔴 NENHUMA OPORTUNIDADE NO RADAR.\n"
                "   Veterano diz: \"Forçar entrada em mercado ruim é como apostar.\n"
                "   Fecha o app, vai fazer outra coisa, volta em 30 min.\""
            )
        lines.append(verdict)
        lines.append("=" * 78)
        return "\n".join(lines)


class MarketScanner:
    """Varre uma lista de símbolos e devolve análise ranqueada.

    Nota: o agente recebido NÃO deve ter posição aberta — para o briefing
    queremos sempre a avaliação 'como se fosse entrar agora'.
    """

    def __init__(
        self,
        client,
        agent_cfg: AgentConfig | None = None,
        strategy_cfg: StrategyConfig | None = None,
    ) -> None:
        self.market = MarketDataService(client)
        self.strategy = ScoringStrategy(strategy_cfg)
        self.agent_cfg = agent_cfg or AgentConfig()

    def scan(
        self,
        symbols: list[str],
        timeframe: str = "1m",
        progress: Callable[[int, int, str], None] | None = None,
    ) -> ScanReport:
        import time

        analyses: list[PairAnalysis] = []
        total = len(symbols)
        for i, sym in enumerate(symbols, 1):
            if progress:
                try:
                    progress(i, total, sym)
                except Exception:
                    pass
            try:
                snap = self.market.fetch_snapshot(sym, timeframe=timeframe)
                raw = self.strategy.decide(snap)
                # Agente novo a cada par (sem estado de cooldown/posição)
                agent = SeniorTraderAgent(AgentConfig(**self.agent_cfg.__dict__))
                dec = agent.evaluate(snap, raw, has_position=False, news_score=0)
                analyses.append(PairAnalysis(
                    symbol=sym,
                    price=snap.price,
                    quality=dec.quality,
                    regime=dec.regime,
                    signal=dec.signal,
                    score=raw.score,
                    rsi=snap.rsi,
                    macd_hist=snap.macd_hist,
                    volatility_pct=snap.volatility_pct,
                    change_24h=snap.change_pct_24h,
                    quote_volume_24h=snap.quote_volume_24h,
                    sentiment=dec.sentiment,
                    reasons=list(dec.reasons),
                ))
            except Exception as e:  # noqa: BLE001
                alias = SYMBOL_ALIASES.get(sym)
                if alias:
                    try:
                        snap = self.market.fetch_snapshot(alias, timeframe=timeframe)
                        raw = self.strategy.decide(snap)
                        agent = SeniorTraderAgent(AgentConfig(**self.agent_cfg.__dict__))
                        dec = agent.evaluate(snap, raw, has_position=False, news_score=0)
                        analyses.append(PairAnalysis(
                            symbol=alias,
                            price=snap.price,
                            quality=dec.quality,
                            regime=dec.regime,
                            signal=dec.signal,
                            score=raw.score,
                            rsi=snap.rsi,
                            macd_hist=snap.macd_hist,
                            volatility_pct=snap.volatility_pct,
                            change_24h=snap.change_pct_24h,
                            quote_volume_24h=snap.quote_volume_24h,
                            sentiment=dec.sentiment,
                            reasons=list(dec.reasons) + [f"alias automático: {sym} → {alias}"],
                        ))
                        continue
                    except Exception as e2:  # noqa: BLE001
                        analyses.append(PairAnalysis(symbol=sym, error=f"{type(e2).__name__}: {e2}"))
                        continue
                analyses.append(PairAnalysis(symbol=sym, error=f"{type(e).__name__}: {e}"))

        return ScanReport(analyses=analyses, timestamp=time.time())


# Listas padrão de símbolos por contexto
DEFAULT_SYMBOLS_BRL = [
    "BTC/BRL", "ETH/BRL", "SOL/BRL", "XRP/BRL", "BNB/BRL",
    "ADA/BRL", "DOGE/BRL", "LINK/BRL", "LTC/BRL", "RENDER/BRL",
]

DEFAULT_SYMBOLS_USDT = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT",
    "ADA/USDT", "DOGE/USDT", "LINK/USDT", "AVAX/USDT", "POL/USDT",
]

# Alias de símbolos antigos/deprecados da Binance.
SYMBOL_ALIASES = {
    "MATIC/USDT": "POL/USDT",
}


def default_symbols_for_quote(quote: str) -> list[str]:
    if quote.upper() == "BRL":
        return DEFAULT_SYMBOLS_BRL
    return DEFAULT_SYMBOLS_USDT
