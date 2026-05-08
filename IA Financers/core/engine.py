"""Engine principal — orquestra mercado + estratégia + risco + execução.

Roda em uma QThread. A cada tick:
    1. Busca snapshot de mercado (preço + indicadores).
    2. Busca score de notícias (se habilitado, com cache).
    3. Estratégia decide.
    4. Risk Engine valida.
    5. Executa trade simulado (ou real, se mode='live').
    6. Emite sinais para a UI.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from core.market import MarketDataService, MarketSnapshot
from core.news import NewsService
from core.risk import RiskEngine
from core.safety import SafetyEngine
from core.strategy import Decision, ScoringStrategy
from core.trader_agent import AgentConfig, AgentDecision, SeniorTraderAgent
from core.learning import LearningEngine, TradeOutcome
from core.gatekeeper_learning import GatekeeperLearning
from core.journal import append_trade as journal_append
from exchange.base import BrokerClient
from pathlib import Path

Mode = Literal["simulation", "live"]


@dataclass
class EngineConfig:
    symbol: str = "BTC/USDT"
    timeframe: str = "1m"
    mode: Mode = "simulation"
    poll_interval_seconds: int = 5
    initial_balance_usdt: float = 1000.0
    rsi_period: int = 14
    sma_fast: int = 9
    sma_slow: int = 21
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    # Auto-features (IA opera mais sozinha quando ativadas)
    auto_scan_enabled: bool = True
    notifications: dict | None = None
    # Shadow trading: IA simula trades em paralelo (sem ordens reais) para
    # aprender mesmo sem capital ou enquanto IA está desativada.
    # Útil quando trade_size_pct × saldo < min_notional da exchange.
    paper_trade_enabled: bool = True
    # 🎓 Conselheiro Senior LLM (Ollama local) — opt-in. Não decide nada.
    senior_enabled: bool = False
    senior_url: str = "http://localhost:11434"
    senior_model: str = "llama3:latest"
    senior_timeout_sec: float = 15.0
    senior_min_interval_sec: float = 60.0
    # 🛡 Gatekeeper Ollama: aprova/veta a decisão da heurística (síncrono curto).
    gatekeeper_enabled: bool = False
    gatekeeper_url: str = "http://localhost:11434"
    gatekeeper_model: str = "llama3:latest"
    gatekeeper_timeout_sec: float = 8.0
    gatekeeper_min_confidence_to_veto: int = 70
    gatekeeper_min_confidence_to_rescue: int = 75
    gatekeeper_rescue_quality_floor: int = 35
    gatekeeper_rescue_size_pct: int = 50
    # Modo decisivo: em perfil agressivo, não fica travado em HOLD por veto.
    decisive_mode: bool = False
    decisive_min_entry_pct: int = 35


@dataclass
class Trade:
    timestamp: datetime
    side: str
    price: float
    amount: float
    pnl: float = 0.0


@dataclass
class Portfolio:
    cash_usdt: float
    base_amount: float = 0.0
    avg_entry: float = 0.0
    trades: list[Trade] = field(default_factory=list)

    def equity(self, price: float) -> float:
        return self.cash_usdt + self.base_amount * price

    def position_value(self, price: float) -> float:
        return self.base_amount * price


class EngineWorker(QObject):
    price_updated = pyqtSignal(float, float)
    decision_updated = pyqtSignal(object)              # Decision
    agent_updated = pyqtSignal(object)                 # AgentDecision
    portfolio_updated = pyqtSignal(float, float, float)
    trade_executed = pyqtSignal(object)                # Trade
    risk_updated = pyqtSignal(dict)
    safety_updated = pyqtSignal(dict)
    senior_updated = pyqtSignal(dict)
    news_updated = pyqtSignal(int, list)
    learning_updated = pyqtSignal(dict)                # stats do LearningEngine
    gatekeeper_learning_updated = pyqtSignal(dict)     # stats do GatekeeperLearning
    log_message = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    manual_trade_request = pyqtSignal(str, float)      # side, pct do tamanho (1..100)

    def __init__(
        self,
        cfg: EngineConfig,
        client: BrokerClient,
        market: MarketDataService,
        strategy: ScoringStrategy,
        risk: RiskEngine,
        news: NewsService,
        agent: SeniorTraderAgent | None = None,
        learning: LearningEngine | None = None,
        agent_baseline: AgentConfig | None = None,
        gatekeeper_learning: GatekeeperLearning | None = None,
    ) -> None:
        super().__init__()
        self.cfg = cfg
        self.client = client
        self.market = market
        self.strategy = strategy
        self.risk = risk
        self.news = news
        self.agent = agent
        self.learning = learning
        # Baseline: valores originais do agent (antes de qualquer ajuste de aprendizado)
        self.agent_baseline = agent_baseline or (
            AgentConfig(**agent.cfg.__dict__) if agent else None
        )
        # Aplica ajustes salvos (se houver) ANTES de começar a operar
        if self.learning and self.agent and self.agent_baseline:
            self.learning.apply_to_agent(self.agent, self.agent_baseline)
        # Camada de proteção 24h: circuit breaker semanal + rate limit + heartbeat
        try:
            from core.paths import data_dir
            self.safety = SafetyEngine(persist_path=data_dir() / "safety.json")
        except Exception:  # noqa: BLE001
            self.safety = SafetyEngine()
        # 🎓 Conselheiro Senior LLM (Ollama local) — opt-in via cfg.senior_*
        try:
            from core.senior import SeniorAdvisor, SeniorConfig
            self.senior = SeniorAdvisor(SeniorConfig(
                enabled=bool(getattr(cfg, "senior_enabled", False)),
                url=str(getattr(cfg, "senior_url", "http://localhost:11434")),
                model=str(getattr(cfg, "senior_model", "llama3:latest")),
                timeout_sec=float(getattr(cfg, "senior_timeout_sec", 15.0)),
                min_interval_sec=float(getattr(cfg, "senior_min_interval_sec", 60.0)),
            ))
        except Exception:  # noqa: BLE001
            self.senior = None
        # 🛡 Gatekeeper Ollama (síncrono, curto) — aprova/veta heurística
        try:
            from core.gatekeeper import Gatekeeper, GatekeeperConfig
            self.gatekeeper = Gatekeeper(GatekeeperConfig(
                enabled=bool(getattr(cfg, "gatekeeper_enabled", False)),
                url=str(getattr(cfg, "gatekeeper_url", "http://localhost:11434")),
                model=str(getattr(cfg, "gatekeeper_model", "llama3:latest")),
                timeout_sec=float(getattr(cfg, "gatekeeper_timeout_sec", 8.0)),
                min_confidence_to_veto=int(getattr(cfg, "gatekeeper_min_confidence_to_veto", 70)),
                min_confidence_to_rescue=int(getattr(cfg, "gatekeeper_min_confidence_to_rescue", 75)),
                rescue_quality_floor=int(getattr(cfg, "gatekeeper_rescue_quality_floor", 35)),
                rescue_size_pct=int(getattr(cfg, "gatekeeper_rescue_size_pct", 50)),
            ))
        except Exception:  # noqa: BLE001
            self.gatekeeper = None
        # 🧠 Aprendizado adaptativo do Gatekeeper — ajusta thresholds c/ histórico
        self.gatekeeper_learning = gatekeeper_learning
        # Baseline dos thresholds — gravado antes de qualquer ajuste, para podermos
        # recalcular o valor absoluto a partir do delta persistido.
        if self.gatekeeper is not None:
            self._gk_baseline_veto = int(self.gatekeeper.cfg.min_confidence_to_veto)
            self._gk_baseline_rescue = int(self.gatekeeper.cfg.min_confidence_to_rescue)
            if self.gatekeeper_learning is not None:
                try:
                    self.gatekeeper_learning.apply_to_gatekeeper(
                        self.gatekeeper, self._gk_baseline_veto, self._gk_baseline_rescue,
                    )
                except Exception:  # noqa: BLE001
                    pass
        else:
            self._gk_baseline_veto = 70
            self._gk_baseline_rescue = 75
        self.portfolio = Portfolio(cash_usdt=cfg.initial_balance_usdt)
        # 💰 Em LIVE, sobrescreve com saldo REAL da corretora.
        # Lê o saldo da QUOTE currency do par (USDT, BRL, USDC, ...).
        # Em simulation, mantém o initial_balance_usdt configurado.
        if cfg.mode == "live":
            self._refresh_live_quote_balance()
        self._running = False
        self._ai_enabled = False
        self._news_cache: tuple[int, list[str]] = (0, [])
        self._news_last_ts = 0.0
        self._news_ttl_seconds = 120
        # Contexto do trade aberto (para o LearningEngine ao fechar)
        self._open_trade_ctx: dict | None = None

        # ---------- Persistência de posição (auto save/restore) ----------
        try:
            from core.position_store import PositionStore
            from core.paths import data_dir
            self.position_store = PositionStore(data_dir() / "position.json")
            saved = self.position_store.load()
            # Só restaura em modo live (em simulação cada sessão começa zerada)
            if saved is not None and cfg.mode == "live" and saved.symbol == cfg.symbol:
                # 🛡 Proteção: posição sem SL/TP é órfã — não restauramos cega.
                # (Resíduo de testes ou crash anterior podia deixar SL=TP=0,
                # o que tornaria check_exit inerte e a posição ficaria sem proteção.)
                if saved.stop_loss <= 0 or saved.take_profit <= 0:
                    self.log_message.emit(
                        f"⚠ Posição salva ({saved.symbol} qty={saved.amount} "
                        f"entry={saved.entry_price}) está SEM SL/TP — IGNORADA por segurança. "
                        f"Limpe data/position.json manualmente se for legítima."
                    )
                    try:
                        self.position_store.clear()
                    except Exception:  # noqa: BLE001
                        pass
                else:
                    self.log_message.emit(
                        f"♻ Posição restaurada do disco: {saved.symbol} "
                        f"qty={saved.amount} entry={saved.entry_price} "
                        f"SL={saved.stop_loss} TP={saved.take_profit}"
                    )
                    # Restaura no portfólio para a IA continuar de onde parou
                    self.portfolio.base_amount = saved.amount
                    self.portfolio.avg_entry = saved.entry_price
                    if self.agent is not None:
                        self.agent.on_position_opened(saved.entry_price)
                    self._open_trade_ctx = {
                        "regime": "?", "sentiment": 0, "quality": 0,
                        "entry_price": saved.entry_price,
                        "entry_ts": saved.opened_at or time.time(),
                    }
        except Exception as e:  # noqa: BLE001
            self.position_store = None
            self.log_message.emit(f"⚠ position_store offline: {e}")

        # ---------- Notificações automáticas ----------
        try:
            from core.notifier import build_notifier
            self.notifier = build_notifier(getattr(cfg, "notifications", None))
        except Exception as e:  # noqa: BLE001
            self.notifier = None
            self.log_message.emit(f"⚠ notifier offline: {e}")

        # ---------- Auto-scanner (IA escolhe par sozinha) ----------
        self._auto_scan_last = 0.0
        self._auto_scan_interval = 90  # 90s
        self._auto_scan_enabled = bool(getattr(cfg, "auto_scan_enabled", True))
        # Só permite trocar de par após fechar trade no lucro.
        self._auto_scan_wait_win = True

        # ---------- Streak tracking (aprende com sequências) ----------
        self._win_streak = 0
        self._loss_streak = 0
        # _size_multiplier persiste em disco via learning.state.size_multiplier
        # Ao iniciar, restaura o que aprendeu na sessão anterior.
        if self.learning is not None:
            self._size_multiplier = float(getattr(self.learning.state, "size_multiplier", 1.0))
            if self._size_multiplier != 1.0:
                self.log_message.emit(
                    f"📚 IA restaurou tamanho aprendido: {self._size_multiplier:.0%} "
                    f"do trade_size_pct configurado"
                )
        else:
            self._size_multiplier = 1.0

        # ---------- Anti-spam de alertas (volume etc) ----------
        self._last_volume_alert_ts = 0.0

        # ---------- Inteligência anti-HOLD (modo oportunidade adaptativo) ----------
        # Quando a IA fica muito tempo sem entrada (flat + HOLD), relaxamos
        # filtros de forma gradual para aumentar frequência de oportunidades.
        self._idle_hold_ticks = 0
        self._opportunity_mode_active = False
        self._opportunity_profile_backup: dict[str, float | int] | None = None

        # ---------- Shadow trading (aprende observando, sem operar) ----------
        # Estado de uma posição virtual paralela. Sempre roda, independente
        # de mode/AI enabled. Alimenta learning.record_trade() ao fechar.
        self._paper_enabled = bool(getattr(cfg, "paper_trade_enabled", True))
        self._paper_pos: dict | None = None  # {entry_price, entry_ts, sl, tp, regime, sentiment, quality}
        self._paper_stats = {"trades": 0, "wins": 0, "total_pnl_pct": 0.0}
        if self._paper_enabled:
            self.log_message.emit(
                "👁 Shadow trading ATIVO — IA aprende observando o mercado real "
                "(sem mexer no seu dinheiro)."
            )

        # Requisições de trade manual vindas da UI (queued para thread do worker)
        self.manual_trade_request.connect(self._on_manual_trade_request)

    # ---------- Controle externo ----------
    def stop(self) -> None:
        self._running = False

    def set_ai_enabled(self, enabled: bool) -> None:
        self._ai_enabled = enabled
        self.log_message.emit(f"IA {'ATIVADA' if enabled else 'DESATIVADA (modo manual)'}")

    def set_runtime_mode(self, mode: Mode) -> None:
        """Troca mode em runtime sem reiniciar o app."""
        new_mode: Mode = "live" if str(mode).lower() == "live" else "simulation"
        old_mode = self.cfg.mode
        if new_mode == old_mode:
            return
        self.cfg.mode = new_mode
        if new_mode == "live":
            self._refresh_live_quote_balance()
            self.log_message.emit("🔴 Modo LIVE aplicado em runtime.")
        else:
            self.log_message.emit("🟢 Modo SIMULAÇÃO aplicado em runtime (sem ordens reais).")

    def set_runtime_testnet(self, enabled: bool) -> bool:
        """Alterna testnet/sandbox do broker em runtime quando suportado.

        Retorna True quando a troca foi aplicada no cliente atual.
        """
        enabled = bool(enabled)
        self.cfg.use_testnet = enabled
        applied = False
        ccxt_client = getattr(self.client, "client", None)
        if ccxt_client is not None and hasattr(ccxt_client, "set_sandbox_mode"):
            try:
                ccxt_client.set_sandbox_mode(enabled)
                applied = True
            except Exception as e:  # noqa: BLE001
                self.log_message.emit(f"⚠ Não consegui alternar sandbox em runtime: {e}")

        broker_cfg = getattr(self.client, "cfg", None)
        if broker_cfg is not None:
            try:
                if hasattr(broker_cfg, "use_testnet"):
                    broker_cfg.use_testnet = enabled
                if hasattr(broker_cfg, "paper"):
                    broker_cfg.paper = enabled
            except Exception:  # noqa: BLE001
                pass

        if applied:
            self.log_message.emit(
                f"🧪 Testnet {'ATIVADO' if enabled else 'DESATIVADO'} em runtime."
            )
            if self.cfg.mode == "live":
                self._refresh_live_quote_balance()
        else:
            self.log_message.emit(
                "⚠ Troca de testnet não suportada em runtime por este broker; "
                "config salva para próxima inicialização."
            )
        return applied

    def set_runtime_broker_config(self, broker_block: dict, mode: Mode | None = None) -> bool:
        """Recria o broker em runtime com nova config (tipo, exchange, keys, testnet)."""
        try:
            from exchange.factory import build_broker

            bb = dict(broker_block or {})
            tmp_cfg = {
                "broker": bb,
                "api_key": bb.get("api_key", ""),
                "api_secret": bb.get("api_secret", ""),
                "use_testnet": bool(bb.get("use_testnet", True)),
            }
            new_client = build_broker(tmp_cfg)
            self.client = new_client
            self.market.client = new_client
            self.cfg.use_testnet = bool(bb.get("use_testnet", True))

            if mode is not None:
                self.cfg.mode = "live" if str(mode).lower() == "live" else "simulation"

            if self.cfg.mode == "live":
                self._refresh_live_quote_balance()

            self.log_message.emit(
                "🔁 Broker/API aplicados em runtime com sucesso."
            )
            return True
        except Exception as e:  # noqa: BLE001
            self.log_message.emit(f"⚠ Não consegui aplicar broker/API em runtime: {e}")
            return False

    def _refresh_live_quote_balance(self) -> None:
        quote_ccy = self.cfg.symbol.split("/")[-1].upper() if "/" in self.cfg.symbol else "USDT"
        try:
            ccxt_client = getattr(self.client, "client", None)
            if ccxt_client is not None and getattr(ccxt_client, "apiKey", ""):
                bal = ccxt_client.fetch_balance()
                free_q = float((bal.get("free") or {}).get(quote_ccy, 0.0) or 0.0)
                total_q = float((bal.get("total") or {}).get(quote_ccy, 0.0) or 0.0)
                if total_q > 0:
                    self.portfolio.cash_usdt = free_q
                    self.log_message.emit(
                        f"💰 Saldo real lido da corretora: {quote_ccy} free={free_q:.2f} "
                        f"total={total_q:.2f}"
                    )
                else:
                    self.log_message.emit(
                        f"⚠ Saldo {quote_ccy} real = 0 na corretora. Nenhuma ordem será dimensionada. "
                        f"Deposite {quote_ccy} antes de operar em LIVE."
                    )
                    self.portfolio.cash_usdt = 0.0
        except Exception as e:  # noqa: BLE001
            self.log_message.emit(
                f"⚠ Não consegui ler saldo real da corretora ({e}). "
                f"Usando initial_balance_usdt={self.cfg.initial_balance_usdt} como fallback."
            )

    def trip_kill_switch(self) -> None:
        self.risk.trip_kill_switch("Acionado manualmente")
        self.log_message.emit("⛔ TRAVA DE EMERGÊNCIA acionada")
        if getattr(self, "notifier", None) is not None:
            try:
                self.notifier.notify(
                    "kill_switch", "TRAVA DE EMERGÊNCIA acionada — IA parada",
                    level="critical",
                )
            except Exception:  # noqa: BLE001
                pass

    def pause(self, paused: bool) -> None:
        if paused:
            self.risk.pause()
            self.log_message.emit("⏸ Operações pausadas")
        else:
            self.risk.resume()
            self.log_message.emit("▶ Operações retomadas")

    def _on_manual_trade_request(self, side: str, pct: float) -> None:
        """Executa BUY/SELL manual com os mesmos guardrails de risco/safety."""
        try:
            side = str(side).upper().strip()
            pct = max(1.0, min(100.0, float(pct)))
            if side not in ("BUY", "SELL"):
                self.log_message.emit(f"⚠ Ordem manual inválida: {side}")
                return

            snap: MarketSnapshot = self.market.fetch_snapshot(
                symbol=self.cfg.symbol,
                timeframe=self.cfg.timeframe,
                rsi_period=self.cfg.rsi_period,
                sma_fast=self.cfg.sma_fast,
                sma_slow=self.cfg.sma_slow,
                macd_fast=self.cfg.macd_fast,
                macd_slow=self.cfg.macd_slow,
                macd_signal=self.cfg.macd_signal,
            )
            price = snap.price
            equity = self.portfolio.equity(price)

            ok, reason = self.risk.allow_trade(equity, snap.volatility_pct)
            if not ok:
                self.log_message.emit(f"⛔ Ordem manual bloqueada pelo risco: {reason}")
                return
            ok, reason = self.safety.can_trade(equity)
            if not ok:
                self.log_message.emit(f"⛔ Ordem manual bloqueada pelo safety: {reason}")
                return

            position_value = self.portfolio.position_value(price)
            amount = self.risk.order_size(side, equity, position_value, price) * (pct / 100.0)
            if amount <= 0:
                self.log_message.emit("⚠ Ordem manual ignorada: tamanho calculado zero.")
                return

            self._execute_trade(side, price, amount, exit_reason=("manual" if side == "SELL" else None))
            self.log_message.emit(f"🖐 Ordem MANUAL executada: {side} ({pct:.0f}% do tamanho)")
        except Exception as e:  # noqa: BLE001
            self.error_occurred.emit(f"Ordem manual falhou: {e}")

    # ---------- Loop principal ----------
    def run(self) -> None:
        self._running = True
        self.log_message.emit(
            f"Engine iniciado em modo {self.cfg.mode.upper()} | par {self.cfg.symbol} | tf {self.cfg.timeframe}"
        )
        while self._running:
            try:
                self._tick()
                self.safety.beat()  # 🛡 heartbeat: prova que loop está vivo
            except Exception as e:  # noqa: BLE001
                self.error_occurred.emit(str(e))
            for _ in range(self.cfg.poll_interval_seconds * 10):
                if not self._running:
                    break
                time.sleep(0.1)
        self.log_message.emit("Engine finalizado.")

    def _maybe_refresh_news(self) -> int:
        now = time.time()
        if now - self._news_last_ts > self._news_ttl_seconds:
            self._news_cache = self.news.fetch_score()
            self._news_last_ts = now
            score, headlines = self._news_cache
            # Sempre emite (mesmo sem headlines) para a UI atualizar status
            self.news_updated.emit(score, headlines)
        return self._news_cache[0]

    def _maybe_volume_alert(self, snap: MarketSnapshot) -> None:
        """Detecta spike de volume (>=3x média) e notifica. Anti-spam: 1 alerta por 5min."""
        try:
            df = snap.df
            if df is None or len(df) < 25:
                return
            from core.levels import analyze_levels
            highs = df["high"].to_numpy(dtype=float)
            lows = df["low"].to_numpy(dtype=float)
            closes = df["close"].to_numpy(dtype=float)
            volumes = df["volume"].to_numpy(dtype=float)
            lev = analyze_levels(highs, lows, closes, volumes)
            self._last_levels = lev  # disponível pra UI/log se quiser

            now = time.time()
            if lev.volume_spike and (now - self._last_volume_alert_ts) > 300:
                self._last_volume_alert_ts = now
                msg = (
                    f"📊 Volume ANÔMALO em {self.cfg.symbol}: "
                    f"{lev.volume_ratio:.1f}x a média recente. "
                    f"Pode preceder movimento forte."
                )
                self.log_message.emit(msg)
                if self.notifier is not None:
                    try:
                        self.notifier.notify("volume_spike", msg, level="warn")
                    except Exception:  # noqa: BLE001
                        pass
            # Aviso de proximidade de S/R (sem notify, só log silencioso)
            if lev.near_resistance and self.portfolio.base_amount > 1e-9:
                self.log_message.emit(
                    f"⚠ Preço perto de RESISTÊNCIA em {lev.resistance:.4f} "
                    f"(+{lev.distance_to_resistance_pct:.2f}%) — IA pode realizar lucro."
                )
        except Exception as e:  # noqa: BLE001
            # Nunca quebra o tick por causa do alerta
            self.log_message.emit(f"⚠ analyze_levels falhou: {e}")

    def _maybe_auto_scan(self) -> None:
        """A cada 30min, escaneia outros pares. Se houver outro MUITO melhor, sugere troca.
        NÃO troca sozinha quando há posição aberta — só quando flat."""
        if not self._auto_scan_enabled:
            return
        if self.portfolio.base_amount > 1e-9:
            return  # nunca troca par com posição aberta
        if self._auto_scan_wait_win:
            return  # respeita ciclo: trabalha no par atual até fechar trade vencedor
        now = time.time()
        if now - self._auto_scan_last < self._auto_scan_interval:
            return
        self._auto_scan_last = now
        try:
            from core.scanner import MarketScanner, default_symbols_for_quote
            quote = self.cfg.symbol.split("/")[-1]
            symbols = default_symbols_for_quote(quote)
            scanner = MarketScanner(client=self.client)
            report = scanner.scan(symbols=symbols, timeframe=self.cfg.timeframe)
            best = report.best
            if best is None or best.symbol == self.cfg.symbol:
                return
            current = next((p for p in report.top if p.symbol == self.cfg.symbol), None)
            current_q = current.quality if current else 0
            # Troca quando o melhor está acima do atual com folga mínima.
            # Critério agressivo para reduzir tempo parado e aumentar aprendizado.
            if best.quality >= 55 and (best.quality - current_q) >= 4:
                old_symbol = self.cfg.symbol
                msg = (
                    f"🎯 Auto-scan: {best.symbol} (q={best.quality}) está MUITO melhor "
                    f"que {self.cfg.symbol} (q={current_q}). Trocando par."
                )
                self.log_message.emit(msg)
                if self.notifier is not None:
                    self.notifier.notify("auto_pair_switch", msg, level="info")
                self.cfg.symbol = best.symbol  # troca em runtime (próximo tick usa novo par)
                # Após trocar, só volta a trocar depois de novo trade vencedor.
                self._auto_scan_wait_win = True
                self.log_message.emit(
                    f"🔒 Auto-scan travado em {self.cfg.symbol}: aguardando trade vencedor para próxima troca "
                    f"(antes: {old_symbol})."
                )
        except Exception as e:  # noqa: BLE001
            self.log_message.emit(f"⚠ auto-scan falhou: {e}")

    # ---------- Shadow trading (paper-trade observador) ----------
    def _shadow_close(self, exit_price: float, reason: str) -> None:
        """Fecha shadow position e alimenta LearningEngine."""
        if self._paper_pos is None:
            return
        pos = self._paper_pos
        entry = float(pos["entry_price"])
        pnl_pct = (exit_price - entry) / entry * 100.0 if entry > 0 else 0.0
        win = pnl_pct > 0
        self._paper_stats["trades"] += 1
        if win:
            self._paper_stats["wins"] += 1
        self._paper_stats["total_pnl_pct"] += pnl_pct
        if self.learning is not None:
            try:
                # Mapeia exit_reason do shadow para enum aceito pelo LearningEngine
                exit_map = {
                    "SHADOW_SL": "SL",
                    "SHADOW_TP": "TP",
                    "SHADOW_SELL_signal": "SELL_signal",
                    "SHADOW_TIMEOUT_6h": "EOD",
                }
                duration = max(0.0, time.time() - float(pos.get("entry_ts", time.time())))
                pnl_abs = (exit_price - entry) * 1.0  # 1 unidade virtual
                outcome = TradeOutcome(
                    pnl=pnl_abs,
                    pnl_pct=pnl_pct,
                    win=win,
                    regime=str(pos.get("regime", "?")),
                    sentiment_at_entry=int(pos.get("sentiment", 0)),
                    quality_at_entry=int(pos.get("quality", 0)),
                    exit_reason=exit_map.get(reason, "manual"),  # type: ignore[arg-type]
                    duration_seconds=duration,
                )
                msgs = self.learning.record_trade(outcome)
                for m in msgs:
                    self.log_message.emit(f"📚 [shadow] {m}")
                if self.agent is not None and self.agent_baseline is not None:
                    self.learning.apply_to_agent(self.agent, self.agent_baseline)
                self.learning_updated.emit(self.learning.stats())
            except Exception as e:  # noqa: BLE001
                self.log_message.emit(f"⚠ shadow learning falhou: {e}")
        wr = self._paper_stats["wins"] / max(1, self._paper_stats["trades"]) * 100
        emoji = "✅" if win else "❌"
        self.log_message.emit(
            f"👁 SHADOW {emoji} {reason} @ {exit_price:.4f} | "
            f"PnL {pnl_pct:+.2f}% | total: {self._paper_stats['trades']} trades, "
            f"{wr:.0f}% win-rate"
        )
        self._paper_pos = None

    def _shadow_tick(self, price: float, final_signal: str, agent_dec) -> None:
        """Mantém posição virtual paralela. Aprende sem operar de verdade."""
        if self._paper_pos is not None:
            sl = float(self._paper_pos.get("sl", 0.0))
            tp = float(self._paper_pos.get("tp", 0.0))
            if sl > 0 and price <= sl:
                self._shadow_close(price, "SHADOW_SL")
                return
            if tp > 0 and price >= tp:
                self._shadow_close(price, "SHADOW_TP")
                return
            if final_signal == "SELL":
                self._shadow_close(price, "SHADOW_SELL_signal")
                return
            if time.time() - float(self._paper_pos.get("entry_ts", 0)) > 6 * 3600:
                self._shadow_close(price, "SHADOW_TIMEOUT_6h")
                return
            return
        if final_signal != "BUY" or agent_dec is None:
            return
        quality = int(getattr(agent_dec, "quality", 0))
        if quality < 40:
            return
        sl = float(getattr(agent_dec, "stop_loss", 0.0)) or price * 0.99
        tp = float(getattr(agent_dec, "take_profit", 0.0)) or price * 1.02
        self._paper_pos = {
            "entry_price": price,
            "entry_ts": time.time(),
            "sl": sl,
            "tp": tp,
            "regime": getattr(agent_dec, "regime", "?"),
            "sentiment": int(getattr(agent_dec, "sentiment", 0)),
            "quality": quality,
        }
        self.log_message.emit(
            f"👁 SHADOW BUY @ {price:.4f} | SL={sl:.4f} TP={tp:.4f} "
            f"| Q{quality} | aprendendo sem operar"
        )

    def _tick(self) -> None:
        # Auto-scan periódico: IA pode trocar de par sozinha se achar um melhor
        self._maybe_auto_scan()
        snap: MarketSnapshot = self.market.fetch_snapshot(
            symbol=self.cfg.symbol,
            timeframe=self.cfg.timeframe,
            rsi_period=self.cfg.rsi_period,
            sma_fast=self.cfg.sma_fast,
            sma_slow=self.cfg.sma_slow,
            macd_fast=self.cfg.macd_fast,
            macd_slow=self.cfg.macd_slow,
            macd_signal=self.cfg.macd_signal,
        )
        price = snap.price

        self.price_updated.emit(price, time.time() * 1000)

        # Análise de níveis (S/R) e volume — barato, roda a cada tick
        self._maybe_volume_alert(snap)

        news_score = self._maybe_refresh_news()
        decision = self.strategy.decide(snap, news_score=news_score)
        self.decision_updated.emit(decision)
        equity = self.portfolio.equity(price)
        self.risk.roll_day_if_needed(equity)
        self.portfolio_updated.emit(equity, self.portfolio.cash_usdt, self.portfolio.base_amount)
        self.risk_updated.emit(self.risk.status(equity))
        self.safety_updated.emit(self.safety.status())

        # Camada do trader sênior (cérebro)
        has_position = self.portfolio.base_amount > 1e-9
        if self.agent is not None:
            agent_dec: AgentDecision = self.agent.evaluate(snap, decision, has_position, news_score=news_score)
            self.agent_updated.emit(agent_dec)

            # Saída forçada por SL/TP tem prioridade absoluta
            if has_position:
                must_exit, reason = self.agent.check_exit(price)
                if must_exit:
                    self.log_message.emit(f"🛡️ {reason}")
                    amount = self.portfolio.base_amount
                    exit_kind = "TP" if "TAKE-PROFIT" in reason else "SL"
                    entry_for_pct = float(self.portfolio.avg_entry or price)
                    self._execute_trade("SELL", price, amount, exit_reason=exit_kind)
                    pnl_pct_close = (price - entry_for_pct) / entry_for_pct * 100.0 if entry_for_pct > 0 else 0.0
                    self.agent.on_position_closed(pnl_pct_close)
                    return
            final_signal = agent_dec.signal
            if agent_dec.reasons:
                self.log_message.emit("🧠 " + " | ".join(agent_dec.reasons[:3]))
        else:
            final_signal = decision.signal
            agent_dec = None  # type: ignore[assignment]

        # Shadow trading: roda SEMPRE (independente de IA ativada/desativada).
        # Aprende observando o mercado real, sem mexer no dinheiro.
        if self._paper_enabled:
            self._shadow_tick(price, final_signal, agent_dec)

        # 🎓 Conselheiro Senior LLM (não bloqueia nada, só comenta — fire-and-forget)
        try:
            self._maybe_ask_senior(snap, decision, agent_dec, final_signal, price, news_score, equity)
        except Exception:  # noqa: BLE001
            pass

        # 🛡 Gatekeeper Ollama (síncrono, curto): aprova/veta a decisão.
        # - Se heurística disse BUY e Ollama discorda com confiança alta → vira HOLD.
        # - Se heurística disse HOLD por baixa qualidade e Ollama aprovaria → BUY com tamanho reduzido.
        # - Se Ollama estiver offline, a decisão da heurística passa intocada.
        gatekeeper_size_pct = 100
        _last_gk_action: str | None = None
        _last_gk_confidence: int = 0
        if (self._ai_enabled and self.gatekeeper is not None
                and getattr(self.gatekeeper.cfg, "enabled", False)
                and final_signal in ("BUY", "HOLD")
                and self.agent is not None):
            try:
                pnl_day = float(self.risk.status(equity).get("daily_pnl_pct", 0.0))
            except Exception:  # noqa: BLE001
                pnl_day = 0.0
            ctx = {
                "symbol": self.cfg.symbol,
                "price": round(price, 6),
                "regime": getattr(agent_dec, "regime", "?"),
                "rsi": round(snap.rsi, 1),
                "volatility": round(snap.volatility_pct, 3),
                "macd_hist": round(snap.macd_hist, 6),
                "sentiment": int(getattr(agent_dec, "sentiment", 0)),
                "news": int(news_score),
                "quality": int(getattr(agent_dec, "quality", 0)),
                "reasons": " | ".join((getattr(agent_dec, "reasons", []) or [])[:3]),
                "position": "comprado" if has_position else "flat",
                "pnl_day": round(pnl_day, 2),
                "mode": self.cfg.mode,
            }
            try:
                verdict = self.gatekeeper.review(
                    proposed_signal=final_signal,
                    quality=int(getattr(agent_dec, "quality", 0)),
                    min_quality=int(self.agent.cfg.min_setup_quality),
                    context=ctx,
                    has_position=has_position,
                )
            except Exception as e:  # noqa: BLE001
                self.log_message.emit(f"⚠ Gatekeeper falhou: {e}")
                verdict = None
            if verdict is not None:
                # Emite no canal do senior pra UI mostrar (reaproveita widget existente)
                try:
                    self.senior_updated.emit({
                        "comment": verdict.reason,
                        "confidence": verdict.confidence,
                        "agree": verdict.agree,
                        "model": getattr(self.gatekeeper.cfg, "model", "ollama"),
                        "latency_sec": verdict.latency_sec,
                        "error": verdict.error,
                        "decision": verdict.final_signal,
                        "source": "gatekeeper",
                    })
                except Exception:  # noqa: BLE001
                    pass
                if verdict.action == "veto":
                    if (
                        bool(getattr(self.cfg, "decisive_mode", False))
                        and final_signal == "BUY"
                        and not has_position
                    ):
                        gatekeeper_size_pct = min(
                            gatekeeper_size_pct,
                            max(10, int(getattr(self.cfg, "decisive_min_entry_pct", 35))),
                        )
                        self.log_message.emit(
                            "⚡ Modo decisivo ativo: veto ignorado com tamanho reduzido "
                            f"({gatekeeper_size_pct}%). Motivo gatekeeper: {verdict.reason}"
                        )
                    else:
                        self.log_message.emit(f"🛡 Gatekeeper VETOU BUY: {verdict.reason}")
                        final_signal = "HOLD"
                    if self.gatekeeper_learning is not None:
                        try:
                            self.gatekeeper_learning.record_decision("veto", int(verdict.confidence))
                        except Exception:  # noqa: BLE001
                            pass
                elif verdict.action == "rescue":
                    self.log_message.emit(
                        f"🛡 Gatekeeper RESGATOU setup (tamanho {verdict.size_pct}%): {verdict.reason}"
                    )
                    final_signal = "BUY"
                    gatekeeper_size_pct = max(10, min(100, int(verdict.size_pct)))
                # action "pass" / "skip": mantém final_signal
                # Anota a ação do gatekeeper para correlacionar com outcome ao fechar
                _last_gk_action = verdict.action
                _last_gk_confidence = int(verdict.confidence)

        if not self._ai_enabled or final_signal == "HOLD":
            if not self._ai_enabled:
                self._reset_opportunity_mode("IA desativada")
                self._idle_hold_ticks = 0
                return
            if not has_position:
                self._idle_hold_ticks += 1
                self._maybe_activate_opportunity_mode()
            return

        ok, reason = self.risk.allow_trade(equity, snap.volatility_pct)
        if not ok:
            self.log_message.emit(f"Trade bloqueado pelo risco: {reason}")
            return
        # 🛡 Camada extra: circuit breaker semanal + rate limit
        ok, reason = self.safety.can_trade(equity)
        if not ok:
            self.log_message.emit(f"🛡 Safety bloqueou: {reason}")
            return

        position_value = self.portfolio.position_value(price)
        amount = self.risk.order_size(final_signal, equity, position_value, price)
        # Aplica multiplicador dinâmico de tamanho (aprende com sequências de wins/losses)
        if final_signal == "BUY" and self._size_multiplier != 1.0:
            amount *= self._size_multiplier
        # Sanity-mode: setup mediano → tamanho proporcional à qualidade
        if final_signal == "BUY" and agent_dec is not None:
            sf = float(getattr(agent_dec, "size_factor", 1.0))
            if sf < 1.0:
                amount *= sf
                self.log_message.emit(
                    f"📉 Sanity-mode: tamanho reduzido para {sf*100:.0f}% "
                    f"(qualidade {getattr(agent_dec, 'quality', 0)})"
                )
        # 🛡 Gatekeeper resgate: reduz tamanho quando o trade foi liberado por LLM
        if final_signal == "BUY" and gatekeeper_size_pct < 100:
            amount *= gatekeeper_size_pct / 100.0
        if amount <= 0:
            if final_signal == "BUY" and not has_position:
                self._idle_hold_ticks += 1
                self._maybe_activate_opportunity_mode()
            return
        # Captura contexto antes de executar (para o LearningEngine ao fechar)
        ctx_before = None
        if self.agent is not None and final_signal == "BUY":
            ctx_before = {
                "regime": getattr(agent_dec, "regime", "?"),
                "sentiment": int(getattr(agent_dec, "sentiment", 0)),
                "quality": int(getattr(agent_dec, "quality", 0)),
            }
        exit_kind_arg = "SELL_signal" if final_signal == "SELL" else None
        base_before = self.portfolio.base_amount
        self._execute_trade(final_signal, price, amount, exit_reason=exit_kind_arg)
        buy_filled = final_signal != "BUY" or (self.portfolio.base_amount > base_before + 1e-9)
        if final_signal == "BUY" and not buy_filled:
            # Ex.: minNotional/lotSize/saldo insuficiente bloqueou execução real.
            self._idle_hold_ticks += 1
            self._maybe_activate_opportunity_mode()
        else:
            self._idle_hold_ticks = 0
            self._reset_opportunity_mode("entrada executada")
        # Notifica o agente para atualizar SL/TP/cooldown
        if self.agent is not None:
            if final_signal == "BUY":
                self.agent.on_position_opened(price)
                if ctx_before is not None:
                    self._open_trade_ctx = {
                        **ctx_before,
                        "entry_price": price,
                        "entry_ts": time.time(),
                        "gk_action": _last_gk_action,
                        "gk_confidence": _last_gk_confidence,
                    }
            elif final_signal == "SELL" and self.portfolio.base_amount <= 1e-9:
                # Pega PnL% do trade que acabou de fechar (último trade no portfolio)
                pnl_pct_close = 0.0
                try:
                    last_t = self.portfolio.trades[-1]
                    if last_t.side == "SELL" and last_t.price > 0:
                        # `pnl` no Trade é absoluto. Convertemos pra % usando entry registrado antes.
                        ctx = self._open_trade_ctx or {}
                        entry = float(ctx.get("entry_price", 0.0))
                        if entry > 0:
                            pnl_pct_close = (last_t.price - entry) / entry * 100.0
                except Exception:
                    pass
                self.agent.on_position_closed(pnl_pct_close)

    def _maybe_activate_opportunity_mode(self) -> None:
        """Relaxa filtros da IA após HOLD prolongado para aumentar entradas."""
        if self.agent is None:
            return
        if self.portfolio.base_amount > 1e-9:
            self._reset_opportunity_mode("posição aberta")
            return
        # Ativa após alguns minutos sem entrada (depende do poll_interval).
        if self._idle_hold_ticks < 18:
            return

        a = self.agent.cfg
        if not self._opportunity_mode_active:
            self._opportunity_profile_backup = {
                "min_setup_quality": int(a.min_setup_quality),
                "adx_min": float(a.adx_min),
                "volume_min_ratio": float(a.volume_min_ratio),
            }

        # Escala em degraus para evitar mudança brusca.
        stage = 1 if self._idle_hold_ticks < 36 else 2
        quality_drop = 10 if stage == 1 else 15
        adx_drop = 3.0 if stage == 1 else 5.0
        vol_drop = 0.20 if stage == 1 else 0.35

        a.min_setup_quality = max(
            int(a.min_setup_quality_floor + 2),
            int((self._opportunity_profile_backup or {}).get("min_setup_quality", a.min_setup_quality)) - quality_drop,
        )
        a.adx_min = max(5.0, float((self._opportunity_profile_backup or {}).get("adx_min", a.adx_min)) - adx_drop)
        a.volume_min_ratio = max(
            0.20,
            float((self._opportunity_profile_backup or {}).get("volume_min_ratio", a.volume_min_ratio)) - vol_drop,
        )

        if not self._opportunity_mode_active:
            self._opportunity_mode_active = True
            self.log_message.emit(
                "🧠 Modo oportunidade ON: HOLD prolongado detectado; "
                f"filtros relaxados (Qmin={a.min_setup_quality}, ADX>={a.adx_min:.1f}, Vol>={a.volume_min_ratio:.2f}x)."
            )

    def _reset_opportunity_mode(self, reason: str) -> None:
        if not self._opportunity_mode_active or self.agent is None:
            return
        backup = self._opportunity_profile_backup or {}
        self.agent.cfg.min_setup_quality = int(backup.get("min_setup_quality", self.agent.cfg.min_setup_quality))
        self.agent.cfg.adx_min = float(backup.get("adx_min", self.agent.cfg.adx_min))
        self.agent.cfg.volume_min_ratio = float(backup.get("volume_min_ratio", self.agent.cfg.volume_min_ratio))
        self._opportunity_mode_active = False
        self._opportunity_profile_backup = None
        self.log_message.emit(f"🧠 Modo oportunidade OFF: restaurando filtros ({reason}).")

    # ---------- Conselheiro Senior LLM ----------
    def _maybe_ask_senior(self, snap, decision, agent_dec, final_signal: str,
                          price: float, news_score: int, equity: float) -> None:
        """Dispara análise async do Ollama. Não bloqueia, não decide nada."""
        if self.senior is None or not self.senior.cfg.enabled:
            return
        # Monta contexto resumido para o LLM
        try:
            df = snap.df
            rsi_val = float(df["rsi"].iloc[-1]) if "rsi" in df.columns else 0.0
            ema_fast = float(df["close"].ewm(span=20).mean().iloc[-1])
            ema_slow = float(df["close"].ewm(span=50).mean().iloc[-1])
            trend = "alta" if ema_fast > ema_slow else ("baixa" if ema_fast < ema_slow else "lateral")
        except Exception:  # noqa: BLE001
            rsi_val = 0.0
            trend = "?"
        reason_str = ""
        if agent_dec is not None and getattr(agent_dec, "reasons", None):
            reason_str = " | ".join(agent_dec.reasons[:3])
        elif decision is not None:
            reason_str = getattr(decision, "reason", "?") or "?"
        position = "comprado" if self.portfolio.base_amount > 1e-9 else "flat"
        try:
            pnl_day = float(self.risk.status(equity).get("daily_pnl_pct", 0.0))
        except Exception:  # noqa: BLE001
            pnl_day = 0.0
        ctx = {
            "symbol": self.cfg.symbol,
            "price": round(price, 4),
            "trend": trend,
            "rsi": round(rsi_val, 1),
            "decision": final_signal,
            "reason": reason_str[:200],
            "news_score": int(news_score),
            "position": position,
            "pnl_day": round(pnl_day, 2),
        }
        def _on_advice(adv) -> None:
            try:
                self.senior_updated.emit({
                    "comment": adv.comment,
                    "confidence": adv.confidence,
                    "agree": adv.agree,
                    "model": adv.model,
                    "latency_sec": adv.latency_sec,
                    "error": adv.error,
                    "decision": ctx["decision"],
                })
            except Exception:  # noqa: BLE001
                pass
        self.senior.ask(ctx, _on_advice)

    # ---------- Execução ----------
    def _execute_trade(self, side: str, price: float, amount: float,
                       exit_reason: str | None = None) -> None:
        if self.cfg.mode == "live":
            # Valida limites da exchange (minNotional/lotSize) antes de mandar
            try:
                from core.exchange_limits import fetch_limits
                ccxt_client = getattr(self.client, "client", None)
                if ccxt_client is not None:
                    limits = fetch_limits(ccxt_client, self.cfg.symbol)
                    ok, adj_amount, reason = limits.validate_and_round(price, amount)
                    if not ok:
                        self.error_occurred.emit(
                            f"Ordem rejeitada localmente ({self.cfg.symbol}): {reason}"
                        )
                        return
                    amount = adj_amount
            except Exception as e:  # noqa: BLE001
                # Falhar a checagem de limites NÃO deve travar o trade — apenas avisa.
                self.log_message.emit(f"⚠ não consegui validar limites: {e}")

            try:
                self.client.place_market_order(self.cfg.symbol, side.lower(), amount)
                self.log_message.emit(f"📡 Ordem REAL enviada: {side} {amount} @ {price}")
            except Exception as e:  # noqa: BLE001
                self.error_occurred.emit(f"Falha ordem real: {e}")
                return

        pnl = 0.0
        closed_position = False
        if side == "BUY":
            if price <= 0 or self.portfolio.cash_usdt <= 0:
                return
            cost = amount * price
            if cost > self.portfolio.cash_usdt:
                amount = self.portfolio.cash_usdt / price
                cost = amount * price
            if amount <= 1e-9:
                return
            new_total = self.portfolio.base_amount + amount
            if new_total > 0:
                self.portfolio.avg_entry = (
                    self.portfolio.avg_entry * self.portfolio.base_amount + price * amount
                ) / new_total
            self.portfolio.base_amount = new_total
            self.portfolio.cash_usdt -= cost
        else:
            amount = min(amount, self.portfolio.base_amount)
            if amount <= 0:
                return
            proceeds = amount * price
            pnl = (price - self.portfolio.avg_entry) * amount
            self.portfolio.base_amount -= amount
            self.portfolio.cash_usdt += proceeds
            if self.portfolio.base_amount <= 1e-9:
                self.portfolio.avg_entry = 0.0
                closed_position = True

        trade = Trade(datetime.now(), side, price, amount, pnl)
        self.portfolio.trades.append(trade)
        self.trade_executed.emit(trade)
        # Re-emite portfolio para a UI refletir o estado pós-trade neste mesmo tick
        new_equity = self.portfolio.equity(price)
        self.portfolio_updated.emit(
            new_equity, self.portfolio.cash_usdt, self.portfolio.base_amount
        )
        self.log_message.emit(
            f"✅ {side} {amount:.6f} @ {price:.2f} | PnL trade: {pnl:+.2f} USDT"
        )
        if side == "SELL" and closed_position and pnl > 0:
            # Lucro fechado: libera troca de par na próxima janela de auto-scan.
            self._auto_scan_wait_win = False
            self.log_message.emit("🔓 Auto-scan liberado: trade vencedor concluído.")

        # Persistência automática: salva posição quando abre, limpa quando fecha
        if self.position_store is not None:
            try:
                if side == "BUY" and self.portfolio.base_amount > 1e-9:
                    from core.position_store import PersistedPosition
                    plan = getattr(self.agent, "_plan", None) if self.agent else None
                    self.position_store.save(PersistedPosition(
                        symbol=self.cfg.symbol,
                        amount=self.portfolio.base_amount,
                        entry_price=self.portfolio.avg_entry,
                        stop_loss=getattr(plan, "stop_loss", 0.0) if plan else 0.0,
                        take_profit=getattr(plan, "take_profit", 0.0) if plan else 0.0,
                        opened_at=time.time(),
                        high_watermark=getattr(plan, "high_watermark", price) if plan else price,
                        trailing_active=getattr(plan, "trailing_active", False) if plan else False,
                    ))
                elif side == "SELL" and self.portfolio.base_amount <= 1e-9:
                    self.position_store.clear()
            except Exception as e:  # noqa: BLE001
                self.log_message.emit(f"⚠ position_store falhou: {e}")

        # Notificação automática (telegram/webhook se configurado)
        if self.notifier is not None:
            try:
                lvl = "warn" if (side == "SELL" and pnl < 0) else "info"
                self.notifier.notify(
                    event=f"trade_{side.lower()}",
                    message=f"{side} {self.cfg.symbol} {amount:.6f} @ {price:.2f} | PnL {pnl:+.2f}",
                    level=lvl,
                )
            except Exception:  # noqa: BLE001
                pass

        # Aprendizado por sequência (streak): wins seguidos = mais ousada, losses = mais cuidadosa
        if side == "SELL" and self.portfolio.base_amount <= 1e-9:
            size_changed = False
            if pnl > 0:
                self._win_streak += 1
                self._loss_streak = 0
                if self._win_streak >= 3 and self._size_multiplier < 1.5:
                    self._size_multiplier = min(1.5, self._size_multiplier + 0.1)
                    size_changed = True
                    self.log_message.emit(
                        f"🔥 {self._win_streak} wins seguidos — IA aumentou tamanho "
                        f"para {self._size_multiplier:.0%}"
                    )
            elif pnl < 0:
                self._loss_streak += 1
                self._win_streak = 0
                if self._loss_streak >= 2 and self._size_multiplier > 0.5:
                    self._size_multiplier = max(0.5, self._size_multiplier - 0.15)
                    size_changed = True
                    self.log_message.emit(
                        f"🥶 {self._loss_streak} losses seguidos — IA diminuiu tamanho "
                        f"para {self._size_multiplier:.0%} pra preservar capital"
                    )
                if self._loss_streak >= 4 and self.notifier is not None:
                    self.notifier.notify(
                        "loss_streak_warning",
                        f"⚠ {self._loss_streak} perdas seguidas. IA está em modo defensivo.",
                        level="warn",
                    )
            # Persiste a aprendizagem em disco (sobrevive a reinício do app)
            if size_changed and self.learning is not None:
                try:
                    self.learning.state.size_multiplier = self._size_multiplier
                    self.learning._save()
                except Exception as e:  # noqa: BLE001
                    self.log_message.emit(f"⚠ não consegui persistir size_multiplier: {e}")

        # Auditoria — persiste em data/trades.csv
        try:
            from core.paths import data_dir
            journal_append(
                data_dir() / "trades.csv",
                timestamp=trade.timestamp,
                symbol=self.cfg.symbol,
                side=side, price=price, amount=amount, pnl=pnl,
                mode=self.cfg.mode,
            )
        except Exception as e:  # noqa: BLE001
            self.error_occurred.emit(f"journal: {e}")

        # 🛡 Registra no safety pra rate limit semanal/diário/horário
        try:
            self.safety.record_trade()
        except Exception:  # noqa: BLE001
            pass

        # Aprendizado: registra resultado quando uma posição é fechada
        if (side == "SELL" and self.learning is not None
                and self._open_trade_ctx is not None
                and self.portfolio.base_amount <= 1e-9):
            ctx = self._open_trade_ctx
            entry = float(ctx.get("entry_price") or price)
            pnl_pct = (price / entry - 1.0) * 100.0 if entry > 0 else 0.0
            duration = time.time() - float(ctx.get("entry_ts") or time.time())
            outcome = TradeOutcome(
                pnl=pnl, pnl_pct=pnl_pct, win=pnl > 0,
                regime=str(ctx.get("regime", "?")),
                sentiment_at_entry=int(ctx.get("sentiment", 0)),
                quality_at_entry=int(ctx.get("quality", 0)),
                exit_reason=(exit_reason or "SELL_signal"),  # type: ignore[arg-type]
                duration_seconds=duration,
            )
            adjustments = self.learning.record_trade(outcome)
            if self.agent is not None and self.agent_baseline is not None:
                self.learning.apply_to_agent(self.agent, self.agent_baseline)
            for msg in adjustments:
                self.log_message.emit(f"📚 aprendi: {msg}")
            self.learning_updated.emit(self.learning.stats())
            # 🧠 Aprendizado do Gatekeeper — registra outcome se a entrada
            # veio de uma decisão "pass" ou "rescue" do LLM.
            if self.gatekeeper_learning is not None:
                gk_action = ctx.get("gk_action")
                if gk_action in ("pass", "rescue"):
                    try:
                        gk_msgs = self.gatekeeper_learning.record_outcome(
                            action=gk_action,
                            win=pnl > 0,
                            pnl_pct=pnl_pct,
                            confidence=int(ctx.get("gk_confidence", 0)),
                            baseline_veto=self._gk_baseline_veto,
                            baseline_rescue=self._gk_baseline_rescue,
                            gatekeeper=self.gatekeeper,
                        )
                        for msg in gk_msgs:
                            self.log_message.emit(f"🛡 gatekeeper aprendeu: {msg}")
                        try:
                            self.gatekeeper_learning_updated.emit(
                                self.gatekeeper_learning.stats()
                            )
                        except Exception:  # noqa: BLE001
                            pass
                    except Exception as e:  # noqa: BLE001
                        self.log_message.emit(f"⚠ gatekeeper learning falhou: {e}")
            self._open_trade_ctx = None


class EngineController:
    def __init__(self, worker: EngineWorker) -> None:
        self.worker = worker
        self.thread = QThread()
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.worker.stop()
        self.thread.quit()
        self.thread.wait(3000)
