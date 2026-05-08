"""Dashboard PyQt6 — UI principal do AI Trader Copilot."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import deque
from datetime import datetime
from pathlib import Path

import pyqtgraph as pg
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QIcon, QPainter, QPicture
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.engine import EngineController, Trade
from core.paths import config_path as _config_path
from core.preflight import has_blockers, has_warnings, run_preflight
from core.scanner import MarketScanner, default_symbols_for_quote
from core.strategy import Decision
from core.trader_agent import AgentConfig, AgentDecision
from core import updater
from version import APP_NAME, __version__


CONFIG_PATH = _config_path()


class BinanceCandlestickItem(pg.GraphicsObject):
    """Item de candle (OHLC) com cores estilo Binance."""

    def __init__(self) -> None:
        super().__init__()
        self._data: list[tuple[float, float, float, float, float]] = []
        self._picture = QPicture()
        self._body_half_width = 0.32

    def setData(self, data: list[tuple[float, float, float, float, float]]) -> None:
        self._data = data
        self._generate_picture()
        self.update()

    def _generate_picture(self) -> None:
        self._picture = QPicture()
        p = QPainter(self._picture)
        try:
            for x, o, c, lo, hi in self._data:
                is_up = c >= o
                col = QColor("#0ecb81") if is_up else QColor("#f6465d")
                p.setPen(pg.mkPen(col, width=1.2))
                p.setBrush(pg.mkBrush(col))

                # Em candles flat (O=H=L=C), forçamos altura mínima visual.
                base = max(abs(c), abs(o), 1.0)
                min_h = max(0.02, base * 0.0002)
                if hi <= lo:
                    hi = c + min_h
                    lo = c - min_h
                if abs(c - o) < min_h:
                    if is_up:
                        c = o + min_h
                    else:
                        c = o - min_h

                # Pavio (high/low)
                p.drawLine(pg.QtCore.QPointF(x, lo), pg.QtCore.QPointF(x, hi))

                # Corpo do candle
                top = max(o, c)
                bottom = min(o, c)
                height = max(1e-8, top - bottom)
                rect = pg.QtCore.QRectF(
                    x - self._body_half_width,
                    bottom,
                    self._body_half_width * 2,
                    height,
                )
                p.drawRect(rect)
        finally:
            p.end()

    def paint(self, p: QPainter, *args) -> None:  # noqa: ANN002
        p.drawPicture(0, 0, self._picture)

    def boundingRect(self):  # noqa: ANN201
        # PyQt6/pyqtgraph exige QRectF; QPicture pode devolver QRect.
        return pg.QtCore.QRectF(self._picture.boundingRect())


def _resource_path(rel: str) -> Path:
    """Resolve recurso tanto em dev quanto empacotado pelo PyInstaller."""
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates = [
            exe_dir / "_internal" / rel,
            exe_dir / rel,
        ]
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / rel)
        for p in candidates:
            if p.exists():
                return p
    return Path(__file__).resolve().parents[1] / rel


def _backtest_explain_for_layperson(result, symbol: str) -> str:
    """Traduz o resultado do backtest em linguagem simples (sem jargão)."""
    lines = []
    lines.append("=" * 60)
    lines.append("📖 EXPLICAÇÃO PARA QUEM NÃO É DO MERCADO")
    lines.append("=" * 60)
    lines.append(
        f"O que isso fez: simulou a IA operando {symbol} usando o histórico\n"
        f"real dos últimos {result.cfg.limit} preços. Como se ela tivesse\n"
        f"operado de verdade no passado, com seu dinheiro."
    )
    lines.append("")
    if result.total_trades == 0:
        lines.append(
            "🔸 A IA NÃO operou nenhuma vez no período testado.\n"
            "   Isso quer dizer que ela achou o mercado ruim demais o tempo todo.\n"
            "   Não é necessariamente ruim — significa que ela é cuidadosa.\n"
            "   Mas em LIVE, talvez ela também não opere muito."
        )
    else:
        ganhou = result.total_pnl_pct > 0
        if ganhou:
            lines.append(
                f"💰 Resultado: GANHOU {result.total_pnl_pct:+.2f}% no período\n"
                f"   (R$ {result.total_pnl:+.2f} sobre R$ {result.cfg.initial_balance:.2f})"
            )
        else:
            lines.append(
                f"💸 Resultado: PERDEU {result.total_pnl_pct:+.2f}% no período\n"
                f"   (R$ {result.total_pnl:+.2f} sobre R$ {result.cfg.initial_balance:.2f})"
            )
        lines.append(
            f"\n🎯 Acertos: {result.win_rate:.0f} de cada 100 trades deram lucro.\n"
            f"   ({result.wins} trades positivos vs {result.losses} negativos)"
        )
        if result.max_drawdown_pct > 0:
            lines.append(
                f"\n📉 Pior queda no caminho: {result.max_drawdown_pct:.1f}%\n"
                f"   Ou seja, em algum momento o saldo chegou a cair {result.max_drawdown_pct:.1f}%\n"
                f"   antes de recuperar. Você precisa estar emocionalmente OK\n"
                f"   com perdas temporárias desse tamanho."
            )
        if result.profit_factor > 0:
            if result.profit_factor >= 1.5:
                pf_msg = "✅ MUITO BOM — ganha bem mais do que perde."
            elif result.profit_factor >= 1.1:
                pf_msg = "🟡 OK — ganha um pouco mais do que perde."
            else:
                pf_msg = "🔴 RUIM — perde quase o que ganha."
            lines.append(f"\n⚖ Equilíbrio ganha vs perde: {result.profit_factor:.2f}  {pf_msg}")
    lines.append("")
    lines.append("👉 O QUE FAZER AGORA?")
    if result.total_trades == 0 or result.total_pnl_pct < 0:
        lines.append(
            "   ❌ NÃO ative a IA com dinheiro real ainda.\n"
            "   • Tente outro par no Briefing\n"
            "   • Ou rode em outro timeframe (1h, 4h)\n"
            "   • Ou aguarde mercado com tendência mais clara"
        )
    elif result.total_pnl_pct < 5:
        lines.append(
            "   🟡 Pode ativar com pouco dinheiro pra observar.\n"
            "   • Lucro foi pequeno — pode ser sorte\n"
            "   • Teste em períodos diferentes antes de aumentar"
        )
    else:
        lines.append(
            "   ✅ Resultado promissor.\n"
            "   • Pode ativar com cautela\n"
            "   • Comece com tamanho pequeno mesmo assim\n"
            "   • Acompanhe as primeiras horas vendo se o real bate com simulado"
        )
    lines.append("=" * 60)
    return "\n".join(lines)


class Dashboard(QMainWindow):
    def __init__(
        self,
        controller: EngineController,
        mode: str = "simulation",
        use_testnet: bool = False,
        update_manifest_url: str = "",
        require_live_confirmation: bool = True,
    ) -> None:
        super().__init__()
        self.controller = controller
        self.worker = controller.worker
        self.mode = mode
        self.use_testnet = use_testnet
        self.update_manifest_url = update_manifest_url
        self.require_live_confirmation = require_live_confirmation

        title = f"{APP_NAME} v{__version__}"
        if mode == "live" and use_testnet:
            title += "  —  🟡 TESTNET (USDT fake)"
        elif mode == "live":
            title += "  —  🔴 LIVE (dinheiro real)"
        else:
            title += "  —  🟢 Simulação"
        self.setWindowTitle(title)

        icon_path = _resource_path("assets/app.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self.resize(1120, 700)
        self.setStyleSheet("background:#0b0d10;color:#e5e7eb;")

        self._max_points = 400
        self._x: deque[float] = deque(maxlen=self._max_points)
        self._y: deque[float] = deque(maxlen=self._max_points)
        self._candle_seconds = 60
        self._candles: deque[tuple[float, float, float, float, float]] = deque(maxlen=160)
        self._active_candle_start: int | None = None
        self._active_candle_index = 0.0
        self._active_ohlc: dict[str, float] | None = None
        self._chart_only_dialog: QDialog | None = None
        self._chart_only_plot = None
        self._chart_only_item = None

        # Estado para o painel "O que está acontecendo?"
        self._state = {
            "regime": "—",
            "signal": "HOLD",
            "quality": 0,
            "sentiment": 0,
            "reasons": [],
            "has_position": False,
            "pnl_day_pct": 0.0,
            "ai_on": False,
            "paused": False,
            "news_score": 0,
            "last_trade": None,    # tupla (side, price, pnl, hora)
        }

        self._build_ui()
        self._wire_signals()
        try:
            startup_cfg = self._load_cfg()
            startup_profile = startup_cfg.get("current_risk_profile", "bom")
            self._apply_risk_profile(startup_profile, force=True)
            # Restaura estado do botão Scalping conforme config salvo
            _secs = int((startup_cfg.get("agent") or {}).get("max_trade_duration_seconds", 0))
            if _secs > 0:
                self.btn_scalping.setChecked(True)
                self.btn_scalping.setText(f"⏱ Scalping {_secs}s")
        except Exception:
            pass

    # ---------- UI ----------
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(6)

        # ===== MODE BANNER (claro e visível) =====
        mode_banner = QWidget()
        if self.mode == "live" and self.use_testnet:
            mode_banner.setStyleSheet(
                "background:#4a3000;border-bottom:2px solid #f59e0b;padding:4px;"
            )
            mode_text = "🟡 TESTNET — Operando na Binance Testnet com USDT fake"
            mode_color = "#f59e0b"
        elif self.mode == "live":
            mode_banner.setStyleSheet(
                "background:#7f1d1d;border-bottom:2px solid #dc2626;padding:4px;"
            )
            mode_text = "🔴 REAL — Você está operando com DINHEIRO REAL. CUIDADO!"
            mode_color = "#ef4444"
        else:
            mode_banner.setStyleSheet(
                "background:#1f3a1f;border-bottom:2px solid #10b981;padding:4px;"
            )
            mode_text = "🟢 SIMULAÇÃO — Operando com dinheiro FAKE (teste seguro)"
            mode_color = "#10b981"

        mode_layout = QHBoxLayout(mode_banner)
        mode_layout.setContentsMargins(14, 6, 14, 6)
        self.mode_banner = mode_banner
        self.mode_label = QLabel(mode_text)
        self.mode_label.setStyleSheet(f"color:{mode_color};font-size:11px;font-weight:bold;")
        mode_layout.addWidget(self.mode_label)
        mode_layout.addStretch()
        root.addWidget(mode_banner)

        # ===== TICKER BAR (estilo Binance) =====
        # Mostra par, preço grande, variação 24h, max/min/volume.
        ticker = QWidget()
        ticker.setStyleSheet(
            "background:#181a20;border-bottom:1px solid #2b3139;"
        )
        tl = QHBoxLayout(ticker)
        tl.setContentsMargins(14, 8, 14, 8)
        tl.setSpacing(20)

        # Símbolo (ex: BTC/BRL)
        self.ticker_symbol = QLabel("—")
        self.ticker_symbol.setStyleSheet(
            "color:#f0b90b;font-size:16px;font-weight:bold;"
        )
        tl.addWidget(self.ticker_symbol)

        # Preço grande
        self.ticker_price = QLabel("—")
        self.ticker_price.setStyleSheet(
            "color:#e5e7eb;font-size:20px;font-weight:bold;"
        )
        tl.addWidget(self.ticker_price)

        # Preço secundário (mesmo ativo em outra moeda — ex: USDT se par é BRL)
        self.ticker_price_alt = QLabel("")
        self.ticker_price_alt.setStyleSheet(
            "color:#848e9c;font-size:12px;font-weight:bold;"
        )
        tl.addWidget(self.ticker_price_alt)

        # Variação 24h
        def _mini(label_text: str) -> tuple[QWidget, QLabel]:
            box = QWidget()
            v = QVBoxLayout(box)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(0)
            cap = QLabel(label_text)
            cap.setStyleSheet("color:#848e9c;font-size:10px;")
            val = QLabel("—")
            val.setStyleSheet("color:#e5e7eb;font-size:12px;font-weight:bold;")
            v.addWidget(cap)
            v.addWidget(val)
            return box, val

        box_chg, self.ticker_change = _mini("Var. 24h")
        box_high, self.ticker_high = _mini("Máx. 24h")
        box_low, self.ticker_low = _mini("Mín. 24h")
        box_vol, self.ticker_volume = _mini("Volume 24h")
        tl.addWidget(box_chg)
        tl.addWidget(box_high)
        tl.addWidget(box_low)
        tl.addWidget(box_vol)
        tl.addStretch(1)

        # Status da IA (mini-indicador)
        self.ticker_status = QLabel("● IA OFF")
        self.ticker_status.setStyleSheet(
            "color:#848e9c;font-size:12px;font-weight:bold;"
        )
        tl.addWidget(self.ticker_status)

        root.addWidget(ticker)
        # Estado pra detectar mudanças de preço (cor do tick)
        self._last_ticker_price: float = 0.0
        self._last_24h_fetch: float = 0.0

        # ===== WATCHLIST BAR (várias moedas, BRL + USDT) =====
        watch = QWidget()
        watch.setStyleSheet(
            "background:#0e1014;border-bottom:1px solid #2b3139;"
        )
        wl = QHBoxLayout(watch)
        wl.setContentsMargins(14, 6, 14, 6)
        wl.setSpacing(18)
        # Lista de coins monitoradas (sempre mostra cotação BRL + USDT)
        self._watch_coins = ["BTC", "ETH", "BNB", "XRP", "SOL", "ADA"]
        self._watch_labels: dict[str, QLabel] = {}
        for coin in self._watch_coins:
            box = QWidget()
            bl = QVBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.setSpacing(0)
            cap = QLabel(coin)
            cap.setStyleSheet("color:#f0b90b;font-size:11px;font-weight:bold;")
            val = QLabel("—")
            val.setStyleSheet("color:#e5e7eb;font-size:11px;")
            bl.addWidget(cap)
            bl.addWidget(val)
            wl.addWidget(box)
            self._watch_labels[coin] = val
        wl.addStretch(1)
        refresh_lbl = QLabel("⟲ 60s")
        refresh_lbl.setStyleSheet("color:#848e9c;font-size:10px;")
        wl.addWidget(refresh_lbl)
        # Watchlist oculta por padrão (pode ser reativada via self._watch_bar.setVisible(True))
        self._watch_bar = watch
        watch.setVisible(False)
        root.addWidget(watch)
        self._last_watch_fetch: float = 0.0

        # ===== BANNER RESULTADO DO TRADE =====
        self._trade_result_banner = QLabel("")
        self._trade_result_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._trade_result_banner.setStyleSheet(
            "font-size:14px;font-weight:bold;padding:6px 14px;"
            "border-radius:6px;background:transparent;"
        )
        self._trade_result_banner.setVisible(False)
        root.addWidget(self._trade_result_banner)
        self._trade_banner_timer = QTimer(self)
        self._trade_banner_timer.setSingleShot(True)
        self._trade_banner_timer.timeout.connect(
            lambda: self._trade_result_banner.setVisible(False)
        )

        # Painel narrativo amigável ("O que está acontecendo?")
        story_box = QWidget()
        story_box.setStyleSheet(
            "background:#111827;border:1px solid #374151;border-radius:8px;"
        )
        story_layout = QVBoxLayout(story_box)
        story_layout.setContentsMargins(14, 10, 14, 10)
        story_title = QLabel("💬 O que está acontecendo agora")
        story_title.setStyleSheet("color:#94a3b8;font-size:12px;")
        self.story_label = QLabel(
            "Aguardando os primeiros dados do mercado…"
        )
        self.story_label.setWordWrap(True)
        self.story_label.setStyleSheet(
            "color:#e5e7eb;font-size:13px;line-height:1.4;"
        )
        story_layout.addWidget(story_title)
        story_layout.addWidget(self.story_label)
        root.addWidget(story_box)

        # ===== 🧠 CÉREBRO DA IA — painel de aprendizado ao vivo =====
        brain_box = QWidget()
        brain_box.setStyleSheet(
            "background:#0b1220;border:1px solid #f0b90b;border-radius:8px;"
        )
        brain_layout = QVBoxLayout(brain_box)
        brain_layout.setContentsMargins(14, 8, 14, 8)
        brain_layout.setSpacing(4)

        # Linha de título + botão de expandir
        brain_title_row = QHBoxLayout()
        brain_title = QLabel("🧠 CÉREBRO DA IA — aprendendo ao vivo")
        brain_title.setStyleSheet("color:#f0b90b;font-size:12px;font-weight:bold;")
        self._btn_brain_toggle = QPushButton("▼ detalhes")
        self._btn_brain_toggle.setCheckable(True)
        self._btn_brain_toggle.setStyleSheet(
            "padding:1px 6px;background:transparent;color:#94a3b8;"
            "border:none;font-size:10px;"
        )
        brain_title_row.addWidget(brain_title)
        brain_title_row.addStretch(1)
        brain_title_row.addWidget(self._btn_brain_toggle)
        brain_layout.addLayout(brain_title_row)

        # Linha 1 (sempre visível): trades / win-rate / pnl / shadow status
        brain_row1 = QHBoxLayout()
        brain_row1.setSpacing(12)
        self.brain_trades = QLabel("Trades: 0")
        self.brain_winrate = QLabel("Win-rate: --")
        self.brain_pnl = QLabel("PnL acumulado: --")
        self.brain_shadow = QLabel("Shadow: ⏸")
        for lbl in (self.brain_trades, self.brain_winrate,
                    self.brain_pnl, self.brain_shadow):
            lbl.setStyleSheet("color:#e5e7eb;font-size:10px;")
            brain_row1.addWidget(lbl)
        brain_row1.addStretch(1)
        brain_layout.addLayout(brain_row1)

        # ---- Detalhe colapsável (oculto por padrão) ----
        self._brain_detail = QWidget()
        brain_detail_layout = QVBoxLayout(self._brain_detail)
        brain_detail_layout.setContentsMargins(0, 4, 0, 0)
        brain_detail_layout.setSpacing(4)

        # Linha 2: multiplicadores ativos
        brain_row2 = QHBoxLayout()
        brain_row2.setSpacing(12)
        self.brain_mult_q = QLabel("Qualidade: 1.00x")
        self.brain_mult_sl = QLabel("SL: 1.00x")
        self.brain_mult_tp = QLabel("TP: 1.00x")
        self.brain_mult_size = QLabel("Tamanho: 1.00x")
        for lbl in (self.brain_mult_q, self.brain_mult_sl,
                    self.brain_mult_tp, self.brain_mult_size):
            lbl.setStyleSheet("color:#94a3b8;font-size:10px;")
            brain_row2.addWidget(lbl)
        brain_row2.addStretch(1)
        brain_detail_layout.addLayout(brain_row2)

        # Linha 3: melhor / pior contexto
        brain_row3 = QHBoxLayout()
        brain_row3.setSpacing(12)
        self.brain_best = QLabel("Melhor contexto: —")
        self.brain_worst = QLabel("Pior contexto: —")
        self.brain_best.setStyleSheet("color:#0ecb81;font-size:10px;")
        self.brain_worst.setStyleSheet("color:#f6465d;font-size:10px;")
        self.brain_best.setWordWrap(True)
        self.brain_worst.setWordWrap(True)
        brain_row3.addWidget(self.brain_best)
        brain_row3.addWidget(self.brain_worst)
        brain_row3.addStretch(1)
        brain_detail_layout.addLayout(brain_row3)

        # Linha 4: último ajuste automático
        self.brain_adjust = QLabel("Último ajuste: (nenhum ainda — aguardando 1º trade)")
        self.brain_adjust.setStyleSheet("color:#f0b90b;font-size:11px;font-style:italic;")
        self.brain_adjust.setWordWrap(True)
        brain_detail_layout.addWidget(self.brain_adjust)

        # 🛡 Linha de proteções
        safety_row = QHBoxLayout()
        self.safety_label = QLabel("🛡 Proteções: aguardando…")
        self.safety_label.setStyleSheet("color:#0ecb81;font-size:11px;")
        self.safety_label.setWordWrap(True)
        self.btn_safety_reset = QPushButton("Reset proteção")
        self.btn_safety_reset.setStyleSheet(
            "padding:2px 8px;background:#1f2937;color:#e5e7eb;border:1px solid #374151;font-size:10px;"
        )
        self.btn_safety_reset.setToolTip("Destrava manualmente o circuit breaker semanal/rate limit")
        self.btn_safety_reset.clicked.connect(self._reset_safety)
        safety_row.addWidget(self.safety_label, stretch=1)
        safety_row.addWidget(self.btn_safety_reset)
        brain_detail_layout.addLayout(safety_row)

        # 🎓 Conselheiro Senior LLM
        senior_row = QHBoxLayout()
        self.senior_label = QLabel("🎓 Senior: desligado (Ollama opt-in)")
        self.senior_label.setStyleSheet("color:#9ca3af;font-size:11px;font-style:italic;")
        self.senior_label.setWordWrap(True)
        self.btn_senior_toggle = QPushButton("Ligar Senior")
        self.btn_senior_toggle.setCheckable(True)
        self.btn_senior_toggle.setStyleSheet(
            "padding:2px 8px;background:#1f2937;color:#e5e7eb;border:1px solid #374151;font-size:10px;"
        )
        self.btn_senior_toggle.setToolTip(
            "Ativa um LLM local (Ollama) que comenta cada decisão. NÃO bloqueia trades — só opina."
        )
        self.btn_senior_toggle.toggled.connect(self._toggle_senior)
        senior_row.addWidget(self.senior_label, stretch=1)
        senior_row.addWidget(self.btn_senior_toggle)
        brain_detail_layout.addLayout(senior_row)

        self._brain_detail.setVisible(False)
        brain_layout.addWidget(self._brain_detail)
        self._btn_brain_toggle.toggled.connect(
            lambda checked: (
                self._brain_detail.setVisible(checked),
                self._btn_brain_toggle.setText("▲ fechar" if checked else "▼ detalhes"),
            )
        )

        root.addWidget(brain_box)
        self._last_brain_refresh: float = 0.0


        # KPIs (grid responsivo para evitar corte de texto)
        kpis = QGridLayout()
        kpis.setHorizontalSpacing(6)
        kpis.setVerticalSpacing(2)
        # Quote atual (USDT/BRL/USDC...) — usado pra mostrar moeda certa nos KPIs
        try:
            _cfg0 = self._load_cfg()
            self._quote_ccy = (_cfg0.get("symbol", "BTC/USDT").split("/")[-1]
                               if "/" in _cfg0.get("symbol", "BTC/USDT") else "USDT")
        except Exception:  # noqa: BLE001
            self._quote_ccy = "USDT"
        self.kpi_equity = self._kpi(f"Patrimônio ({self._quote_ccy})", "—")
        self.kpi_cash = self._kpi(f"Caixa ({self._quote_ccy})", "—")
        self.kpi_position = self._kpi("Posição", "—")
        self.kpi_price = self._kpi("Preço", "—")
        self.kpi_rsi = self._kpi("RSI", "—")
        # Mantidos como objetos para compatibilidade com _on_decision/_on_agent,
        # mas não são adicionados ao grid visível (reduz poluição).
        self.kpi_macd = self._kpi("MACD hist", "—")
        self.kpi_vol = self._kpi("Volatilidade", "—")
        self.kpi_score = self._kpi("Score", "0")
        self.kpi_sentiment = self._kpi("Sentimento", "0")
        self.kpi_signal = self._kpi("Sinal", "HOLD")
        self.kpi_regime = self._kpi("Regime", "—")
        self.kpi_quality = self._kpi("Confiança", "—")
        self.kpi_pnl = self._kpi("PnL Diário", "0.00%")
        # Grid visível: 8 KPIs essenciais em 4 colunas (mais espaço, mais legível)
        kpi_visible = (
            self.kpi_signal, self.kpi_regime, self.kpi_quality, self.kpi_pnl,
            self.kpi_equity, self.kpi_cash, self.kpi_position, self.kpi_rsi,
        )
        cols = 4
        for i, w in enumerate(kpi_visible):
            w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            kpis.addWidget(w, i // cols, i % cols)
        root.addLayout(kpis)

        # Controles agrupados (UI menos poluída)
        controls_wrap = QVBoxLayout()
        controls_wrap.setSpacing(6)
        self.btn_ai = QPushButton("Ativar IA")
        self.btn_ai.setCheckable(True)
        self.btn_manual = QPushButton("Modo Manual")
        self.btn_pause = QPushButton("Pausar Operações")
        self.btn_pause.setCheckable(True)
        self.btn_testnet = QPushButton("🧪 Modo Testnet rápido")
        self.btn_apikey = QPushButton("🔑 Configurar API Key")
        self.btn_profile_sim = QPushButton("💾 Ativar Perfil SIM")
        self.btn_profile_real = QPushButton("💾 Ativar Perfil REAL")
        self.btn_preflight = QPushButton("✅ Pré-flight LIVE")
        self.btn_briefing = QPushButton("🔍 Briefing de Mercado")
        self.btn_backtest = QPushButton("📈 Backtest")
        self.btn_history = QPushButton("📜 Histórico")
        self.btn_logs = QPushButton("📄 Logs")
        self.btn_edit_config = QPushButton("⚙️ Editar Config")
        self.btn_mode = QPushButton("🔴 Modo LIVE" if self.mode != "live" else "🟢 Modo Simulação")
        self.btn_update = QPushButton("Verificar Atualização")
        self.btn_kill = QPushButton("⛔ TRAVA DE EMERGÊNCIA")
        self.btn_kill.setStyleSheet("background:#b00020;color:white;font-weight:bold;padding:8px;")
        self.btn_exit = QPushButton("🚪 Sair")
        self.btn_exit.setStyleSheet("background:#374151;color:#e5e7eb;font-weight:bold;padding:8px;border:1px solid #4b5563;")
        self.btn_senior_preset = QPushButton("🎓 Senior Setup")
        self.btn_manual_buy = QPushButton("⬆ Comprar Manual")
        self.btn_manual_sell = QPushButton("⬇ Vender Manual")
        self.btn_scalping = QPushButton("⏱ Scalping")
        self.btn_scalping.setCheckable(True)
        # Botões de perfil de risco para AI aprender
        self.btn_risk_risco = QPushButton("🔴 %RISCO")
        self.btn_risk_bom = QPushButton("🟡 %BOM")
        self.btn_risk_seguro = QPushButton("🟢 %SEGURO")
        # Tooltips em linguagem simples (pra leigo)
        self.btn_ai.setToolTip("Liga a IA pra ela operar sozinha (decide hora certa de comprar e vender).")
        self.btn_manual.setToolTip("Você manda a ordem você mesmo (compra/venda manual).")
        self.btn_pause.setToolTip("Pausa a IA sem fechar nada. Posições abertas continuam abertas.")
        self.btn_testnet.setToolTip("Liga/desliga testnet (dinheiro fake). Tenta aplicar em tempo real.")
        self.btn_apikey.setToolTip("Cadastra suas chaves da corretora. Tenta aplicar em tempo real.")
        self.btn_profile_sim.setToolTip("Ativa perfil SIM e testnet. Tenta aplicar em tempo real.")
        self.btn_profile_real.setToolTip("Ativa perfil REAL e desativa testnet. Tenta aplicar em tempo real.")
        self.btn_preflight.setToolTip("Checa se está tudo certo ANTES de operar com dinheiro real.")
        self.btn_briefing.setToolTip("A IA analisa vários pares e te diz quais estão BONS pra operar agora.")
        self.btn_backtest.setToolTip("Simula a IA operando no histórico real. Mostra se ela ganharia ou perderia.")
        self.btn_history.setToolTip("Mostra todos os trades já executados (paper e live) com filtros e estatísticas.")
        self.btn_logs.setToolTip("Abre o arquivo de log do app (data/app.log) — útil para diagnóstico.")
        self.btn_edit_config.setToolTip("Abre o config.json no editor padrão. Algumas mudanças aplicam em tempo real; outras podem exigir reinício.")
        self.btn_mode.setToolTip("Alterna entre Simulação (paper, sem risco) e LIVE (dinheiro real).")
        self.btn_senior_preset.setToolTip("Ativa todos os melhores filtros e ferramentas (ADX, volume, learning, sanity-mode).")
        self.btn_kill.setToolTip("EMERGÊNCIA: vende tudo agora e trava a IA. Use se algo estiver errado.")
        self.btn_manual_buy.setToolTip("Envia uma ordem de COMPRA manual no par atual (somente com IA desligada).")
        self.btn_manual_sell.setToolTip("Envia uma ordem de VENDA manual no par atual (somente com IA desligada).")
        self.btn_scalping.setToolTip("Ativa modo scalping por tempo: a IA fecha a posição automaticamente após X segundos, mesmo sem atingir SL/TP.")
        self.btn_risk_risco.setToolTip("🔴 Agressivo: 5% size, 5% TP, 1% SL. Alto risco, alto lucro. A IA aprende a operar neste perfil.")
        self.btn_risk_bom.setToolTip("🟡 Equilibrado: 2% size, 3% TP, 1.5% SL. Perfil padrão, retorno consisténte. A IA aprende a operar neste perfil.")
        self.btn_risk_seguro.setToolTip("🟢 Defensivo: 1% size, 2% TP, 2% SL. Baixo risco, preserva capital. A IA aprende a operar neste perfil.")
        def _style_default(btn: QPushButton) -> None:
            btn.setStyleSheet(
                "padding:6px;background:#1f2937;color:#e5e7eb;"
                "border:1px solid #374151;font-size:11px;"
            )

        group_style = (
            "QGroupBox {"
            "border:1px solid #374151;border-radius:6px;"
            "margin-top:8px;padding:6px;background:#0f172a;"
            "}"
            "QGroupBox::title {"
            "subcontrol-origin: margin; left:10px; padding:0 6px;"
            "color:#94a3b8;font-size:11px;font-weight:bold;"
            "}"
        )

        self.grp_ops = QGroupBox("Operação")
        self.grp_ops.setStyleSheet(group_style)
        ops_layout = QHBoxLayout(self.grp_ops)
        ops_layout.setSpacing(4)
        for b in (self.btn_ai, self.btn_manual, self.btn_pause, self.btn_manual_buy, self.btn_manual_sell, self.btn_scalping):
            _style_default(b)
            ops_layout.addWidget(b)
        self.btn_manual_buy.setStyleSheet("padding:6px;background:#065f46;color:white;border:1px solid #059669;font-size:11px;font-weight:bold;")
        self.btn_manual_sell.setStyleSheet("padding:6px;background:#7f1d1d;color:white;border:1px solid #ef4444;font-size:11px;font-weight:bold;")
        self.btn_kill.setStyleSheet("background:#b00020;color:white;font-weight:bold;padding:8px;")
        self.btn_exit.setStyleSheet("background:#374151;color:#e5e7eb;font-weight:bold;padding:8px;border:1px solid #4b5563;")
        ops_layout.addWidget(self.btn_kill)
        ops_layout.addWidget(self.btn_exit)
        ops_layout.addStretch(1)

        self.grp_account = QGroupBox("Conta e Segurança")
        self.grp_account.setStyleSheet(group_style)
        acc_layout = QHBoxLayout(self.grp_account)
        acc_layout.setSpacing(4)
        for b in (self.btn_testnet, self.btn_apikey, self.btn_profile_sim, self.btn_profile_real, self.btn_preflight, self.btn_mode):
            _style_default(b)
            acc_layout.addWidget(b)
        acc_layout.addStretch(1)

        self.grp_tools = QGroupBox("Análises e Sistema")
        self.grp_tools.setStyleSheet(group_style)
        tools_layout = QHBoxLayout(self.grp_tools)
        tools_layout.setSpacing(4)
        for b in (self.btn_briefing, self.btn_backtest, self.btn_history, self.btn_logs, self.btn_edit_config, self.btn_senior_preset, self.btn_update):
            _style_default(b)
            tools_layout.addWidget(b)
        self.btn_senior_preset.setStyleSheet("padding:6px;background:#059669;color:white;border:1px solid #10b981;font-size:11px;font-weight:bold;")
        tools_layout.addStretch(1)

        self.grp_risk = QGroupBox("Perfis de Risco")
        self.grp_risk.setStyleSheet(group_style)
        risk_layout = QHBoxLayout(self.grp_risk)
        risk_layout.setSpacing(4)
        for b in (self.btn_risk_risco, self.btn_risk_bom, self.btn_risk_seguro):
            _style_default(b)
            risk_layout.addWidget(b)
        risk_layout.addStretch(1)

        menu_row = QHBoxLayout()
        menu_row.setSpacing(4)
        self.btn_menu_ops = QPushButton("☰ Operação")
        self.btn_menu_account = QPushButton("☰ Conta")
        self.btn_menu_tools = QPushButton("☰ Análises")
        self.btn_menu_risk = QPushButton("☰ Risco")
        self.btn_chart_only = QPushButton("📊 Tela Gráfico")
        for b in (
            self.btn_menu_ops,
            self.btn_menu_account,
            self.btn_menu_tools,
            self.btn_menu_risk,
            self.btn_chart_only,
        ):
            b.setStyleSheet(
                "padding:6px;background:#111827;color:#e5e7eb;"
                "border:1px solid #374151;font-size:11px;font-weight:bold;"
            )
            menu_row.addWidget(b)
        menu_row.addStretch(1)

        # Inicialmente, mantém os painéis fechados para limpar a tela.
        self.grp_ops.setVisible(False)
        self.grp_account.setVisible(False)
        self.grp_tools.setVisible(False)
        self.grp_risk.setVisible(False)

        controls_wrap.addLayout(menu_row)
        controls_wrap.addWidget(self.grp_ops)
        controls_wrap.addWidget(self.grp_account)
        controls_wrap.addWidget(self.grp_tools)
        controls_wrap.addWidget(self.grp_risk)
        root.addLayout(controls_wrap)

        # Gráfico
        pg.setConfigOptions(antialias=True)
        self.plot = pg.PlotWidget(title="Preço (tempo real) — Candles")
        self.plot.setBackground("#111418")
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.plot.hideAxis("bottom")
        self.plot.getAxis("left").setTextPen(pg.mkPen("#9ca3af"))
        self.plot.getAxis("left").setTickPen(pg.mkPen("#374151"))
        self.candle_item = BinanceCandlestickItem()
        self.plot.addItem(self.candle_item)
        root.addWidget(self.plot, stretch=2)

        # Inferior: trades + reasons + log + news
        bottom = QHBoxLayout()

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Hora", "Lado", "Preço", "Qtd", "PnL"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setStyleSheet("background:#111418;")
        bottom.addWidget(self.table, stretch=2)

        # Coluna do meio
        mid = QVBoxLayout()
        reasons_header = QHBoxLayout()
        reasons_header.addWidget(QLabel("Razões da decisão atual"))
        reasons_header.addStretch(1)
        btn_news_toggle = QPushButton("📰 Notícias")
        btn_news_toggle.setCheckable(True)
        btn_news_toggle.setStyleSheet(
            "padding:1px 6px;background:#1f2937;color:#94a3b8;"
            "border:1px solid #374151;font-size:10px;"
        )
        btn_news_toggle.setToolTip("Exibe/oculta a lista de notícias recentes")
        reasons_header.addWidget(btn_news_toggle)
        mid.addLayout(reasons_header)
        self.reasons = QListWidget()
        self.reasons.setStyleSheet("background:#111418;")
        mid.addWidget(self.reasons)

        self.news_list = QListWidget()
        self.news_list.setStyleSheet("background:#111418;")
        self.news_list.setVisible(False)
        mid.addWidget(self.news_list)
        btn_news_toggle.toggled.connect(self.news_list.setVisible)
        bottom.addLayout(mid, stretch=2)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet("font-family:Consolas,monospace;background:#0b0d10;color:#cbd5e1;")
        bottom.addWidget(self.log, stretch=2)

        root.addLayout(bottom, stretch=2)

    def _kpi(self, title: str, value: str) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(6, 3, 6, 3)
        t = QLabel(title)
        t.setStyleSheet("color:#94a3b8;font-size:10px;")
        val = QLabel(value)
        val.setStyleSheet("font-size:14px;font-weight:bold;color:#e5e7eb;")
        val.setAlignment(Qt.AlignmentFlag.AlignLeft)
        v.addWidget(t)
        v.addWidget(val)
        box.value_label = val  # type: ignore[attr-defined]
        return box

    # ---------- Signals ----------
    def _wire_signals(self) -> None:
        w = self.worker
        w.price_updated.connect(self._on_price)
        w.decision_updated.connect(self._on_decision)
        w.agent_updated.connect(self._on_agent)
        w.portfolio_updated.connect(self._on_portfolio)
        w.trade_executed.connect(self._on_trade)
        w.risk_updated.connect(self._on_risk)
        w.safety_updated.connect(self._on_safety)
        w.senior_updated.connect(self._on_senior)
        w.news_updated.connect(self._on_news)
        w.learning_updated.connect(self._on_learning)
        w.log_message.connect(self._append_log)
        w.error_occurred.connect(lambda m: self._append_log(f"⚠ ERRO: {m}"))

        self.btn_ai.toggled.connect(self._toggle_ai)
        self.btn_manual.clicked.connect(lambda: self._toggle_ai(False))
        self.btn_pause.toggled.connect(self.worker.pause)
        self.btn_testnet.clicked.connect(self._open_testnet_dialog)
        self.btn_manual_buy.clicked.connect(lambda: self._manual_trade("BUY"))
        self.btn_manual_sell.clicked.connect(lambda: self._manual_trade("SELL"))
        self.btn_scalping.clicked.connect(self._open_scalping_dialog)
        self.btn_preflight.clicked.connect(self._open_preflight_dialog)
        self.btn_apikey.clicked.connect(self._open_apikey_dialog)
        self.btn_profile_sim.clicked.connect(lambda: self._activate_key_profile("sim"))
        self.btn_profile_real.clicked.connect(lambda: self._activate_key_profile("real"))
        self.btn_briefing.clicked.connect(self._open_briefing_dialog)
        self.btn_backtest.clicked.connect(self._open_backtest_dialog)
        self.btn_history.clicked.connect(self._open_history_dialog)
        self.btn_logs.clicked.connect(self._open_logs)
        self.btn_edit_config.clicked.connect(self._edit_config)
        self.btn_mode.clicked.connect(self._open_mode_dialog)
        self.btn_senior_preset.clicked.connect(self._open_senior_preset_dialog)
        self.btn_pause.toggled.connect(self._on_pause_toggled)
        self.btn_kill.clicked.connect(self.worker.trip_kill_switch)
        self.btn_update.clicked.connect(self._check_update)
        self.btn_risk_risco.clicked.connect(lambda: self._apply_risk_profile("risco"))
        self.btn_risk_bom.clicked.connect(lambda: self._apply_risk_profile("bom"))
        self.btn_risk_seguro.clicked.connect(lambda: self._apply_risk_profile("seguro"))
        self.btn_menu_ops.clicked.connect(lambda: self._toggle_menu_group("ops"))
        self.btn_menu_account.clicked.connect(lambda: self._toggle_menu_group("account"))
        self.btn_menu_tools.clicked.connect(lambda: self._toggle_menu_group("tools"))
        self.btn_menu_risk.clicked.connect(lambda: self._toggle_menu_group("risk"))
        self.btn_chart_only.clicked.connect(self._open_chart_only_window)
        self.btn_exit.clicked.connect(self._request_exit)

    def _toggle_ai(self, enabled: bool) -> None:
        if enabled and self.mode == "live" and self.require_live_confirmation:
            # Antes da confirmação, roda o pré-flight automaticamente.
            cfg = self._load_cfg()
            items = run_preflight(cfg)
            if has_blockers(items) or has_warnings(items):
                if not self._show_preflight_dialog(items, blocking=True):
                    self.btn_ai.blockSignals(True)
                    self.btn_ai.setChecked(False)
                    self.btn_ai.blockSignals(False)
                    return
            res = QMessageBox.warning(
                self,
                "Confirmar modo LIVE",
                "Você está prestes a ATIVAR a IA em modo LIVE.\n\n"
                "Ordens reais serão enviadas à corretora com seu dinheiro.\n\n"
                "Tem certeza que deseja continuar?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if res != QMessageBox.StandardButton.Yes:
                self.btn_ai.blockSignals(True)
                self.btn_ai.setChecked(False)
                self.btn_ai.blockSignals(False)
                return
        self.btn_ai.setChecked(enabled)
        self.btn_ai.setText("IA Ativada ✅" if enabled else "Ativar IA")
        self.worker.set_ai_enabled(enabled)
        self._state["ai_on"] = enabled
        self._refresh_story()

    def _toggle_menu_group(self, group: str) -> None:
        """Mostra/oculta grupos de botões ao clicar no botão de menu."""
        groups = {
            "ops": self.grp_ops,
            "account": self.grp_account,
            "tools": self.grp_tools,
            "risk": self.grp_risk,
        }
        target = groups.get(group)
        if target is None:
            return
        is_open = target.isVisible()
        for g in groups.values():
            g.setVisible(False)
        target.setVisible(not is_open)

    def _open_chart_only_window(self) -> None:
        """Abre uma tela dedicada apenas ao gráfico de candles."""
        if self._chart_only_dialog is not None and self._chart_only_dialog.isVisible():
            self._chart_only_dialog.raise_()
            self._chart_only_dialog.activateWindow()
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("📊 Gráfico de Velas")
        dlg.resize(1100, 620)
        dlg.setStyleSheet("background:#0b0d10;color:#e5e7eb;")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(8, 8, 8, 8)

        plot = pg.PlotWidget(title=f"{self.worker.cfg.symbol} — Candles")
        plot.setBackground("#111418")
        plot.showGrid(x=True, y=True, alpha=0.2)
        plot.hideAxis("bottom")
        plot.getAxis("left").setTextPen(pg.mkPen("#9ca3af"))
        plot.getAxis("left").setTickPen(pg.mkPen("#374151"))
        item = BinanceCandlestickItem()
        plot.addItem(item)
        lay.addWidget(plot)

        # Renderiza estado atual imediatamente na nova janela.
        candle_data = list(self._candles)
        if self._active_ohlc is not None:
            candle_data.append(
                (
                    self._active_candle_index,
                    self._active_ohlc["o"],
                    self._active_ohlc["c"],
                    self._active_ohlc["l"],
                    self._active_ohlc["h"],
                )
            )
        if candle_data:
            item.setData(candle_data)

        self._chart_only_dialog = dlg
        self._chart_only_plot = plot
        self._chart_only_item = item

        def _cleanup() -> None:
            self._chart_only_dialog = None
            self._chart_only_plot = None
            self._chart_only_item = None

        dlg.finished.connect(_cleanup)
        dlg.show()

    def _manual_trade(self, side: str) -> None:
        """Solicita execução manual de BUY/SELL com percentual do tamanho padrão."""
        if self.btn_ai.isChecked() or self._state.get("ai_on", False):
            QMessageBox.warning(
                self,
                "Modo Manual",
                "Desative a IA antes de enviar ordem manual.",
            )
            return

        pct, ok = QInputDialog.getDouble(
            self,
            "Ordem Manual",
            f"Percentual do tamanho para {side} (1-100):",
            100.0,
            1.0,
            100.0,
            0,
        )
        if not ok:
            return

        self.worker.manual_trade_request.emit(side, float(pct))

    # ---------- Config helpers ----------
    def _load_cfg(self) -> dict:
        try:
            with CONFIG_PATH.open("r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
        except Exception as e:  # noqa: BLE001
            # Tenta o backup automático antes de desistir (não perde API key)
            bak = CONFIG_PATH.with_suffix(".json.bak")
            if bak.exists():
                try:
                    with bak.open("r", encoding="utf-8") as f:
                        cfg = json.load(f)
                    self._append_log(
                        f"⚠ config.json corrompido ({e}). Restaurado do backup."
                    )
                    return cfg
                except Exception:  # noqa: BLE001
                    pass
            self._append_log(f"⚠ Falha ao ler config.json: {e}")
            return {}

    def _save_cfg(self, cfg: dict) -> bool:
        """Escrita ATÔMICA: grava em arquivo temporário e renomeia.
        Garante que config.json nunca fique corrompido nem perca a API key
        se o app cair durante o save (crítico em modo LIVE)."""
        import os
        import tempfile
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            # Backup do config atual antes de escrever (defesa extra)
            if CONFIG_PATH.exists():
                bak = CONFIG_PATH.with_suffix(".json.bak")
                try:
                    bak.write_bytes(CONFIG_PATH.read_bytes())
                except Exception:  # noqa: BLE001
                    pass
            # Grava em temp no MESMO diretório (pra os.replace ser atômico)
            fd, tmp_path = tempfile.mkstemp(
                prefix=".cfg_", suffix=".tmp", dir=str(CONFIG_PATH.parent)
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=2, ensure_ascii=False)
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except OSError:
                        pass
                os.replace(tmp_path, CONFIG_PATH)
            except Exception:
                # Limpa temp se algo deu errado
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
            return True
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "config.json",
                                 f"Não consegui salvar o config.json:\n{e}")
            return False

    # ---------- Perfis de API key (SIM/REAL) ----------
    def _save_active_keys_to_profile(self, profile: str, silent: bool = False) -> bool:
        """Copia as chaves ativas do keyring para um perfil persistente."""
        profile = profile.lower().strip()
        if profile not in ("sim", "real"):
            return False
        try:
            from core.secrets import get_secret, set_secret
            active_key = get_secret("binance_api_key")
            active_secret = get_secret("binance_api_secret")
            if not active_key or not active_secret:
                if not silent:
                    self._append_log("⚠ Não encontrei key ativa no keyring para salvar perfil.")
                return False
            suffix = "sim" if profile == "sim" else "real"
            ok = True
            ok &= set_secret(f"binance_api_key_{suffix}", active_key)
            ok &= set_secret(f"binance_api_secret_{suffix}", active_secret)
            ok &= set_secret(f"broker_api_key_{suffix}", active_key)
            ok &= set_secret(f"broker_api_secret_{suffix}", active_secret)
            if ok and not silent:
                self._append_log(f"💾 Perfil {profile.upper()} salvo no keyring.")
            return bool(ok)
        except Exception as e:  # noqa: BLE001
            if not silent:
                self._append_log(f"⚠ Falha ao salvar perfil {profile.upper()}: {e}")
            return False

    def _activate_key_profile(self, profile: str) -> None:
        """Ativa o perfil SIM/REAL no keyring e ajusta testnet no config."""
        profile = profile.lower().strip()
        if profile not in ("sim", "real"):
            return
        try:
            from core.secrets import get_secret, set_secret
            suffix = "sim" if profile == "sim" else "real"
            key = get_secret(f"binance_api_key_{suffix}")
            secret = get_secret(f"binance_api_secret_{suffix}")
            if not key or not secret:
                QMessageBox.warning(
                    self,
                    "Perfil não encontrado",
                    f"Perfil {profile.upper()} ainda não foi salvo.\n\n"
                    "Abra 'Configurar API Key', salve a chave desejada e tente novamente.",
                )
                return
            ok = True
            ok &= set_secret("binance_api_key", key)
            ok &= set_secret("binance_api_secret", secret)
            ok &= set_secret("broker_api_key", key)
            ok &= set_secret("broker_api_secret", secret)
            if not ok:
                QMessageBox.warning(self, "Keyring", "Falha ao ativar perfil no keyring.")
                return

            cfg = self._load_cfg()
            use_testnet = profile == "sim"
            cfg["mode"] = "live"
            cfg["use_testnet"] = use_testnet
            cfg["api_key"] = "***KEYRING***"
            cfg["api_secret"] = "***KEYRING***"
            block = dict(cfg.get("broker") or {})
            block["use_testnet"] = use_testnet
            block["api_key"] = "***KEYRING***"
            block["api_secret"] = "***KEYRING***"
            cfg["broker"] = block
            if not self._save_cfg(cfg):
                return

            self._append_log(
                f"🔁 Perfil {profile.upper()} ativado | use_testnet={use_testnet}."
            )
            applied = False
            if self.worker.portfolio.base_amount > 1e-9:
                self._append_log(
                    "⚠ Perfil salvo, mas há posição aberta; aplicação runtime adiada por segurança."
                )
            else:
                runtime_block = dict(block)
                runtime_block["api_key"] = key
                runtime_block["api_secret"] = secret
                applied = self.worker.set_runtime_broker_config(
                    runtime_block,
                    mode=cfg.get("mode", self.mode),
                )
                if applied:
                    self.mode = str(cfg.get("mode", self.mode))
                    self.use_testnet = use_testnet
                    self._refresh_mode_ui()

            if applied:
                QMessageBox.information(
                    self,
                    "Perfil ativado",
                    f"✅ Perfil {profile.upper()} ativado e aplicado em tempo real.\n\n"
                    f"Testnet: {'ATIVADO 🧪' if use_testnet else 'DESATIVADO 🔴'}\n"
                    "Não precisa reiniciar o app.",
                )
            else:
                QMessageBox.information(
                    self,
                    "Perfil ativado",
                    f"✅ Perfil {profile.upper()} ativado e salvo.\n\n"
                    f"Testnet: {'ATIVADO 🧪' if use_testnet else 'DESATIVADO 🔴'}\n\n"
                    "Não foi possível aplicar em runtime agora. A mudança entra na próxima inicialização.",
                )
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Perfil", f"Erro ao ativar perfil: {e}")

    # ---------- Configurar API Key ----------
    def _open_apikey_dialog(self) -> None:
        """Diálogo amigável para configurar API key/secret sem editar JSON."""
        cfg = self._load_cfg()
        block = dict(cfg.get("broker") or {})

        dlg = QDialog(self)
        dlg.setWindowTitle("🔑 Configurar API Key da corretora")
        dlg.setMinimumWidth(520)
        dlg.setStyleSheet("background:#0b0d10;color:#e5e7eb;")
        v = QVBoxLayout(dlg)

        # Aviso de segurança
        warn = QLabel(
            "<b>⚠ Antes de criar a API Key na corretora:</b><br>"
            "• <b>NÃO marque</b> a permissão de <b>saque (Withdrawals)</b>.<br>"
            "• Restrinja por <b>IP</b> (apenas o seu).<br>"
            "• Guarde a Secret Key num gerenciador de senhas.<br>"
            "• Comece em <b>Testnet</b> ou com <b>valores pequenos</b>."
        )
        warn.setWordWrap(True)
        warn.setStyleSheet(
            "background:#1f2937;border:1px solid #f59e0b;border-radius:6px;"
            "padding:10px;color:#fbbf24;"
        )
        v.addWidget(warn)

        form = QFormLayout()

        # Tipo de broker
        cb_type = QComboBox()
        cb_type.addItems(["binance", "ccxt", "alpaca"])
        cb_type.setCurrentText(block.get("type", "binance"))

        # Exchange ID (só p/ ccxt)
        ed_exchange = QLineEdit(block.get("exchange_id", "binance"))
        ed_exchange.setPlaceholderText("bybit, kraken, mercado, foxbit, kucoin…")

        # Hidrata com valor real do keyring se vier placeholder
        try:
            from core.secrets import hydrate_config
            block_show = hydrate_config({"broker": dict(block)}).get("broker", block)
        except Exception:  # noqa: BLE001
            block_show = block

        ed_key = QLineEdit(block_show.get("api_key", ""))
        ed_key.setPlaceholderText("Cole sua API Key aqui")

        ed_secret = QLineEdit(block_show.get("api_secret", ""))
        ed_secret.setPlaceholderText("Cole sua Secret Key aqui")
        ed_secret.setEchoMode(QLineEdit.EchoMode.Password)

        ed_password = QLineEdit(block_show.get("password", ""))
        ed_password.setPlaceholderText("Só se a corretora exigir (KuCoin, OKX…)")
        ed_password.setEchoMode(QLineEdit.EchoMode.Password)

        cb_show = QCheckBox("Mostrar segredos")

        def _toggle_show(checked: bool) -> None:
            mode = QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            ed_secret.setEchoMode(mode)
            ed_password.setEchoMode(mode)

        cb_show.toggled.connect(_toggle_show)

        cb_testnet = QCheckBox("Usar Testnet (recomendado para começar)")
        cb_testnet.setChecked(bool(block.get("use_testnet", True)))

        # Esconde campos irrelevantes conforme o tipo
        lbl_exchange = QLabel("Exchange (ccxt):")
        lbl_pwd = QLabel("Password:")

        def _on_type_changed(text: str) -> None:
            is_ccxt = text == "ccxt"
            lbl_exchange.setVisible(is_ccxt)
            ed_exchange.setVisible(is_ccxt)
            lbl_pwd.setVisible(is_ccxt)
            ed_password.setVisible(is_ccxt)
            cb_testnet.setText(
                "Usar Paper Trading (recomendado)" if text == "alpaca"
                else "Usar Testnet (recomendado para começar)"
            )

        cb_type.currentTextChanged.connect(_on_type_changed)

        form.addRow(QLabel("Corretora:"), cb_type)
        form.addRow(lbl_exchange, ed_exchange)
        form.addRow(QLabel("API Key:"), ed_key)
        form.addRow(QLabel("Secret Key:"), ed_secret)
        form.addRow(lbl_pwd, ed_password)
        form.addRow(QLabel(""), cb_show)
        form.addRow(QLabel(""), cb_testnet)
        v.addLayout(form)

        # Aplica visibilidade inicial
        _on_type_changed(cb_type.currentText())

        # Estilo dos inputs
        for w in (ed_exchange, ed_key, ed_secret, ed_password):
            w.setStyleSheet(
                "background:#111827;color:#e5e7eb;border:1px solid #374151;"
                "padding:6px;border-radius:4px;"
            )
        cb_type.setStyleSheet(
            "background:#111827;color:#e5e7eb;border:1px solid #374151;padding:6px;"
        )

        # Botões
        box = QDialogButtonBox()
        btn_save = box.addButton("💾 Salvar", QDialogButtonBox.ButtonRole.AcceptRole)
        btn_clear = box.addButton("🗑 Limpar (sem keys)", QDialogButtonBox.ButtonRole.DestructiveRole)
        box.addButton("Cancelar", QDialogButtonBox.ButtonRole.RejectRole)
        v.addWidget(box)

        result = {"action": None}

        def _on_save() -> None:
            result["action"] = "save"
            dlg.accept()

        def _on_clear() -> None:
            ed_key.setText("")
            ed_secret.setText("")
            ed_password.setText("")
            result["action"] = "save"
            dlg.accept()

        btn_save.clicked.connect(_on_save)
        btn_clear.clicked.connect(_on_clear)
        box.rejected.connect(dlg.reject)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        # Salva
        new_block = {
            "type": cb_type.currentText(),
            "exchange_id": ed_exchange.text().strip() or "binance",
            "api_key": ed_key.text().strip(),
            "api_secret": ed_secret.text().strip(),
            "password": ed_password.text().strip(),
            "use_testnet": cb_testnet.isChecked(),
        }
        if new_block["type"] == "alpaca":
            new_block["paper"] = cb_testnet.isChecked()
            new_block["feed"] = block.get("feed", "iex")
        cfg["broker"] = new_block
        # Mantém compatibilidade com campos legados
        cfg["api_key"] = new_block["api_key"]
        cfg["api_secret"] = new_block["api_secret"]
        cfg["use_testnet"] = new_block["use_testnet"]

        # 🔐 Move segredos pro Windows Credential Manager — JSON fica com placeholder
        try:
            from core.secrets import store_secrets_from_dict
            cfg = store_secrets_from_dict(cfg)
        except Exception as e:  # noqa: BLE001
            self._append_log(f"⚠ keyring indisponível ({e}) — segredos ficam em texto.")

        if not self._save_cfg(cfg):
            return

        has_keys = bool(new_block["api_key"] and new_block["api_secret"])
        if has_keys:
            # Salva automaticamente no perfil correspondente para troca rápida.
            self._save_active_keys_to_profile(
                "sim" if new_block.get("use_testnet", True) else "real",
                silent=True,
            )
        self._append_log(
            f"💾 API Key {'configurada' if has_keys else 'limpa'} para {new_block['type']}."
        )

        applied = False
        if self.worker.portfolio.base_amount > 1e-9:
            self._append_log(
                "⚠ Broker/API salvos, mas há posição aberta; aplicação runtime adiada por segurança."
            )
        else:
            # Usa valores digitados no diálogo (antes de placeholders do keyring)
            # para o runtime não perder as credenciais.
            applied = self.worker.set_runtime_broker_config(new_block, mode=cfg.get("mode", self.mode))
            if applied:
                self.use_testnet = bool(new_block.get("use_testnet", True))
                self.mode = str(cfg.get("mode", self.mode))
                self._refresh_mode_ui()

        if applied:
            QMessageBox.information(
                self,
                "API Key salva",
                "✅ Configuração salva e aplicada em tempo real.\n\n"
                f"Corretora: {new_block['type']}"
                + (f" ({new_block['exchange_id']})" if new_block["type"] == "ccxt" else "")
                + f"\nKeys: {'✅ presentes' if has_keys else '❌ vazias (modo somente leitura)'}"
                + f"\nTestnet/Paper: {'🧪 ativo' if new_block['use_testnet'] else '🔴 DESATIVADO'}\n\n"
                "Não precisa reiniciar o app.",
            )
        else:
            QMessageBox.information(
                self,
                "API Key salva",
                "✅ Configuração salva no config.json.\n\n"
                f"Corretora: {new_block['type']}"
                + (f" ({new_block['exchange_id']})" if new_block["type"] == "ccxt" else "")
                + f"\nKeys: {'✅ presentes' if has_keys else '❌ vazias (modo somente leitura)'}"
                + f"\nTestnet/Paper: {'🧪 ativo' if new_block['use_testnet'] else '🔴 DESATIVADO'}\n\n"
                "Não foi possível aplicar em runtime agora. A mudança entra automaticamente na próxima inicialização.",
            )

    # ---------- Modo Testnet rápido ----------
    def _open_testnet_dialog(self) -> None:
        cfg = self._load_cfg()
        block = cfg.get("broker") or {}
        btype = block.get("type", "binance")
        ex_id = block.get("exchange_id", "binance")
        cur_paper = bool(block.get("use_testnet", cfg.get("use_testnet", True)))

        msg = (
            f"<b>Broker atual:</b> {btype}"
            + (f" ({ex_id})" if btype == "ccxt" else "")
            + "<br>"
            f"<b>Estado:</b> {'🧪 Testnet (fake $)' if cur_paper else '🔴 LIVE (dinheiro real)'}<br><br>"
            "Deseja <b>ativar Testnet</b> agora?<br>"
            "<small>(use_testnet/paper = true → ordens não vão para a corretora real).</small>"
        )

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Modo Testnet rápido")
        box.setText(msg)
        btn_on = box.addButton("🧪 Ativar Testnet", QMessageBox.ButtonRole.AcceptRole)
        btn_off = box.addButton("🔴 Desativar (ir LIVE)", QMessageBox.ButtonRole.DestructiveRole)
        btn_cancel = box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked == btn_cancel:
            return

        new_val = clicked == btn_on
        block["use_testnet"] = new_val
        # Para Alpaca, "paper" é o termo equivalente.
        if btype == "alpaca":
            block["paper"] = new_val
        cfg["broker"] = block
        cfg["use_testnet"] = new_val  # legado

        # Se desligando testnet, força modo simulação até o usuário trocar manualmente.
        # Isso evita que apenas clicar no botão dispare ordens reais.
        if not new_val and cfg.get("mode") == "live":
            warn = QMessageBox.warning(
                self, "Atenção",
                "Você desativou o testnet. O app continuará em SIMULAÇÃO até "
                "você editar manualmente \"mode\": \"live\" no config.json e "
                "passar pelo Pré-flight.\n\nDeseja prosseguir?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if warn != QMessageBox.StandardButton.Yes:
                return
            cfg["mode"] = "simulation"

        if not self._save_cfg(cfg):
            return

        applied = self.worker.set_runtime_testnet(new_val)
        self.use_testnet = new_val
        self._refresh_mode_ui()

        if applied:
            self._append_log(
                f"✅ Testnet alterado para {new_val} em tempo real."
            )
            QMessageBox.information(
                self,
                "Modo Testnet rápido",
                f"✅ Configuração salva e aplicada.\n\n"
                f"Testnet agora: {'ATIVADO 🧪' if new_val else 'DESATIVADO 🔴'}\n"
                "Não precisa reiniciar o app.",
            )
        else:
            self._append_log(
                f"⚠ Testnet alterado para {new_val}, mas este broker exige reinício para aplicar."
            )
            QMessageBox.information(
                self,
                "Modo Testnet rápido",
                f"✅ Configuração salva.\n\n"
                f"Testnet agora: {'ATIVADO 🧪' if new_val else 'DESATIVADO 🔴'}\n\n"
                "Este broker não suportou troca em runtime. A mudança entra na próxima inicialização.",
            )

    # ---------- Backtest ----------
    def _open_history_dialog(self) -> None:
        """Mostra todos os trades de data/trades.csv com filtros e estatísticas."""
        import csv
        from datetime import datetime
        from core.paths import data_dir
        csv_path = data_dir() / "trades.csv"

        dlg = QDialog(self)
        dlg.setWindowTitle("📜 Histórico de Trades")
        dlg.resize(950, 600)
        dlg.setStyleSheet("background:#0b0d10;color:#e5e7eb;")
        v = QVBoxLayout(dlg)

        if not csv_path.exists():
            v.addWidget(QLabel(f"Nenhum trade ainda em {csv_path}"))
            btn_ok = QPushButton("Fechar")
            btn_ok.clicked.connect(dlg.reject)
            v.addWidget(btn_ok)
            dlg.exec()
            return

        rows: list[dict] = []
        try:
            with csv_path.open("r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    rows.append(r)
        except Exception as e:  # noqa: BLE001
            v.addWidget(QLabel(f"Erro lendo CSV: {e}"))
            dlg.exec()
            return

        # Filtros
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Símbolo:"))
        cb_sym = QComboBox()
        cb_sym.addItem("(todos)")
        for s in sorted({r.get("symbol", "") for r in rows if r.get("symbol")}):
            cb_sym.addItem(s)
        filter_row.addWidget(cb_sym)
        filter_row.addWidget(QLabel("Modo:"))
        cb_mode = QComboBox()
        cb_mode.addItems(["(todos)", "live", "simulation"])
        filter_row.addWidget(cb_mode)
        filter_row.addWidget(QLabel("Lado:"))
        cb_side = QComboBox()
        cb_side.addItems(["(todos)", "BUY", "SELL"])
        filter_row.addWidget(cb_side)
        filter_row.addStretch(1)
        v.addLayout(filter_row)

        stats_label = QLabel("")
        stats_label.setStyleSheet(
            "background:#111827;border:1px solid #374151;border-radius:6px;"
            "padding:8px;color:#e5e7eb;font-family:Consolas,monospace;"
        )
        stats_label.setWordWrap(True)
        v.addWidget(stats_label)

        table = QTableWidget(0, 7)
        table.setHorizontalHeaderLabels(
            ["Data/Hora", "Símbolo", "Lado", "Preço", "Qtd", "PnL", "Modo"]
        )
        table.horizontalHeader().setStretchLastSection(True)
        table.setStyleSheet("background:#111418;color:#e5e7eb;")
        table.setSortingEnabled(True)
        v.addWidget(table, stretch=1)

        def _apply_filter() -> None:
            from PyQt6.QtGui import QColor
            sym = cb_sym.currentText()
            mode = cb_mode.currentText()
            side = cb_side.currentText()
            filt = []
            for r in rows:
                if sym != "(todos)" and r.get("symbol") != sym:
                    continue
                if mode != "(todos)" and r.get("mode") != mode:
                    continue
                if side != "(todos)" and r.get("side") != side:
                    continue
                filt.append(r)
            table.setSortingEnabled(False)
            table.setRowCount(len(filt))
            total_pnl = 0.0
            wins = 0
            losses = 0
            sells = 0
            buys = 0
            for i, r in enumerate(filt):
                ts = r.get("timestamp", "")
                try:
                    ts_fmt = datetime.fromisoformat(ts).strftime("%d/%m %H:%M:%S")
                except Exception:  # noqa: BLE001
                    ts_fmt = ts
                items = [
                    ts_fmt,
                    r.get("symbol", ""),
                    r.get("side", ""),
                    f"{float(r.get('price', 0)):,.4f}",
                    f"{float(r.get('amount', 0)):,.6f}",
                    f"{float(r.get('pnl', 0)):+,.4f}",
                    r.get("mode", ""),
                ]
                for j, val in enumerate(items):
                    item = QTableWidgetItem(val)
                    if j == 5:
                        try:
                            pnl_v = float(r.get("pnl", 0))
                            if pnl_v > 0:
                                item.setForeground(QColor("#0ecb81"))
                            elif pnl_v < 0:
                                item.setForeground(QColor("#f6465d"))
                        except Exception:  # noqa: BLE001
                            pass
                    if j == 2:
                        s = r.get("side", "")
                        if s == "BUY":
                            item.setForeground(QColor("#0ecb81"))
                        elif s == "SELL":
                            item.setForeground(QColor("#f6465d"))
                    table.setItem(i, j, item)
                try:
                    pnl_v = float(r.get("pnl", 0))
                    total_pnl += pnl_v
                    if r.get("side") == "SELL":
                        sells += 1
                        if pnl_v > 0:
                            wins += 1
                        elif pnl_v < 0:
                            losses += 1
                    else:
                        buys += 1
                except Exception:  # noqa: BLE001
                    pass
            table.setSortingEnabled(True)
            wr = (wins / sells * 100) if sells else 0.0
            color = "#0ecb81" if total_pnl >= 0 else "#f6465d"
            stats_label.setText(
                f"Total: {len(filt)} trades  |  BUY: {buys}  SELL: {sells}  |  "
                f"Wins: {wins}  Losses: {losses}  |  "
                f"Win-rate: <b>{wr:.1f}%</b>  |  "
                f"PnL acumulado: <b style='color:{color}'>{total_pnl:+,.4f}</b>"
            )

        cb_sym.currentTextChanged.connect(_apply_filter)
        cb_mode.currentTextChanged.connect(_apply_filter)
        cb_side.currentTextChanged.connect(_apply_filter)
        _apply_filter()

        btns = QHBoxLayout()
        btn_export = QPushButton("📋 Copiar caminho do CSV")
        btn_close = QPushButton("Fechar")
        btns.addWidget(btn_export)
        btns.addStretch(1)
        btns.addWidget(btn_close)
        v.addLayout(btns)

        def _copy_path() -> None:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(str(csv_path))
            self._append_log(f"📋 Caminho copiado: {csv_path}")
        btn_export.clicked.connect(_copy_path)
        btn_close.clicked.connect(dlg.reject)
        dlg.exec()

    def _open_backtest_dialog(self) -> None:
        """Roda backtest no symbol atual e mostra métricas. Read-only."""
        from core.backtest import Backtester, BacktestConfig
        cfg = self._load_cfg()
        symbol = cfg.get("symbol", "BTC/USDT")

        dlg = QDialog(self)
        dlg.setWindowTitle(f"📈 Backtest — {symbol}")
        dlg.resize(720, 560)
        v = QVBoxLayout(dlg)

        # Form simples: timeframe + qtd candles
        form = QFormLayout()
        cb_tf = QComboBox()
        cb_tf.addItems(["1m", "5m", "15m", "1h", "4h", "1d"])
        cb_tf.setCurrentText("1h")
        ed_limit = QLineEdit("720")
        ed_limit.setToolTip("Quantidade de candles (720 em 1h ≈ 30 dias)")
        form.addRow("Timeframe:", cb_tf)
        form.addRow("Candles:", ed_limit)
        v.addLayout(form)

        progress = QLabel("Pronto para rodar.")
        v.addWidget(progress)

        text = QTextEdit()
        text.setReadOnly(True)
        text.setStyleSheet("font-family:Consolas,monospace;background:#0b0d10;color:#e5e7eb;")
        v.addWidget(text, stretch=1)

        btns = QHBoxLayout()
        btn_run = QPushButton("▶ Rodar Backtest")
        btn_close = QPushButton("Fechar")
        btns.addWidget(btn_run)
        btns.addStretch(1)
        btns.addWidget(btn_close)
        v.addLayout(btns)
        btn_close.clicked.connect(dlg.reject)

        from PyQt6.QtWidgets import QApplication

        def _run():
            tf = cb_tf.currentText()
            try:
                limit = max(60, int(ed_limit.text()))
            except ValueError:
                limit = 720
            progress.setText(f"⏳ Rodando backtest {symbol} {tf} ({limit} candles)…")
            QApplication.processEvents()
            try:
                bt = Backtester(self.worker.client)
                result = bt.run(BacktestConfig(
                    symbol=symbol, timeframe=tf, limit=limit,
                    initial_balance=float(cfg.get("initial_balance_usdt", 1000)),
                    trade_size_pct=float(cfg.get("trade_size_pct", 10)),
                ))
            except Exception as e:  # noqa: BLE001
                text.setPlainText(f"❌ Erro: {e}")
                progress.setText("Falhou.")
                return
            # Veredito
            verdict_lines = []
            if result.total_trades == 0:
                verdict_lines.append("⏸  Estratégia NÃO ABRIU TRADES no período. "
                                     "Considere afrouxar filtros (qualidade mínima, regime).")
            elif result.total_pnl_pct > 5 and result.win_rate >= 50:
                verdict_lines.append("✅ APROVADO — estratégia mostra edge real no período.")
            elif result.total_pnl_pct > 0:
                verdict_lines.append("🟡 MARGINAL — lucro pequeno, pode ser sorte. "
                                     "Rode em períodos diferentes pra confirmar.")
            else:
                verdict_lines.append("🔴 REPROVADO — estratégia perdeu dinheiro no período. "
                                     "NÃO use parâmetros atuais em LIVE.")
            text.setPlainText(result.summary() + "\n\n" + "\n".join(verdict_lines))
            progress.setText(f"✅ Concluído — {result.total_trades} trades simulados.")
            text.append("\n" + _backtest_explain_for_layperson(result, cfg.get("symbol", "?")))

        btn_run.clicked.connect(_run)
        dlg.exec()

    # ---------- Briefing de Mercado ----------
    def _open_briefing_dialog(self) -> None:
        """Pede pra IA analisar vários pares e mostrar a recomendação ANTES de operar."""
        cfg = self._load_cfg()
        cur_symbol = cfg.get("symbol", "BTC/USDT")
        quote = cur_symbol.split("/")[-1] if "/" in cur_symbol else "USDT"
        symbols = default_symbols_for_quote(quote)
        # garante que o par atual também é avaliado
        if cur_symbol not in symbols:
            symbols = [cur_symbol] + symbols

        # Constrói AgentConfig a partir do config atual
        a = cfg.get("agent", {}) or {}
        agent_cfg = AgentConfig(
            extreme_vol_pct=float(a.get("extreme_vol_pct", 2.5)),
            calm_vol_pct=float(a.get("calm_vol_pct", 0.15)),
            trend_strength_min_pct=float(a.get("trend_strength_min_pct", 0.05)),
            min_setup_quality=int(a.get("min_setup_quality", 60)),
            cooldown_seconds=int(a.get("cooldown_seconds", 60)),
            stop_loss_pct=float(a.get("stop_loss_pct", 1.0)),
            take_profit_pct=float(a.get("take_profit_pct", 2.0)),
        )

        dlg = QDialog(self)
        dlg.setWindowTitle("🔍 Briefing de Mercado — análise da IA")
        dlg.resize(880, 620)
        v = QVBoxLayout(dlg)

        info = QLabel(
            f"<b>Analisando {len(symbols)} pares com cotação em {quote}…</b><br>"
            "<small>A IA está aplicando o mesmo cérebro que usa em runtime "
            "(Strategy + SeniorTraderAgent) em cada par e ranqueando por qualidade de setup.</small>"
        )
        info.setWordWrap(True)
        v.addWidget(info)

        progress = QLabel("⏳ Iniciando…")
        v.addWidget(progress)

        text = QTextEdit()
        text.setReadOnly(True)
        text.setStyleSheet("font-family:Consolas,monospace;background:#0b0d10;color:#e5e7eb;")
        v.addWidget(text, stretch=1)

        btns = QHBoxLayout()
        # Seletor manual: usuário pode escolher qualquer par analisado
        from PyQt6.QtWidgets import QComboBox, QLabel as _QLabel
        sel_label = _QLabel("Trocar para:")
        combo = QComboBox()
        combo.setMinimumWidth(140)
        btn_apply = QPushButton("✅ Usar par recomendado")
        btn_apply.setEnabled(False)
        btn_close = QPushButton("Fechar")
        btns.addWidget(sel_label)
        btns.addWidget(combo)
        btns.addSpacing(12)
        btns.addWidget(btn_apply)
        btns.addStretch(1)
        btns.addWidget(btn_close)
        v.addLayout(btns)
        btn_close.clicked.connect(dlg.reject)

        # Roda scan inline (com QApplication.processEvents pra UI não travar).
        # É rápido (~10-20s pra ~10 pares).
        from PyQt6.QtWidgets import QApplication
        scanner = MarketScanner(self.worker.client, agent_cfg=agent_cfg)

        def _on_progress(i: int, total: int, sym: str) -> None:
            progress.setText(f"⏳ Analisando {i}/{total}: {sym}")
            QApplication.processEvents()

        try:
            report = scanner.scan(symbols, timeframe=cfg.get("timeframe", "1m"),
                                   progress=_on_progress)
        except Exception as e:  # noqa: BLE001
            text.setPlainText(f"❌ Falha na análise:\n{e}")
            progress.setText("Erro.")
            dlg.exec()
            return

        progress.setText(f"✅ Análise completa — {len(report.analyses)} pares avaliados.")
        text.setPlainText(report.to_text())
        best = report.best
        # Popula combo com TODOS os pares analisados (usuário escolhe manualmente)
        combo.clear()
        for an in report.analyses:
            label = f"{an.symbol}  (Q{an.quality} {an.signal})"
            combo.addItem(label, userData=an.symbol)
        # Pré-seleciona o melhor (ou o atual)
        target = (best.symbol if best else cur_symbol)
        for i in range(combo.count()):
            if combo.itemData(i) == target:
                combo.setCurrentIndex(i)
                break
        # Habilita o botão sempre que a análise terminou (usuário decide)
        btn_apply.setEnabled(True)
        btn_apply.setText("✅ Usar par selecionado")
        # IA NÃO tem certeza se o melhor par tem qualidade < 60 ou se nem houve pick
        ia_uncertain = (best is None) or (best.quality < 60)
        if ia_uncertain:
            btn_apply.setStyleSheet(
                "background:#7f1d1d;color:#fee2e2;font-weight:bold;padding:8px;"
            )
            btn_apply.setText("⚠ Usar mesmo assim (IA recomenda AGUARDAR)")
            # Aviso destacado no topo do texto
            warning = (
                "\n" + "=" * 60 + "\n"
                "⚠  ATENÇÃO: A IA NÃO TEM CERTEZA AGORA!\n"
                + "=" * 60 + "\n"
                f"O melhor par encontrado tem qualidade "
                f"{best.quality if best else 0}/100 — abaixo do mínimo recomendado (60).\n"
                "👉 NÃO ative a IA pra operar agora. Espere 15-30 min e\n"
                "   rode o Briefing de novo. Mercado sem setup claro = não force.\n"
                + "=" * 60 + "\n\n"
            )
            text.setPlainText(warning + report.to_text())

        def _apply():
            chosen = combo.currentData()
            if not chosen:
                return
            cfg2 = self._load_cfg()
            if cfg2.get("symbol") == chosen:
                QMessageBox.information(
                    dlg, "Sem mudança",
                    f"{chosen} já é o par atual.",
                )
                return
            cfg2["symbol"] = chosen
            if not self._save_cfg(cfg2):
                return
            # O engine já suporta trocar símbolo em runtime; o próximo tick usa o novo par.
            self.worker.cfg.symbol = chosen
            self._append_log(f"💾 Par alterado para {chosen} pelo Briefing (aplicado em tempo real).")
            QMessageBox.information(
                dlg,
                "Par alterado",
                f"Symbol agora: {chosen}\n\n"
                "Aplicado em tempo real. Não precisa reiniciar.",
            )
            dlg.accept()

        btn_apply.clicked.connect(_apply)
        dlg.exec()

    # ---------- Modo LIVE / SIMULAÇÃO ----------
    def _refresh_mode_ui(self) -> None:
        title = f"{APP_NAME} v{__version__}"
        if self.mode == "live" and self.use_testnet:
            title += "  —  🟡 TESTNET (USDT fake)"
            banner_css = "background:#4a3000;border-bottom:2px solid #f59e0b;padding:4px;"
            label_text = "🟡 TESTNET — Operando na Binance Testnet com USDT fake"
            label_color = "#f59e0b"
        elif self.mode == "live":
            title += "  —  🔴 LIVE (dinheiro real)"
            banner_css = "background:#7f1d1d;border-bottom:2px solid #dc2626;padding:4px;"
            label_text = "🔴 REAL — Você está operando com DINHEIRO REAL. CUIDADO!"
            label_color = "#ef4444"
        else:
            title += "  —  🟢 Simulação"
            banner_css = "background:#1f3a1f;border-bottom:2px solid #10b981;padding:4px;"
            label_text = "🟢 SIMULAÇÃO — Operando com dinheiro FAKE (teste seguro)"
            label_color = "#10b981"

        self.setWindowTitle(title)
        self.btn_mode.setText("🔴 Modo LIVE" if self.mode != "live" else "🟢 Modo Simulação")
        if hasattr(self, "mode_banner"):
            self.mode_banner.setStyleSheet(banner_css)
        if hasattr(self, "mode_label"):
            self.mode_label.setText(label_text)
            self.mode_label.setStyleSheet(f"color:{label_color};font-size:11px;font-weight:bold;")

    def _open_mode_dialog(self) -> None:
        """Alterna mode entre 'simulation' e 'live' com confirmação dupla."""
        cfg = self._load_cfg()
        cur_mode = cfg.get("mode", "simulation")
        going_live = cur_mode != "live"

        if self.worker.portfolio.base_amount > 1e-9:
            QMessageBox.warning(
                self,
                "Troca de modo bloqueada",
                "Existe posição aberta no momento.\n\n"
                "Feche a posição antes de alternar entre LIVE e SIMULAÇÃO.",
            )
            return

        if going_live:
            # 1) Pré-flight obrigatório
            items = run_preflight(cfg)
            if has_blockers(items):
                self._show_preflight_dialog(items, blocking=False)
                QMessageBox.critical(
                    self,
                    "Bloqueado",
                    "Existem bloqueadores no Pré-flight. Resolva-os antes de "
                    "ativar o modo LIVE.",
                )
                return
            # 2) Aviso se testnet desativado
            block = cfg.get("broker") or {}
            using_testnet = bool(block.get("use_testnet", cfg.get("use_testnet", True)))
            warn_text = (
                "<b>Você está prestes a ativar o modo LIVE.</b><br><br>"
                f"Broker: <b>{block.get('type', 'binance')}</b><br>"
                f"Testnet/Paper: <b>{'🧪 ATIVO (ordens simuladas)' if using_testnet else '🔴 DESATIVADO (ORDENS REAIS!)'}</b><br><br>"
                "A IA passará a executar ordens automaticamente quando você "
                "clicar em <b>Ativar IA</b>.<br><br>"
                "Para confirmar, digite <b>LIVE</b> em maiúsculas:"
            )
            text, ok = QInputDialog.getText(
                self, "Confirmar modo LIVE", warn_text
            )
            if not ok or text.strip() != "LIVE":
                self._append_log("ℹ Ativação do modo LIVE cancelada.")
                return
            cfg["mode"] = "live"
        else:
            # Voltando para simulação — confirmação simples
            res = QMessageBox.question(
                self,
                "Voltar para Simulação",
                "Voltar para o modo SIMULAÇÃO?\n\n"
                "Nenhuma ordem real será mais enviada à corretora.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if res != QMessageBox.StandardButton.Yes:
                return
            cfg["mode"] = "simulation"

        if not self._save_cfg(cfg):
            return

        # Aplica no engine em runtime, sem reiniciar.
        self.worker.set_runtime_mode(cfg["mode"])
        self.mode = cfg["mode"]
        self._refresh_mode_ui()

        self._append_log(f"✅ Modo alterado para {cfg['mode'].upper()} em tempo real.")
        QMessageBox.information(
            self,
            "Modo alterado",
            f"✅ Configuração salva e aplicada.\n\nMode agora: {cfg['mode'].upper()}\n"
            "Não precisa reiniciar o app.",
        )

    # ---------- Logs ----------
    def _open_logs(self) -> None:
        """Abre o arquivo de log no editor padrão do Windows."""
        try:
            from core.applog import log_path
            p = log_path()
            if not p.exists():
                p.write_text("", encoding="utf-8")
            os.startfile(str(p))  # type: ignore[attr-defined]
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Logs", f"Não consegui abrir o log:\n{e}")

    def _edit_config(self) -> None:
        """Abre o config.json (persistente, ao lado do .exe) no editor padrão."""
        try:
            from core.paths import config_path
            p = config_path()
            if not p.exists():
                QMessageBox.warning(self, "Config", f"config.json não encontrado em:\n{p}")
                return
            os.startfile(str(p))  # type: ignore[attr-defined]
            self._append_log(
                f"⚙️ Editando: {p}  — algumas mudanças aplicam em tempo real; outras podem exigir reinício."
            )
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Config", f"Não consegui abrir o config:\n{e}")

    def _apply_risk_profile(self, profile: str, force: bool = False) -> None:
        """Alterna entre perfis de risco (risco, bom, seguro) e recria LearningEngine."""
        try:
            cfg = self._load_cfg()
            current_profile = cfg.get("current_risk_profile", "bom")
            
            if profile == current_profile and not force:
                self._append_log(f"🔄 Perfil de risco já é {profile}.")
                return
            
            # Valida se o perfil existe
            profiles = cfg.get("risk_profiles", {})
            if profile not in profiles:
                QMessageBox.warning(self, "Perfil de Risco", f"Perfil '{profile}' não encontrado no config.")
                return
            
            # Atualiza config
            cfg["current_risk_profile"] = profile
            self._save_cfg(cfg)
            
            # Aplica os parâmetros do perfil ao agent da engine
            profile_cfg = profiles[profile]
            if self.worker and self.worker.agent:
                self.worker.agent.cfg.min_setup_quality = int(profile_cfg.get("min_setup_quality", 60))
                self.worker.agent.cfg.stop_loss_pct = float(profile_cfg.get("stop_loss_pct", 1.5))
                self.worker.agent.cfg.take_profit_pct = float(profile_cfg.get("take_profit_pct", 3.0))
                self.worker.agent.cfg.adx_min = float(profile_cfg.get("adx_min", 18.0))
                self.worker.agent.cfg.volume_min_ratio = float(profile_cfg.get("volume_min_ratio", 0.8))
                self.worker.agent.cfg.trailing_enabled = bool(profile_cfg.get("trailing_enabled", True))
                self.worker.agent.cfg.countertrend_reversal_enabled = bool(profile_cfg.get("countertrend_reversal_enabled", False))
                self.worker.agent.cfg.countertrend_rsi_max = float(profile_cfg.get("countertrend_rsi_max", 35.0))
                self.worker.agent.cfg.countertrend_size_factor = float(profile_cfg.get("countertrend_size_factor", 0.35))
            if self.worker and self.worker.risk:
                self.worker.risk.cfg.trade_size_pct = float(profile_cfg.get("trade_size_pct", 2.0))
            if self.worker and self.worker.cfg:
                self.worker.cfg.decisive_mode = bool(profile_cfg.get("decisive_mode", False))
                self.worker.cfg.decisive_min_entry_pct = int(profile_cfg.get("decisive_min_entry_pct", 35))
            if self.worker and self.worker.strategy:
                self.worker.strategy.cfg.score_buy_threshold = int(profile_cfg.get("score_buy_threshold", cfg.get("score_buy_threshold", 2)))
                self.worker.strategy.cfg.score_sell_threshold = int(profile_cfg.get("score_sell_threshold", cfg.get("score_sell_threshold", -2)))
                self.worker.strategy.cfg.rsi_buy = float(profile_cfg.get("rsi_buy", cfg.get("rsi_buy", 30)))
                self.worker.strategy.cfg.rsi_sell = float(profile_cfg.get("rsi_sell", cfg.get("rsi_sell", 70)))
            
            # Recarrega Learning Engine para o novo perfil
            if self.worker and self.worker.learning:
                from core.learning import LearningConfig, LearningEngine
                from core.paths import data_dir
                l_cfg = LearningConfig(
                    persist_path=data_dir() / f"learning_{profile}.json",
                    risk_profile=profile,
                    min_trades_to_adjust=int(cfg.get("learning", {}).get("min_trades_to_adjust", 3)),
                    review_every_n_trades=int(cfg.get("learning", {}).get("review_every_n_trades", 1)),
                    max_quality_mult=float(cfg.get("learning", {}).get("max_quality_mult", 1.30)),
                    min_quality_mult=float(cfg.get("learning", {}).get("min_quality_mult", 0.50)),
                )
                self.worker.learning = LearningEngine(l_cfg)
                if self.worker.agent_baseline:
                    self.worker.learning.apply_to_agent(self.worker.agent, self.worker.agent_baseline)
            
            profile_label = profiles[profile].get("label", profile)
            if force:
                self._append_log(f"✅ Perfil de risco {profile_label} aplicado no startup.")
            else:
                self._append_log(f"✅ Perfil de risco alterado para {profile_label}. Learning reiniciado.")
            
        except Exception as e:  # noqa: BLE001
            self._append_log(f"⚠️ Erro ao aplicar perfil de risco: {e}")
            QMessageBox.warning(self, "Perfil de Risco", f"Erro: {e}")

    def _open_scalping_dialog(self) -> None:
        """Dialog para configurar (ou desligar) o modo scalping por tempo."""
        from PyQt6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSpinBox,
            QPushButton, QCheckBox,
        )
        current = 0
        if self.worker and self.worker.agent:
            current = int(self.worker.agent.cfg.max_trade_duration_seconds)

        dlg = QDialog(self)
        dlg.setWindowTitle("⏱ Scalping por Tempo")
        dlg.setFixedWidth(360)
        v = QVBoxLayout(dlg)

        desc = QLabel(
            "<b>Duração máxima de uma posição aberta.</b><br>"
            "Quando atingido, a IA fecha o trade independente de SL/TP.<br>"
            "<small>0 segundos = desligado (comportamento padrão).</small>"
        )
        desc.setWordWrap(True)
        v.addWidget(desc)

        chk = QCheckBox("Ativar scalping por tempo")
        chk.setChecked(current > 0)
        v.addWidget(chk)

        row = QHBoxLayout()
        row.addWidget(QLabel("Duração (segundos):"))
        spin = QSpinBox()
        spin.setRange(30, 86400)
        spin.setSingleStep(30)
        spin.setValue(current if current > 0 else 300)
        spin.setEnabled(current > 0)
        row.addWidget(spin)
        v.addLayout(row)

        presets_row = QHBoxLayout()
        for label, secs in [("1 min", 60), ("2 min", 120), ("5 min", 300), ("10 min", 600), ("30 min", 1800)]:
            pb = QPushButton(label)
            pb.setStyleSheet("padding:4px;font-size:10px;")
            pb.clicked.connect(lambda _, s=secs: (spin.setValue(s), chk.setChecked(True)))
            presets_row.addWidget(pb)
        v.addLayout(presets_row)

        chk.toggled.connect(spin.setEnabled)

        btns = QHBoxLayout()
        btn_ok = QPushButton("✅ Aplicar")
        btn_cancel = QPushButton("Cancelar")
        btns.addWidget(btn_ok)
        btns.addWidget(btn_cancel)
        v.addLayout(btns)
        btn_cancel.clicked.connect(dlg.reject)

        def _apply() -> None:
            secs = spin.value() if chk.isChecked() else 0
            # Aplica em runtime
            if self.worker and self.worker.agent:
                self.worker.agent.cfg.max_trade_duration_seconds = secs
            # Persiste no config.json
            try:
                from core.paths import config_path
                import json
                cp = config_path()
                data = json.loads(cp.read_text(encoding="utf-8"))
                data.setdefault("agent", {})["max_trade_duration_seconds"] = secs
                cp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception as e:  # noqa: BLE001
                self._append_log(f"⚠ Não foi possível salvar scalping no config: {e}")
            label = f"{secs}s" if secs > 0 else "OFF"
            self._append_log(f"⏱ Scalping por tempo: {label}")
            self.btn_scalping.setChecked(secs > 0)
            self.btn_scalping.setText(f"⏱ Scalping {label}" if secs > 0 else "⏱ Scalping")
            dlg.accept()

        btn_ok.clicked.connect(_apply)
        dlg.exec()

    def _open_senior_preset_dialog(self) -> None:
        """Abre o dialog para ativar Senior Trader setup."""
        try:
            from ui.wizard_dialog import SeniorTraderPresetDialog
            from core.paths import config_path
            dlg = SeniorTraderPresetDialog(config_path(), self.mode, parent=self)
            dlg.exec()
            self._append_log("✅ Senior Trader Setup aplicado. Reinicie o app para ativar todas as mudanças.")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Senior Setup", f"Erro ao abrir dialog: {e}")

    def _restart_app(self) -> None:
        """Fecha o app e relança o mesmo executável/script.

        Em .exe (PyInstaller) usa um helper PowerShell que:
          1. Aguarda o processo pai encerrar (Wait-Process — confiável).
          2. Espera 6s para o Windows liberar handles do _MEI antigo
             (evita 'Failed to load Python DLL' no bootloader do novo .exe).
          3. Loga cada etapa em %TEMP%\\aitc_restart.log.
          4. Cria um lock em %TEMP%\\aitc_restart.lock para impedir helpers
             duplicados (caso usuário clique 'Reiniciar' duas vezes).
          5. Relança o .exe.
        """
        import tempfile

        self._append_log("🔄 Reiniciando o app…")
        try:
            self.controller.stop()
        except Exception:  # noqa: BLE001
            pass

        # Constantes Win32 (CreateProcess flags)
        CREATE_NO_WINDOW = 0x08000000
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        DETACHED_PROCESS = 0x00000008

        tmp = Path(tempfile.gettempdir())
        log_path = tmp / "aitc_restart.log"
        lock_path = tmp / "aitc_restart.lock"

        # Anti-duplicado: se já existe um helper rodando, não cria outro.
        if lock_path.exists():
            try:
                age = (datetime.now().timestamp() - lock_path.stat().st_mtime)
                # Lock antigo (>2 min) é considerado órfão — sobrescreve.
                if age < 120:
                    self._append_log("   helper de restart já está em execução — ignorando segundo clique.")
                    self._exiting = True
                    from PyQt6.QtWidgets import QApplication
                    QApplication.instance().quit()
                    return
            except Exception:  # noqa: BLE001
                pass

        try:
            if getattr(sys, "frozen", False):
                exe = sys.executable
                exe_dir = Path(exe).resolve().parent
                my_pid = os.getpid()
                ps1 = tmp / "aitc_restart.ps1"
                # PowerShell: Wait-Process é a forma confiável de esperar o
                # processo encerrar (não precisa de polling com tasklist).
                # Tudo escrito em ASCII puro para evitar erro de encoding.
                ps1_text = (
                    "$ErrorActionPreference = 'Continue'\r\n"
                    f"$logPath = '{log_path}'\r\n"
                    f"$lockPath = '{lock_path}'\r\n"
                    f"$exe = '{exe}'\r\n"
                    f"$exeDir = '{exe_dir}'\r\n"
                    f"$pid_parent = {my_pid}\r\n"
                    "Set-Content -Path $lockPath -Value $PID -Encoding ASCII\r\n"
                    "function Log($m) { Add-Content -Path $logPath -Value (\"[{0}] {1}\" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'), $m) -Encoding UTF8 }\r\n"
                    "Set-Content -Path $logPath -Value '' -Encoding UTF8\r\n"
                    "Log \"helper iniciado pid_helper=$PID pid_pai=$pid_parent exe=$exe\"\r\n"
                    "try {\r\n"
                    "  $p = Get-Process -Id $pid_parent -ErrorAction SilentlyContinue\r\n"
                    "  if ($p) {\r\n"
                    "    Log 'aguardando processo pai encerrar (timeout 60s)'\r\n"
                    "    try { Wait-Process -Id $pid_parent -Timeout 60 -ErrorAction Stop } catch {\r\n"
                    "      Log 'timeout aguardando pai - forcando taskkill'\r\n"
                    "      Stop-Process -Id $pid_parent -Force -ErrorAction SilentlyContinue\r\n"
                    "      Start-Sleep -Seconds 2\r\n"
                    "    }\r\n"
                    "  } else { Log 'pai ja estava encerrado' }\r\n"
                    "  Log 'aguardando 2s para liberar handles do executavel'\r\n"
                    "  Start-Sleep -Seconds 2\r\n"
                    "  Log \"relancando $exe\"\r\n"
                    "  Start-Process -FilePath $exe -WorkingDirectory $exeDir\r\n"
                    "  Log 'Start-Process emitido com sucesso'\r\n"
                    "} catch {\r\n"
                    "  Log (\"ERRO: $_\")\r\n"
                    "} finally {\r\n"
                    "  Remove-Item -Path $lockPath -ErrorAction SilentlyContinue\r\n"
                    "}\r\n"
                )
                ps1.write_text(ps1_text, encoding="ascii")
                # Lança PowerShell sem janela visível, totalmente desacoplado.
                subprocess.Popen(
                    ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                     "-WindowStyle", "Hidden", "-File", str(ps1)],
                    close_fds=True,
                    creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
                    cwd=str(exe_dir),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._append_log(f"   helper de restart agendado (log: {log_path}).")
            else:
                # Modo dev (rodando do .py): precisa detachar o filho para que
                # ele sobreviva ao QApplication.quit() do pai.
                script = str(Path(__file__).resolve().parents[1] / "main.py")
                flags = 0
                if sys.platform == "win32":
                    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
                subprocess.Popen(
                    [sys.executable, script],
                    close_fds=True,
                    creationflags=flags,
                    cwd=str(Path(script).parent),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(
                self,
                "Reinício automático falhou",
                f"Não consegui reiniciar automaticamente:\n{e}\n\n"
                f"Log de diagnóstico: {log_path}\n\n"
                "Feche o app e abra novamente manualmente.",
            )
            return
        self._exiting = True
        from PyQt6.QtWidgets import QApplication
        QApplication.instance().quit()

    # ---------- Pré-flight LIVE ----------
    def _open_preflight_dialog(self) -> None:
        cfg = self._load_cfg()
        items = run_preflight(cfg)
        self._show_preflight_dialog(items, blocking=False)

    def _show_preflight_dialog(self, items, blocking: bool) -> bool:
        """Mostra o checklist. Se `blocking=True`, devolve True somente se o
        usuário clicar em 'Prosseguir' E não houver bloqueadores fatais."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Pré-flight checklist — segurança LIVE")
        dlg.setMinimumWidth(560)
        dlg.setStyleSheet("background:#0b0d10;color:#e5e7eb;")
        v = QVBoxLayout(dlg)

        header = QLabel(
            "<b>Verificação de segurança antes de operar com dinheiro real.</b><br>"
            "<small>Itens em <span style='color:#ef4444'>vermelho</span> bloqueiam, "
            "<span style='color:#f59e0b'>amarelos</span> são alertas, "
            "<span style='color:#10b981'>verdes</span> estão OK.</small>"
        )
        header.setWordWrap(True)
        v.addWidget(header)

        # Lista rolável
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner_v = QVBoxLayout(inner)
        for it in items:
            color = "#10b981" if it.ok else (
                "#ef4444" if it.level == "fatal"
                else "#f59e0b" if it.level == "warn" else "#94a3b8"
            )
            line = QLabel(
                f"<div style='margin:6px 0'>"
                f"<span style='font-size:16px'>{it.icon()}</span> "
                f"<b style='color:{color}'>{it.title}</b><br>"
                f"<span style='color:#cbd5e1;font-size:12px'>{it.msg}</span>"
                f"</div>"
            )
            line.setWordWrap(True)
            inner_v.addWidget(line)
        inner_v.addStretch()
        scroll.setWidget(inner)
        v.addWidget(scroll, stretch=1)

        # Resumo
        blockers = has_blockers(items)
        warns = has_warnings(items)
        if blockers:
            summary = ("<b style='color:#ef4444'>❌ Há bloqueadores fatais.</b> "
                       "Corrija antes de operar real.")
        elif warns:
            summary = ("<b style='color:#f59e0b'>⚠️ Há alertas.</b> "
                       "Você pode prosseguir, mas leia com atenção.")
        else:
            summary = "<b style='color:#10b981'>✅ Tudo certo.</b> Pronto para operar."
        sm = QLabel(summary)
        sm.setWordWrap(True)
        v.addWidget(sm)

        # Botões
        if blocking:
            box = QDialogButtonBox()
            ok_btn = box.addButton("Prosseguir", QDialogButtonBox.ButtonRole.AcceptRole)
            box.addButton("Cancelar", QDialogButtonBox.ButtonRole.RejectRole)
            ok_btn.setEnabled(not blockers)
            if blockers:
                ok_btn.setToolTip("Há itens fatais — corrija o config primeiro.")
            box.accepted.connect(dlg.accept)
            box.rejected.connect(dlg.reject)
            v.addWidget(box)
            return dlg.exec() == QDialog.DialogCode.Accepted
        else:
            box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            box.rejected.connect(dlg.reject)
            box.accepted.connect(dlg.accept)
            v.addWidget(box)
            dlg.exec()
            return False

    def _check_update(self) -> None:
        if not self.update_manifest_url:
            QMessageBox.information(
                self, "Atualização",
                "Nenhuma URL de manifest configurada em config.json (campo update_manifest_url).",
            )
            return
        info = updater.check_and_get_update(self.update_manifest_url)
        if not info:
            QMessageBox.information(
                self, "Atualização",
                f"Você já está na versão mais recente (v{__version__}).",
            )
            return
        notes = info.notes or "(sem notas)"
        res = QMessageBox.question(
            self, "Nova versão disponível",
            f"Versão {info.version} disponível (atual v{__version__}).\n\n{notes}\n\nBaixar e atualizar agora?",
        )
        if res != QMessageBox.StandardButton.Yes:
            return
        try:
            updater.apply_update(info)
        except RuntimeError as e:
            QMessageBox.warning(self, "Atualização", str(e))
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Atualização", f"Falha ao atualizar: {e}")

    # ---------- Slots ----------
    def _on_price(self, price: float, ts_ms: float) -> None:
        # Monta OHLC por minuto a partir dos ticks para renderizar candles.
        ts = int(ts_ms / 1000.0)
        bucket = ts - (ts % self._candle_seconds)

        if self._active_candle_start is None:
            self._active_candle_start = bucket
            self._active_ohlc = {"o": price, "h": price, "l": price, "c": price}
        elif bucket != self._active_candle_start:
            if self._active_ohlc is not None:
                self._candles.append(
                    (
                        self._active_candle_index,
                        self._active_ohlc["o"],
                        self._active_ohlc["c"],
                        self._active_ohlc["l"],
                        self._active_ohlc["h"],
                    )
                )
                self._active_candle_index += 1.0
            self._active_candle_start = bucket
            self._active_ohlc = {"o": price, "h": price, "l": price, "c": price}
        else:
            if self._active_ohlc is not None:
                self._active_ohlc["h"] = max(self._active_ohlc["h"], price)
                self._active_ohlc["l"] = min(self._active_ohlc["l"], price)
                self._active_ohlc["c"] = price

        candle_data = list(self._candles)
        if self._active_ohlc is not None:
            candle_data.append(
                (
                    self._active_candle_index,
                    self._active_ohlc["o"],
                    self._active_ohlc["c"],
                    self._active_ohlc["l"],
                    self._active_ohlc["h"],
                )
            )
        self.candle_item.setData(candle_data)
        if candle_data:
            lows = [d[3] for d in candle_data]
            highs = [d[4] for d in candle_data]
            y_min = min(lows)
            y_max = max(highs)
            pad = max((y_max - y_min) * 0.15, y_max * 0.001, 0.05)
            self.plot.setYRange(y_min - pad, y_max + pad, padding=0.0)

            x_max = candle_data[-1][0]
            x_min = max(0.0, x_max - 80.0)
            self.plot.setXRange(x_min, x_max + 1.0, padding=0.0)
            if self._chart_only_plot is not None and self._chart_only_item is not None:
                self._chart_only_item.setData(candle_data)
                self._chart_only_plot.setYRange(y_min - pad, y_max + pad, padding=0.0)
                self._chart_only_plot.setXRange(x_min, x_max + 1.0, padding=0.0)
        self.kpi_price.value_label.setText(f"{price:,.2f}")
        # ----- Atualiza ticker bar (estilo Binance) -----
        try:
            self.ticker_symbol.setText(self.worker.cfg.symbol)
        except Exception:  # noqa: BLE001
            pass
        # Cor do preço pelo movimento (verde sobe, vermelho desce)
        color = "#e5e7eb"
        if self._last_ticker_price > 0:
            if price > self._last_ticker_price:
                color = "#0ecb81"   # verde Binance
            elif price < self._last_ticker_price:
                color = "#f6465d"   # vermelho Binance
        self.ticker_price.setText(f"{price:,.2f}")
        self.ticker_price.setStyleSheet(
            f"color:{color};font-size:22px;font-weight:bold;"
        )
        self._last_ticker_price = price
        # Status IA mini
        if getattr(self, "_state", {}).get("ai_on"):
            self.ticker_status.setText("● IA ON")
            self.ticker_status.setStyleSheet(
                "color:#0ecb81;font-size:12px;font-weight:bold;"
            )
        else:
            self.ticker_status.setText("○ IA OFF")
            self.ticker_status.setStyleSheet(
                "color:#848e9c;font-size:12px;font-weight:bold;"
            )
        # Stats 24h: busca a cada 60s pra não sobrecarregar API
        import time as _t
        if _t.time() - self._last_24h_fetch > 60:
            self._last_24h_fetch = _t.time()
            self._refresh_ticker_24h()
        # Watchlist multi-moeda: a cada 60s
        if _t.time() - self._last_watch_fetch > 60:
            self._last_watch_fetch = _t.time()
            self._refresh_watchlist()
        # Cérebro IA: a cada 10s (barato — só lê estado in-memory)
        if _t.time() - self._last_brain_refresh > 10:
            self._last_brain_refresh = _t.time()
            self._refresh_brain()

    def _refresh_ticker_24h(self) -> None:
        """Busca high/low/change/volume 24h e atualiza ticker bar."""
        try:
            ccxt_client = getattr(self.worker.client, "client", None)
            if ccxt_client is None:
                return
            sym = self.worker.cfg.symbol
            t = ccxt_client.fetch_ticker(sym)
            chg = t.get("percentage")
            high = t.get("high")
            low = t.get("low")
            qvol = t.get("quoteVolume") or t.get("baseVolume")
            if chg is not None:
                col = "#0ecb81" if chg >= 0 else "#f6465d"
                arrow = "▲" if chg >= 0 else "▼"
                self.ticker_change.setText(f"{arrow} {chg:+.2f}%")
                self.ticker_change.setStyleSheet(
                    f"color:{col};font-size:12px;font-weight:bold;"
                )
            if high is not None:
                self.ticker_high.setText(f"{high:,.2f}")
            if low is not None:
                self.ticker_low.setText(f"{low:,.2f}")
            if qvol is not None:
                if qvol >= 1e9:
                    s = f"{qvol/1e9:.2f}B"
                elif qvol >= 1e6:
                    s = f"{qvol/1e6:.2f}M"
                elif qvol >= 1e3:
                    s = f"{qvol/1e3:.2f}K"
                else:
                    s = f"{qvol:.2f}"
                self.ticker_volume.setText(s)
            # Par alternativo: se principal é XXX/BRL, mostra XXX/USDT (e vice-versa)
            try:
                if "/" in sym:
                    base, quote = sym.split("/")
                    alt_quote = "USDT" if quote != "USDT" else "BRL"
                    alt_sym = f"{base}/{alt_quote}"
                    t2 = ccxt_client.fetch_ticker(alt_sym)
                    alt_price = t2.get("last")
                    alt_chg = t2.get("percentage")
                    if alt_price is not None:
                        prefix = "$" if alt_quote == "USDT" else "R$"
                        chg_str = ""
                        if alt_chg is not None:
                            arr = "▲" if alt_chg >= 0 else "▼"
                            chg_str = f"  {arr}{alt_chg:+.2f}%"
                        col2 = "#848e9c"
                        if alt_chg is not None:
                            col2 = "#0ecb81" if alt_chg >= 0 else "#f6465d"
                        self.ticker_price_alt.setText(
                            f"≈ {prefix}{alt_price:,.2f}{chg_str}"
                        )
                        self.ticker_price_alt.setStyleSheet(
                            f"color:{col2};font-size:14px;font-weight:bold;"
                        )
            except Exception:  # noqa: BLE001
                # Par alternativo pode não existir (ex: par exótico)
                self.ticker_price_alt.setText("")
        except Exception:  # noqa: BLE001
            pass

    def _refresh_watchlist(self) -> None:
        """Busca preço BRL+USDT pras coins da watchlist em thread separada."""
        from threading import Thread
        ccxt_client = getattr(self.worker.client, "client", None)
        if ccxt_client is None:
            return
        coins = list(self._watch_coins)

        def _worker() -> None:
            results: dict[str, str] = {}
            for coin in coins:
                brl_str = "—"
                usd_str = "—"
                brl_chg = None
                try:
                    t = ccxt_client.fetch_ticker(f"{coin}/BRL")
                    p = t.get("last")
                    brl_chg = t.get("percentage")
                    if p is not None:
                        brl_str = f"R$ {p:,.2f}" if p < 1000 else f"R$ {p:,.0f}"
                except Exception:  # noqa: BLE001
                    pass
                try:
                    t = ccxt_client.fetch_ticker(f"{coin}/USDT")
                    p = t.get("last")
                    if p is not None:
                        usd_str = f"${p:,.2f}" if p < 1000 else f"${p:,.0f}"
                except Exception:  # noqa: BLE001
                    pass
                arrow = ""
                col = "#e5e7eb"
                if brl_chg is not None:
                    arrow = " ▲" if brl_chg >= 0 else " ▼"
                    col = "#0ecb81" if brl_chg >= 0 else "#f6465d"
                    arrow += f"{brl_chg:+.1f}%"
                results[coin] = (f"{brl_str}  {usd_str}{arrow}", col)

            # Atualiza UI no thread principal
            from PyQt6.QtCore import QTimer
            def _apply() -> None:
                for coin, (text, color) in results.items():
                    if coin in self._watch_labels:
                        self._watch_labels[coin].setText(text)
                        self._watch_labels[coin].setStyleSheet(
                            f"color:{color};font-size:11px;"
                        )
            QTimer.singleShot(0, _apply)

        Thread(target=_worker, daemon=True).start()

    def _refresh_brain(self) -> None:
        """Atualiza painel 🧠 CÉREBRO DA IA com estado de aprendizado ao vivo."""
        try:
            learning = getattr(self.worker, "learning", None)
            if learning is None:
                return
            s = learning.stats()
            trades = s.get("trades", 0)
            wins = s.get("wins", 0)
            wr = s.get("win_rate_pct", 0.0)
            pnl = s.get("total_pnl_pct", 0.0)
            qm = s.get("quality_mult", 1.0)
            slm = s.get("sl_mult", 1.0)
            tpm = s.get("tp_mult", 1.0)
            best = s.get("best_context") or "—"
            worst = s.get("worst_context") or "—"
            adjustments = s.get("recent_adjustments") or []

            # Status shadow (aberto/fechado)
            shadow_pos = getattr(self.worker, "_shadow_pos", None)
            if shadow_pos:
                self.brain_shadow.setText("Shadow: 👁 ATIVO (aberto)")
                self.brain_shadow.setStyleSheet("color:#f0b90b;font-size:11px;font-weight:bold;")
            else:
                self.brain_shadow.setText("Shadow: 👁 observando")
                self.brain_shadow.setStyleSheet("color:#94a3b8;font-size:11px;")

            self.brain_trades.setText(f"Trades aprendidos: {trades} ({wins} wins)")
            if trades >= 1:
                col = "#0ecb81" if wr >= 50 else "#f6465d"
                self.brain_winrate.setText(f"Win-rate: {wr:.0f}%")
                self.brain_winrate.setStyleSheet(f"color:{col};font-size:11px;font-weight:bold;")
                col2 = "#0ecb81" if pnl >= 0 else "#f6465d"
                self.brain_pnl.setText(f"PnL acumulado: {pnl:+.2f}%")
                self.brain_pnl.setStyleSheet(f"color:{col2};font-size:11px;font-weight:bold;")
            else:
                self.brain_winrate.setText("Win-rate: -- (precisa 1+ trade)")
                self.brain_pnl.setText("PnL: --")

            # Multiplicadores: amarelo se desviou do baseline 1.0
            def _fmt(name: str, val: float, label) -> None:
                label.setText(f"{name}: {val:.2f}x")
                if abs(val - 1.0) > 0.01:
                    label.setStyleSheet("color:#f0b90b;font-size:11px;font-weight:bold;")
                else:
                    label.setStyleSheet("color:#94a3b8;font-size:11px;")
            _fmt("Qualidade", qm, self.brain_mult_q)
            _fmt("SL", slm, self.brain_mult_sl)
            _fmt("TP", tpm, self.brain_mult_tp)
            sm = float(getattr(learning.state, "size_multiplier", 1.0))
            _fmt("Tamanho", sm, self.brain_mult_size)

            self.brain_best.setText(f"✓ Melhor contexto: {best}")
            self.brain_worst.setText(f"✗ Pior contexto: {worst}")

            if adjustments:
                last = adjustments[-1]
                self.brain_adjust.setText(f"Último ajuste auto: {last}")
            else:
                if trades < 5:
                    self.brain_adjust.setText(
                        f"Último ajuste: nenhum (precisa ≥5 trades fechados — atual: {trades})"
                    )
        except Exception:  # noqa: BLE001
            pass

    def _on_decision(self, dec: Decision) -> None:
        snap = dec.snapshot
        self.kpi_rsi.value_label.setText(f"{snap.rsi:.1f}")
        self.kpi_macd.value_label.setText(f"{snap.macd_hist:+.4f}")
        self.kpi_vol.value_label.setText(f"{snap.volatility_pct:.2f}%")
        self.kpi_score.value_label.setText(str(dec.score))
        color = {"BUY": "#10b981", "SELL": "#ef4444", "HOLD": "#e5e7eb"}[dec.signal]
        self.kpi_signal.value_label.setText(dec.signal)
        self.kpi_signal.value_label.setStyleSheet(
            f"font-size:16px;font-weight:bold;color:{color};"
        )
        self.reasons.clear()
        for r in dec.reasons:
            self.reasons.addItem(r)
        self._state["signal"] = dec.signal
        self._state["reasons"] = list(dec.reasons)
        self._refresh_story()

    def _on_agent(self, dec: AgentDecision) -> None:
        regime_color = {
            "TRENDING_UP": "#10b981",
            "TRENDING_DOWN": "#ef4444",
            "RANGING": "#94a3b8",
            "CHOPPY": "#f59e0b",
            "EXTREME_VOL": "#b00020",
        }.get(dec.regime, "#e5e7eb")
        self.kpi_regime.value_label.setText(dec.regime)
        self.kpi_regime.value_label.setStyleSheet(
            f"font-size:16px;font-weight:bold;color:{regime_color};"
        )
        q_color = "#10b981" if dec.quality >= 60 else ("#f59e0b" if dec.quality >= 30 else "#ef4444")
        self.kpi_quality.value_label.setText(f"{dec.quality}%")
        self.kpi_quality.value_label.setStyleSheet(
            f"font-size:16px;font-weight:bold;color:{q_color};"
        )
        s_color = "#10b981" if dec.sentiment > 0 else ("#ef4444" if dec.sentiment < 0 else "#94a3b8")
        self.kpi_sentiment.value_label.setText(f"{dec.sentiment:+d}")
        self.kpi_sentiment.value_label.setStyleSheet(
            f"font-size:16px;font-weight:bold;color:{s_color};"
        )
        # sobrescreve o sinal mostrado pela strategy com o sinal final do agente
        sig_color = {"BUY": "#10b981", "SELL": "#ef4444", "HOLD": "#e5e7eb"}[dec.signal]
        self.kpi_signal.value_label.setText(dec.signal)
        self.kpi_signal.value_label.setStyleSheet(
            f"font-size:16px;font-weight:bold;color:{sig_color};"
        )
        self.reasons.addItem("— trader sênior —")
        for r in dec.reasons:
            self.reasons.addItem(r)
        # Estado para a narrativa
        self._state["regime"] = dec.regime
        self._state["signal"] = dec.signal
        self._state["quality"] = int(dec.quality)
        self._state["sentiment"] = int(dec.sentiment)
        self._state["reasons"] = list(dec.reasons)
        self._refresh_story()

    def _on_portfolio(self, equity: float, cash: float, base: float) -> None:
        ccy = getattr(self, "_quote_ccy", "USDT")
        self.kpi_equity.value_label.setText(f"{equity:,.2f} {ccy}")
        self.kpi_cash.value_label.setText(f"{cash:,.2f}")
        self.kpi_position.value_label.setText(f"{base:.6f}")
        self._state["has_position"] = base > 1e-9
        self._refresh_story()

    def _on_trade(self, trade: Trade) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        items = [
            trade.timestamp.strftime("%H:%M:%S"),
            trade.side,
            f"{trade.price:.2f}",
            f"{trade.amount:.6f}",
            f"{trade.pnl:+.2f}",
        ]
        for col, text in enumerate(items):
            it = QTableWidgetItem(text)
            if col == 1:
                it.setForeground(QColor("#10b981" if trade.side == "BUY" else "#ef4444"))
            self.table.setItem(row, col, it)
        self.table.scrollToBottom()
        self._state["last_trade"] = (
            trade.side, trade.price, trade.pnl,
            trade.timestamp.strftime("%H:%M:%S"),
        )
        # Banner de resultado visível (só em fechamentos com PnL)
        if trade.side == "SELL" and trade.pnl != 0.0:
            if trade.pnl > 0:
                txt = f"✅  LUCRO  +{trade.pnl:.4f} USDT  ({trade.timestamp.strftime('%H:%M:%S')})"
                css = ("font-size:14px;font-weight:bold;padding:6px 14px;"
                       "border-radius:6px;background:#0a2e1a;color:#0ecb81;"
                       "border:1px solid #0ecb81;")
            else:
                txt = f"❌  PREJUÍZO  {trade.pnl:+.4f} USDT  ({trade.timestamp.strftime('%H:%M:%S')})"
                css = ("font-size:14px;font-weight:bold;padding:6px 14px;"
                       "border-radius:6px;background:#2e0a0a;color:#f6465d;"
                       "border:1px solid #f6465d;")
            self._trade_result_banner.setText(txt)
            self._trade_result_banner.setStyleSheet(css)
            self._trade_result_banner.setVisible(True)
            self._trade_banner_timer.start(8000)  # some após 8s
        self._refresh_story()

    def _on_risk(self, status: dict) -> None:
        pnl = status.get("daily_pnl_pct", 0.0)
        self.kpi_pnl.value_label.setText(f"{pnl:+.2f}%")
        color = "#10b981" if pnl >= 0 else "#ef4444"
        self.kpi_pnl.value_label.setStyleSheet(
            f"font-size:16px;font-weight:bold;color:{color};"
        )
        if status.get("kill_switch"):
            self.btn_kill.setText("⛔ TRAVADO")
        self._state["pnl_day_pct"] = pnl
        self._refresh_story()

    def _on_safety(self, st: dict) -> None:
        """Atualiza linha 🛡 Proteções no painel Cérebro."""
        try:
            weekly_locked = st.get("weekly_locked", False)
            weekly_reason = st.get("weekly_reason", "")
            t_h = st.get("trades_last_hour", 0)
            t_d = st.get("trades_last_day", 0)
            hb_age = st.get("heartbeat_age_sec", 0.0)
            hb_stale = st.get("heartbeat_stale", False)
            rate_until = st.get("rate_locked_until", "")

            parts = []
            color = "#0ecb81"
            if weekly_locked:
                parts.append(f"🔒 SEMANAL TRAVADO: {weekly_reason}")
                color = "#f6465d"
            elif rate_until:
                parts.append(f"⛔ RATE LIMIT ATIVO até {rate_until[:16]}")
                color = "#f6465d"
            else:
                parts.append("✅ OK")
            parts.append(f"Trades: {t_h}/h, {t_d}/24h")
            hb_icon = "💔" if hb_stale else "💓"
            parts.append(f"{hb_icon} heartbeat {hb_age:.0f}s")
            if hb_stale:
                color = "#f0b90b"
            self.safety_label.setText("🛡 Proteções: " + " | ".join(parts))
            self.safety_label.setStyleSheet(
                f"color:{color};font-size:11px;font-weight:bold;"
            )
        except Exception:  # noqa: BLE001
            pass

    def _reset_safety(self) -> None:
        """Destrava manualmente circuit breaker semanal/rate limit."""
        try:
            ret = QMessageBox.question(
                self, "Reset proteção",
                "Destravar proteções (circuit breaker semanal + rate limit)?\n\n"
                "Use só se souber o que está fazendo.\n"
                "As proteções existem pra te proteger de loops de bug e perdas grandes.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return
            self.worker.safety.reset_weekly_lock()
            self.worker.safety.state.rate_locked_until_iso = ""
            self.worker.safety.state.trades_log = []
            self.worker.safety._save()
            self._append_log("🛡 Proteções resetadas manualmente.")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Reset", f"Falhou: {e}")

    def _on_senior(self, adv: dict) -> None:
        """Mostra opinião do conselheiro LLM no painel Cérebro."""
        try:
            err = adv.get("error", "")
            if err:
                self.senior_label.setText(f"🎓 Senior offline: {err[:80]}")
                self.senior_label.setStyleSheet("color:#f0b90b;font-size:11px;font-style:italic;")
                return
            comment = adv.get("comment", "—")
            conf = int(adv.get("confidence", 50))
            agree = bool(adv.get("agree", True))
            decision = adv.get("decision", "?")
            lat = float(adv.get("latency_sec", 0.0))
            icon = "✅" if agree else "⚠"
            color = "#0ecb81" if agree and conf >= 60 else ("#f6465d" if not agree else "#f0b90b")
            self.senior_label.setText(
                f"🎓 Senior {icon} ({conf}% sobre {decision}, {lat:.1f}s): {comment}"
            )
            self.senior_label.setStyleSheet(f"color:{color};font-size:11px;")
        except Exception:  # noqa: BLE001
            pass

    def _toggle_senior(self, on: bool) -> None:
        """Liga/desliga o conselheiro LLM em runtime."""
        try:
            if self.worker.senior is None:
                QMessageBox.warning(self, "Senior", "SeniorAdvisor não inicializou.")
                self.btn_senior_toggle.setChecked(False)
                return
            if on:
                ok, msg = self.worker.senior.health_check()
                if not ok:
                    QMessageBox.warning(
                        self, "Ollama indisponível",
                        f"Não consegui falar com o Ollama:\n\n{msg}\n\n"
                        f"Verifique se o serviço está rodando ('ollama serve') e "
                        f"que o modelo '{self.worker.senior.cfg.model}' está instalado "
                        f"('ollama pull llama3').",
                    )
                    self.btn_senior_toggle.setChecked(False)
                    return
                self.worker.senior.cfg.enabled = True
                self.btn_senior_toggle.setText("Desligar Senior")
                self.senior_label.setText("🎓 Senior: ligado, aguardando 1ª análise…")
                self._append_log("🎓 Conselheiro Senior LLM ativado.")
            else:
                self.worker.senior.cfg.enabled = False
                self.btn_senior_toggle.setText("Ligar Senior")
                self.senior_label.setText("🎓 Senior: desligado (Ollama opt-in)")
                self.senior_label.setStyleSheet("color:#9ca3af;font-size:11px;font-style:italic;")
                self._append_log("🎓 Conselheiro Senior LLM desativado.")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Senior", f"Erro: {e}")
            self.btn_senior_toggle.setChecked(False)

    def _on_news(self, score: int, headlines: list) -> None:
        self.news_list.clear()
        self.news_list.addItem(f"📊 Sentimento: {score:+d}")
        for h in headlines:
            self.news_list.addItem(f"• {h}")
        self._state["news_score"] = int(score)
        self._refresh_story()

    def _on_learning(self, stats: dict) -> None:
        """Recebe atualização do LearningEngine após cada trade fechado."""
        trades = stats.get("trades", 0)
        wr = stats.get("win_rate_pct", 0.0)
        qm = stats.get("quality_mult", 1.0)
        slm = stats.get("sl_mult", 1.0)
        tpm = stats.get("tp_mult", 1.0)
        self._append_log(
            f"🧠 Aprendizado: {trades} trades | winrate {wr:.0f}% | "
            f"q×{qm:.2f} sl×{slm:.2f} tp×{tpm:.2f}"
        )
        for adj in stats.get("recent_adjustments", [])[-1:]:
            self._append_log(f"   ↳ {adj}")
        # Atualiza painel visual imediatamente (não espera 10s)
        try:
            self._refresh_brain()
        except Exception:  # noqa: BLE001
            pass

    def _append_log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.log.append(f"[{ts}] {msg}")

    def _on_pause_toggled(self, paused: bool) -> None:
        self._state["paused"] = paused
        self._refresh_story()

    # ---------- Narrativa amigável ----------
    def _refresh_story(self) -> None:
        """Monta um texto em português simples explicando o momento atual.

        Pensado para usuários leigos: nada de jargão sem explicação.
        """
        st = self._state

        # 1) Status geral do robô
        if st["paused"]:
            head = "⏸️ <b>Robô pausado.</b> Nenhuma ordem será enviada agora."
        elif not st["ai_on"]:
            head = ("👀 <b>Modo observação.</b> A IA está apenas analisando o "
                    "mercado — nenhuma ordem será executada até você clicar em "
                    "<i>Ativar IA</i>.")
        else:
            head = "🤖 <b>IA ativa.</b> O robô pode comprar e vender automaticamente."

        # 2) Como está o mercado (regime + sentimento)
        regime_msg = {
            "TRENDING_UP": "📈 O mercado está em <b>tendência de alta</b> — preços subindo de forma consistente.",
            "TRENDING_DOWN": "📉 O mercado está em <b>tendência de queda</b> — preços caindo de forma consistente.",
            "RANGING": "↔️ O mercado está <b>de lado (lateral)</b> — sem direção clara, oscilando numa faixa.",
            "CHOPPY": "🌪️ O mercado está <b>nervoso e instável</b> — movimentos curtos para os dois lados.",
            "EXTREME_VOL": "⚠️ Volatilidade <b>EXTREMA</b> — risco muito alto, melhor esperar acalmar.",
        }.get(st["regime"], f"Regime: {st['regime']}.")

        sent = st["sentiment"]
        if sent >= 2:
            sent_msg = "📰 Notícias <b>muito positivas</b> nas últimas horas."
        elif sent == 1:
            sent_msg = "📰 Notícias <b>levemente positivas</b>."
        elif sent <= -2:
            sent_msg = "📰 Notícias <b>muito negativas</b> — atenção redobrada."
        elif sent == -1:
            sent_msg = "📰 Notícias <b>levemente negativas</b>."
        else:
            sent_msg = "📰 Notícias neutras / sem destaque."

        # 3) O que a IA está fazendo agora
        sig = st["signal"]
        q = st["quality"]
        if st["has_position"]:
            if sig == "SELL":
                action = ("💼 <b>Posição comprada</b> — a IA acabou de decidir <span style='color:#ef4444'><b>VENDER</b></span> "
                          "para realizar o trade.")
            else:
                action = ("💼 <b>Você está comprado</b> (posicionado no ativo) — a IA está "
                          "<b>segurando</b> e monitorando stop-loss / take-profit.")
        else:
            if sig == "BUY":
                if st["ai_on"]:
                    action = (f"🟢 A IA decidiu <b>COMPRAR agora</b> (confiança {q}%). "
                              f"Ordem será enviada neste tick.")
                else:
                    action = (f"🟢 A IA <b>compraria agora</b> (confiança {q}%) — mas a IA está "
                              "desligada, então nenhuma ordem foi enviada.")
            elif sig == "SELL":
                action = "🔴 Sinal de venda, mas você não tem posição aberta — nada a fazer."
            else:
                action = (f"⏳ <b>Aguardando oportunidade.</b> A IA está esperando um setup com "
                          f"qualidade suficiente (atual: {q}%, mínimo configurado: ~60%).")

        # 4) PnL diário em linguagem simples
        pnl = st["pnl_day_pct"]
        if pnl > 0.5:
            pnl_msg = f"💰 Hoje você está <b style='color:#10b981'>ganhando {pnl:+.2f}%</b>."
        elif pnl < -0.5:
            pnl_msg = f"📉 Hoje você está <b style='color:#ef4444'>perdendo {pnl:+.2f}%</b>."
        else:
            pnl_msg = f"⚖️ Resultado do dia praticamente <b>zero</b> ({pnl:+.2f}%)."

        # 5) Último trade (se houver)
        last_trade_msg = ""
        lt = st["last_trade"]
        if lt:
            side, price, pnl_t, hora = lt
            if side == "BUY":
                last_trade_msg = (f"<br>🛒 Última operação às <b>{hora}</b>: <b>COMPROU</b> a "
                                  f"{price:,.2f}.")
            else:
                color = "#10b981" if pnl_t >= 0 else "#ef4444"
                emoji = "🎯" if pnl_t >= 0 else "🛡️"
                last_trade_msg = (f"<br>{emoji} Última operação às <b>{hora}</b>: <b>VENDEU</b> a "
                                  f"{price:,.2f} → resultado <b style='color:{color}'>"
                                  f"{pnl_t:+.2f} USDT</b>.")

        text = (
            f"{head}<br>"
            f"{regime_msg} {sent_msg}<br>"
            f"{action}<br>"
            f"{pnl_msg}{last_trade_msg}"
        )
        self.story_label.setText(text)

    # ---------- Encerrar app ----------
    def _request_exit(self) -> None:
        """Pede confirmação e encerra o app inteiro (não só a janela)."""
        # Aviso reforçado se a IA está ativa em modo live com posição aberta
        warn_extra = ""
        if self.mode == "live" and self._state.get("ai_on"):
            warn_extra = (
                "\n\n⚠ A IA está ATIVA em modo LIVE. "
                "Sair agora interrompe o monitoramento de stop-loss / take-profit."
            )
        if self._state.get("has_position"):
            warn_extra += (
                "\n\n⚠ Você tem uma POSIÇÃO ABERTA. "
                "O robô não vai mais cuidar dela depois que sair."
            )

        res = QMessageBox.question(
            self,
            "Sair do AI Trader Copilot",
            f"Deseja realmente encerrar o aplicativo?{warn_extra}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if res != QMessageBox.StandardButton.Yes:
            return
        self._exiting = True
        self.close()  # dispara closeEvent → para o controller → app.exec sai

    def closeEvent(self, event) -> None:  # noqa: N802
        # Se o usuário fechou pelo X (e não pelo botão Sair), pede confirmação.
        if not getattr(self, "_exiting", False):
            res = QMessageBox.question(
                self,
                "Sair do AI Trader Copilot",
                "Deseja realmente fechar o aplicativo?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if res != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        try:
            self.controller.stop()
        except Exception:  # noqa: BLE001
            pass
        from PyQt6.QtWidgets import QApplication
        super().closeEvent(event)
        QApplication.instance().quit()
