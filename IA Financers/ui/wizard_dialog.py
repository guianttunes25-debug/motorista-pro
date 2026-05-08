"""Startup Wizard Dialog — Bem-vindo, modo de operação, auto-config."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QMessageBox,
    QWidget,
)


class StartupWizardDialog(QDialog):
    """Wizard que aparece na primeira execução para:
    1. Escolher TESTNET ou REAL
    2. Auto-configurar agent params (filtros, learning, sanity-mode)
    3. Ativar profile correto
    4. Marcar wizard como completo
    """

    wizard_complete = pyqtSignal(str)  # Emite "live" ou "simulation"

    def __init__(self, config_path: Path, parent=None):
        super().__init__(parent)
        self.config_path = config_path
        self.mode: str = ""
        self._init_ui()

    def _init_ui(self) -> None:
        self.setWindowTitle("🎓 Bem-vindo ao AI Trader Copilot")
        self.setStyleSheet(
            "QDialog { background-color: #1a202c; } "
            "QLabel { color: #e5e7eb; } "
            "QPushButton { background-color: #3b82f6; color: white; border: none; "
            "border-radius: 6px; padding: 12px 24px; font-weight: bold; font-size: 14px; }"
            "QPushButton:hover { background-color: #2563eb; }"
        )
        self.setModal(True)
        self.setFixedSize(600, 400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(20)

        # Título
        title = QLabel("🎓 Configuração Inicial")
        title_font = QFont()
        title_font.setPointSize(20)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # Subtítulo
        subtitle = QLabel(
            "Você quer operar em TESTNET (USDT fake) ou REAL (USDT real)?"
        )
        subtitle_font = QFont()
        subtitle_font.setPointSize(12)
        subtitle.setFont(subtitle_font)
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("color: #94a3b8; line-height: 1.5;")
        layout.addWidget(subtitle)

        layout.addSpacing(20)

        # Dois botões lado a lado
        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(20)

        # Botão TESTNET
        btn_sim = QPushButton("🟡  TESTNET  (USDT fake)")
        btn_sim.setMinimumHeight(80)
        btn_sim.setFont(self._button_font())
        btn_sim.setStyleSheet(
            "QPushButton { background-color: #f59e0b; color: #111827; border: none; "
            "border-radius: 8px; padding: 20px; font-weight: bold; font-size: 13px; "
            "min-height: 80px; } "
            "QPushButton:hover { background-color: #d97706; }"
        )
        btn_sim.clicked.connect(lambda: self._select_mode("testnet"))
        buttons_layout.addWidget(btn_sim)

        # Botão REAL
        btn_real = QPushButton("🔴  REAL  (Seu dinheiro ⚠️)")
        btn_real.setMinimumHeight(80)
        btn_real.setFont(self._button_font())
        btn_real.setStyleSheet(
            "QPushButton { background-color: #ef4444; color: white; border: none; "
            "border-radius: 8px; padding: 20px; font-weight: bold; font-size: 13px; "
            "min-height: 80px; } "
            "QPushButton:hover { background-color: #dc2626; }"
        )
        btn_real.clicked.connect(lambda: self._select_mode("real"))
        buttons_layout.addWidget(btn_real)

        layout.addLayout(buttons_layout)

        # Info box
        info = QLabel(
            "💡 Em TESTNET, você testa com saldo fake em USDT.\n"
            "Em REAL, a IA opera com seu dinheiro verdadeiro em USDT.\n"
            "Você pode trocar de modo depois em ⚙️ Configurações."
        )
        info.setStyleSheet(
            "color: #cbd5e1; font-size: 11px; "
            "background-color: #334155; border-radius: 6px; padding: 12px; "
            "line-height: 1.6;"
        )
        info.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        layout.addWidget(info)

        layout.addStretch()

    def _button_font(self) -> QFont:
        font = QFont()
        font.setPointSize(11)
        font.setBold(True)
        return font

    def _select_mode(self, mode: str) -> None:
        """Salva modo, auto-configura params, ativa profile."""
        if mode in ("real", "testnet"):
            try:
                from core.secrets import get_secret
                key_user = "binance_api_key_real" if mode == "real" else "binance_api_key_sim"
                sec_user = "binance_api_secret_real" if mode == "real" else "binance_api_secret_sim"
                api_key = get_secret(key_user)
                api_secret = get_secret(sec_user)
                if not (api_key and api_secret):
                    QMessageBox.warning(
                        self,
                        "⚠️ Sem Chaves no Perfil",
                        "Você escolheu um modo com execução de ordens, mas não tem API keys salvas.\n\n"
                        "Use '🔑 Configurar API Key' depois para adicionar suas chaves.\n"
                        "Por enquanto, vou ativar SIMULAÇÃO (seguro).",
                    )
                    mode = "simulation"  # Fallback para simulation
            except Exception as e:
                print(f"⚠️ Erro ao verificar chaves: {e}")
                mode = "simulation"
        self.mode = mode
        is_testnet = mode == "testnet"
        is_simulation = mode == "simulation"

        try:
            # Carregar config
            with self.config_path.open("r", encoding="utf-8") as f:
                cfg = json.load(f)

            # Auto-config para Senior Trader (todos os filtros ativados)
            cfg["mode"] = "simulation" if is_simulation else "live"
            cfg["use_testnet"] = is_testnet
            cfg["wizard_complete"] = True

            broker = cfg.setdefault("broker", {})
            broker["use_testnet"] = is_testnet

            # Agent: ativa todos os filtros + learning + sanity-mode
            if "agent" not in cfg:
                cfg["agent"] = {}

            agent_cfg = cfg["agent"]
            agent_cfg["enabled"] = True
            agent_cfg["min_setup_quality"] = 65  # Floor para sanity-mode
            agent_cfg["min_setup_quality_floor"] = 40  # Mínimo
            agent_cfg["adx_min"] = 18.0  # Filtro ADX
            agent_cfg["volume_min_ratio"] = 0.8  # Filtro volume
            agent_cfg["loss_cooldown_multiplier"] = 2.0  # Anti-overtrading
            agent_cfg["trailing_enabled"] = True  # Trailing stops
            agent_cfg["cooldown_seconds"] = 180  # 3 min entre trades

            # Learning: ativado
            if "learning" not in cfg:
                cfg["learning"] = {}
            cfg["learning"]["enabled"] = True
            cfg["learning"]["min_trades_to_adjust"] = 3

            # Proteção de trade_size em REAL
            if not is_simulation:
                cfg["trade_size_pct"] = min(
                    float(cfg.get("trade_size_pct", 2.0)), 2.0
                )  # máx 2% em REAL

            # Persiste config
            with self.config_path.open("w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)

            # Ativa profile correto via script — mas não falha se não encontrar chaves
            # (usuário pode configurar via "🔑 Configurar API Key" depois)
            profile = "sim" if is_testnet else "real"
            try:
                script_path = (
                    self.config_path.parent.parent / "scripts" / "key_profiles.py"
                )
                result = subprocess.run(
                    [
                        "python",
                        str(script_path),
                        "activate",
                        f"--profile={profile}",
                        f"--config={self.config_path}",
                    ],
                    check=False,
                    capture_output=True,
                    timeout=5,
                    text=True,
                )
                # Log silenciosamente - não interrompe se falhar
                if result.returncode != 0:
                    print(
                        f"⚠️  Aviso: Não consegui ativar profile {profile}.\n"
                        f"   Use '🔑 Configurar API Key' para adicionar chaves depois.\n"
                        f"   Detalhes: {result.stderr[:200]}"
                    )
            except Exception as e:
                print(f"⚠️  Não consegui ativar profile: {e}")

            # Emite sinal e fecha
            self.wizard_complete.emit("simulation" if is_simulation else "live")
            self.accept()

        except Exception as e:
            QMessageBox.critical(
                self, "Erro", f"Erro ao configurar: {e}"
            )


class SeniorTraderPresetDialog(QDialog):
    """Dialog para aplicar / revertir preset Senior Trader em tempo de execução."""

    def __init__(self, config_path: Path, current_mode: str, parent=None):
        super().__init__(parent)
        self.config_path = config_path
        self.current_mode = current_mode
        self._init_ui()

    def _init_ui(self) -> None:
        self.setWindowTitle("🎓 Senior Trader Setup")
        self.setStyleSheet(
            "QDialog { background-color: #1a202c; } "
            "QLabel { color: #e5e7eb; } "
            "QPushButton { background-color: #3b82f6; color: white; border: none; "
            "border-radius: 6px; padding: 10px 20px; font-weight: bold; font-size: 12px; }"
            "QPushButton:hover { background-color: #2563eb; }"
        )
        self.setModal(True)
        self.setFixedSize(500, 300)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(15)

        title = QLabel("🎓 Ativar Setup Senior Trader?")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        info = QLabel(
            "Isso ativa todos os melhores filtros e ferramentas:\n"
            "✅ ADX ≥ 18 (apenas trends fortes)\n"
            "✅ Volume ≥ 80% da média\n"
            "✅ Sanity-mode (sizing dinâmico)\n"
            "✅ Learning automático (ajusta qualidade_mult)\n"
            "✅ Trailing stops (protege lucros)\n"
            "✅ Anti-overtrading (cooldown 3 min)\n"
            "\n💡 Recomendado para operar 24/7 com segurança."
        )
        info.setStyleSheet(
            "color: #cbd5e1; font-size: 11px; line-height: 1.6; "
            "background-color: #334155; border-radius: 6px; padding: 15px;"
        )
        layout.addWidget(info)

        # Botões
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        btn_enable = QPushButton("✅ Ativar Senior Setup")
        btn_enable.clicked.connect(self._apply_senior)
        btn_layout.addWidget(btn_enable)

        btn_cancel = QPushButton("❌ Cancelar")
        btn_cancel.setStyleSheet(
            "QPushButton { background-color: #64748b; color: white; }"
            "QPushButton:hover { background-color: #475569; }"
        )
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        layout.addLayout(btn_layout)
        layout.addStretch()

    def _apply_senior(self) -> None:
        """Aplica configuração Senior Trader e fecha."""
        try:
            with self.config_path.open("r", encoding="utf-8") as f:
                cfg = json.load(f)

            if "agent" not in cfg:
                cfg["agent"] = {}

            agent_cfg = cfg["agent"]
            agent_cfg["enabled"] = True
            agent_cfg["min_setup_quality"] = 65
            agent_cfg["min_setup_quality_floor"] = 40
            agent_cfg["adx_min"] = 18.0
            agent_cfg["volume_min_ratio"] = 0.8
            agent_cfg["loss_cooldown_multiplier"] = 2.0
            agent_cfg["trailing_enabled"] = True
            agent_cfg["cooldown_seconds"] = 180

            if "learning" not in cfg:
                cfg["learning"] = {}
            cfg["learning"]["enabled"] = True

            with self.config_path.open("w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)

            QMessageBox.information(
                self,
                "✅ Senior Trader Setup Ativado",
                "Todas as melhores ferramentas estão ativas!\n"
                "Reinicie o app para que as mudanças tenham efeito.",
            )
            self.accept()

        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Erro ao aplicar setup: {e}")
