"""Telas de boas-vindas do AI Trader Copilot.

- `WelcomeSplash`: splash clássico (auto-some). Mantido por compatibilidade.
- `WelcomeDialog`: tela modal com logo + botão "Iniciar Trade" — usuário
  controla quando o app principal abre.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplashScreen,
    QVBoxLayout,
)

from version import APP_NAME, __version__


def _resource_path(rel: str) -> Path:
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


def _build_pixmap(width: int = 560, height: int = 340) -> QPixmap:
    """Monta o pixmap do splash (fundo escuro + logo + textos)."""
    pix = QPixmap(width, height)
    pix.fill(QColor("#0b0d10"))

    painter = QPainter(pix)
    try:
        # Borda fina ciano
        painter.setPen(QColor("#22d3ee"))
        painter.drawRect(0, 0, width - 1, height - 1)

        # Logo (se existir)
        logo_path = _resource_path("assets/app.png")
        if logo_path.exists():
            logo = QPixmap(str(logo_path)).scaled(
                128, 128,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            logo_x = (width - logo.width()) // 2
            painter.drawPixmap(logo_x, 30, logo)

        # Nome do app
        painter.setPen(QColor("#e5e7eb"))
        title_font = QFont("Segoe UI", 22, QFont.Weight.Bold)
        painter.setFont(title_font)
        painter.drawText(
            0, 175, width, 36,
            Qt.AlignmentFlag.AlignHCenter,
            APP_NAME,
        )

        # Versão
        painter.setPen(QColor("#94a3b8"))
        ver_font = QFont("Segoe UI", 10)
        painter.setFont(ver_font)
        painter.drawText(
            0, 210, width, 20,
            Qt.AlignmentFlag.AlignHCenter,
            f"versão {__version__}",
        )

        # Slogan
        painter.setPen(QColor("#22d3ee"))
        slogan_font = QFont("Segoe UI", 12, QFont.Weight.Bold)
        painter.setFont(slogan_font)
        painter.drawText(
            0, 245, width, 24,
            Qt.AlignmentFlag.AlignHCenter,
            "Seu copiloto inteligente de trading",
        )

        # Mensagem de boas-vindas
        painter.setPen(QColor("#cbd5e1"))
        msg_font = QFont("Segoe UI", 10)
        painter.setFont(msg_font)
        painter.drawText(
            0, 275, width, 20,
            Qt.AlignmentFlag.AlignHCenter,
            "Bem-vindo! Carregando dados do mercado…",
        )

        # Aviso pequeno
        painter.setPen(QColor("#64748b"))
        warn_font = QFont("Segoe UI", 8)
        painter.setFont(warn_font)
        painter.drawText(
            0, 305, width, 16,
            Qt.AlignmentFlag.AlignHCenter,
            "⚠ Trading envolve risco. Use sempre tamanhos pequenos no início.",
        )
    finally:
        painter.end()

    return pix


class WelcomeSplash(QSplashScreen):
    """Splash screen com auto-hide após `duration_ms` se ninguém fechar."""

    def __init__(self, duration_ms: int = 2200) -> None:
        pixmap = _build_pixmap()
        super().__init__(
            pixmap,
            Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.SplashScreen,
        )
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.close)
        self._timer.start(duration_ms)



class WelcomeDialog(QDialog):
    """Tela de boas-vindas modal com botão 'Iniciar Trade'.

    O dialog NÃO fecha sozinho — o usuário precisa clicar em
    'Iniciar Trade' (ou 'Sair'). Isso dá tempo de ver a logo,
    a versão e o aviso de risco com calma.
    """

    def __init__(self, mode: str = "simulation", use_testnet: bool = False) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} — Bem-vindo")
        self.setModal(True)
        self.setFixedSize(560, 480)
        self.setStyleSheet("background:#0b0d10;color:#e5e7eb;")

        icon_path = _resource_path("assets/app.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        v = QVBoxLayout(self)
        v.setContentsMargins(30, 24, 30, 24)
        v.setSpacing(14)

        # Logo
        logo_path = _resource_path("assets/app.png")
        if logo_path.exists():
            logo_label = QLabel()
            pix = QPixmap(str(logo_path)).scaled(
                128, 128,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            logo_label.setPixmap(pix)
            logo_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            v.addWidget(logo_label)

        # Título
        title = QLabel(APP_NAME)
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        title.setStyleSheet("color:#e5e7eb;font-size:24px;font-weight:bold;")
        v.addWidget(title)

        # Versão
        ver = QLabel(f"versão {__version__}")
        ver.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        ver.setStyleSheet("color:#94a3b8;font-size:11px;")
        v.addWidget(ver)

        # Slogan
        slogan = QLabel("Seu copiloto inteligente de trading")
        slogan.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        slogan.setStyleSheet("color:#22d3ee;font-size:14px;font-weight:bold;")
        v.addWidget(slogan)

        # Modo atual (simulação x testnet x real)
        if mode == "live" and use_testnet:
            mode_text = "🟡 <b>Modo TESTNET</b> — ordens com saldo fake (USDT)"
            mode_color = "#f59e0b"
        elif mode == "live":
            mode_text = "🔴 <b>Modo REAL</b> — ordens reais com seu dinheiro"
            mode_color = "#b00020"
        else:
            mode_text = "🟢 <b>Modo Simulação</b> — sem risco, dinheiro fake"
            mode_color = "#0ea5e9"
        mode_lbl = QLabel(mode_text)
        mode_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        mode_lbl.setStyleSheet(
            f"background:#111827;color:{mode_color};border:1px solid {mode_color};"
            "border-radius:6px;padding:8px;font-size:13px;"
        )
        v.addWidget(mode_lbl)

        # Aviso
        warn = QLabel(
            "⚠ <b>Aviso:</b> Trading envolve risco real de perda. "
            "Comece com valores pequenos e em testnet. "
            "O robô não garante lucro."
        )
        warn.setWordWrap(True)
        warn.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        warn.setStyleSheet("color:#fbbf24;font-size:11px;padding:4px;")
        v.addWidget(warn)

        v.addStretch()

        # Botões
        btns = QHBoxLayout()
        self.btn_start = QPushButton("▶  Iniciar Trade")
        self.btn_start.setStyleSheet(
            "background:#10b981;color:white;font-size:15px;font-weight:bold;"
            "padding:12px 24px;border-radius:6px;border:none;"
        )
        self.btn_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_start.setDefault(True)
        self.btn_start.clicked.connect(self.accept)

        self.btn_exit = QPushButton("Sair")
        self.btn_exit.setStyleSheet(
            "background:#374151;color:#e5e7eb;font-size:13px;"
            "padding:12px 18px;border-radius:6px;border:1px solid #4b5563;"
        )
        self.btn_exit.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_exit.clicked.connect(self.reject)

        btns.addWidget(self.btn_exit)
        btns.addStretch()
        btns.addWidget(self.btn_start)
        v.addLayout(btns)
    def show_status(self, msg: str) -> None:
        """Atualiza a linha de status no rodapé do splash."""
        self.showMessage(
            msg,
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
            QColor("#22d3ee"),
        )
        QApplication.processEvents()
