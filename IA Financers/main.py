"""Ponto de entrada do AI Trader Copilot."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication

# Logger central — captura stdout/stderr/exceptions/Qt para data/app.log
# DEVE ser instalado ANTES de qualquer outro import que possa logar/printar.
from core.applog import install as _install_logger
_LOG_FILE = _install_logger()

from core.engine import EngineConfig, EngineController, EngineWorker
from core.learning import LearningConfig, LearningEngine
from core.gatekeeper_learning import GatekeeperLearning, GkLearningConfig
from core.market import MarketDataService
from core.news import NewsConfig, NewsService
from core.risk import RiskConfig, RiskEngine
from core.strategy import ScoringStrategy, StrategyConfig
from core.trader_agent import AgentConfig, SeniorTraderAgent
from core.paths import config_path
from exchange.factory import build_broker
from ui.dashboard import Dashboard
from ui.splash import WelcomeDialog
from ui.wizard_dialog import StartupWizardDialog

CONFIG_PATH = config_path()


def _normalize_symbol_to_usdt(raw_symbol: str) -> str:
    s = str(raw_symbol or "BTC/USDT").strip().upper()
    if "/" in s:
        base = s.split("/", 1)[0].strip()
    else:
        base = s[:-4] if s.endswith("USDT") and len(s) > 4 else s
    if not base:
        base = "BTC"
    return f"{base}/USDT"


def _seed_config_if_missing() -> None:
    """Em .exe (PyInstaller) o config.json bundled fica em sys._MEIPASS.
    Copia para o diretório do .exe na primeira execução para persistir edições."""
    if CONFIG_PATH.exists():
        return
    bundled = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "config.json"
    if bundled.exists() and bundled.resolve() != CONFIG_PATH.resolve():
        try:
            CONFIG_PATH.write_text(bundled.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            pass


def load_config() -> dict:
    _seed_config_if_missing()
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        cfg = json.load(f)
    # Segurança: move chaves em texto pro Windows Credential Manager
    # (idempotente — só age na 1ª vez ou se voltar a aparecer texto plano)
    try:
        from core.secrets import migrate_config_to_keyring, hydrate_config
        migrated = migrate_config_to_keyring(cfg, CONFIG_PATH)
        if migrated:
            print("🔐 API key migrada pro Windows Credential Manager (config.json limpo).")
        # Hidrata cfg em memória com valores reais do keyring
        cfg = hydrate_config(cfg)  # ⬅️ IMPORTANTE: atribuir o retorno!
    except Exception as e:  # noqa: BLE001
        print(f"⚠ keyring indisponível ({e}) — usando config.json em texto (legado).")
    return cfg


def _auto_protect_config(cfg: dict) -> dict:
    """IA mentor: aplica regra dos 1-2% automaticamente.

    Se trade_size_pct estiver acima de 5% (zona de risco amador),
    reduz para 2% e persiste. Avisa no console.
    Só age em modo live — em simulação respeita o que o usuário pôs.
    """
    if cfg.get("mode") != "live":
        return cfg
    pct = float(cfg.get("trade_size_pct", 2.0))
    if pct <= 5.0:
        return cfg
    print(f"\n{'='*60}")
    print(f"🛡  IA MENTOR: trade_size_pct={pct}% é alto demais.")
    print(f"   Aplicando regra dos profissionais (1-2%): reduzindo para 2.0%.")
    print(f"   Você pode reverter manualmente em config.json se quiser.")
    print(f"{'='*60}\n")
    cfg["trade_size_pct"] = 2.0
    # Persiste atomicamente (tempfile + os.replace)
    import tempfile, os
    try:
        d = CONFIG_PATH.parent
        fd, tmp = tempfile.mkstemp(prefix=".cfg_", suffix=".json", dir=str(d))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(CONFIG_PATH))
    except Exception as e:  # noqa: BLE001
        print(f"⚠ não consegui persistir auto-correção: {e}")
    return cfg


def main() -> int:
    cfg = load_config()
    cfg = _auto_protect_config(cfg)

    mode = str(cfg.get("mode", "simulation")).lower()
    use_testnet = bool((cfg.get("broker") or {}).get("use_testnet", cfg.get("use_testnet", True)))

    # Compatibilidade com nomes antigos vindos do wizard.
    if mode == "real":
        mode = "live"
        use_testnet = False
    elif mode == "testnet":
        mode = "live"
        use_testnet = True

    # Garante que o par sempre opere em USDT (real e testnet).
    original_symbol = str(cfg.get("symbol", "BTC/USDT"))
    normalized_symbol = _normalize_symbol_to_usdt(original_symbol)
    if normalized_symbol != original_symbol:
        print(f"ℹ Ajustando par para USDT: {original_symbol} -> {normalized_symbol}")
    cfg["symbol"] = normalized_symbol

    cfg["use_testnet"] = use_testnet
    broker_cfg = cfg.setdefault("broker", {})
    broker_cfg["use_testnet"] = use_testnet

    api_key = (cfg.get("broker") or {}).get("api_key") or cfg.get("api_key", "")
    api_secret = (cfg.get("broker") or {}).get("api_secret") or cfg.get("api_secret", "")
    
    # Se em LIVE mas sem chaves, força SIMULAÇÃO com aviso claro
    if mode == "live" and not (api_key and api_secret):
        print("\n" + "="*70)
        print("🔴 ERRO: Você escolheu MODO REAL, mas não tem API keys configuradas!")
        print("         Use '🔑 Configurar API Key' no app para adicionar suas chaves.")
        print("         Forçando modo SIMULAÇÃO por segurança.")
        print("="*70 + "\n")
        mode = "simulation"

    client = build_broker(cfg)

    market = MarketDataService(client)

    strategy = ScoringStrategy(StrategyConfig(
        rsi_buy=float(cfg.get("rsi_buy", 30)),
        rsi_sell=float(cfg.get("rsi_sell", 70)),
        score_buy_threshold=int(cfg.get("score_buy_threshold", 2)),
        score_sell_threshold=int(cfg.get("score_sell_threshold", -2)),
    ))

    risk = RiskEngine(RiskConfig(
        initial_balance_usdt=float(cfg.get("initial_balance_usdt", 1000.0)),
        trade_size_pct=float(cfg.get("trade_size_pct", 10.0)),
        max_daily_loss_pct=float(cfg.get("max_daily_loss_pct", 2.0)),
        max_position_pct=float(cfg.get("max_position_pct", 25.0)),
        max_volatility_pct=float(cfg.get("max_volatility_pct", 3.0)),
    ))

    news = NewsService(NewsConfig(
        token=cfg.get("cryptopanic_token", ""),
        currencies=cfg.get("news_currencies", "BTC,ETH"),
        enabled=bool(cfg.get("news_enabled", False)),
    ))

    agent_cfg = cfg.get("agent", {}) or {}
    agent: SeniorTraderAgent | None = None
    agent_baseline: AgentConfig | None = None
    if bool(agent_cfg.get("enabled", True)):
        baseline = AgentConfig(
            extreme_vol_pct=float(agent_cfg.get("extreme_vol_pct", 2.5)),
            calm_vol_pct=float(agent_cfg.get("calm_vol_pct", 0.15)),
            trend_strength_min_pct=float(agent_cfg.get("trend_strength_min_pct", 0.05)),
            min_setup_quality=int(agent_cfg.get("min_setup_quality", 60)),
            min_setup_quality_floor=int(agent_cfg.get("min_setup_quality_floor", 35)),
            risk_adjusted_min_size=float(agent_cfg.get("risk_adjusted_min_size", 0.25)),
            cooldown_seconds=int(agent_cfg.get("cooldown_seconds", 60)),
            stop_loss_pct=float(agent_cfg.get("stop_loss_pct", 1.0)),
            take_profit_pct=float(agent_cfg.get("take_profit_pct", 2.0)),
            trailing_enabled=bool(agent_cfg.get("trailing_enabled", True)),
            trailing_activation_pct=float(agent_cfg.get("trailing_activation_pct", 0.5)),
            trailing_distance_pct=float(agent_cfg.get("trailing_distance_pct", 0.5)),
            adx_min=float(agent_cfg.get("adx_min", 18.0)),
            volume_min_ratio=float(agent_cfg.get("volume_min_ratio", 0.8)),
            loss_cooldown_multiplier=float(agent_cfg.get("loss_cooldown_multiplier", 2.0)),
            min_tp_to_fee_ratio=float(agent_cfg.get("min_tp_to_fee_ratio", 4.0)),
            fee_pct_per_side=float(agent_cfg.get("fee_pct_per_side", 0.1)),
            max_trade_duration_seconds=int(agent_cfg.get("max_trade_duration_seconds", 0)),
        )
        agent_baseline = baseline
        agent = SeniorTraderAgent(AgentConfig(**baseline.__dict__))

    # LearningEngine — ajusta thresholds da IA com base no histórico
    learning_cfg_dict = cfg.get("learning", {}) or {}
    learning: LearningEngine | None = None
    if bool(learning_cfg_dict.get("enabled", True)) and agent is not None:
        from core.paths import data_dir
        risk_profile = cfg.get("current_risk_profile", "bom")
        l_cfg = LearningConfig(
            persist_path=data_dir() / f"learning_{risk_profile}.json",
            risk_profile=risk_profile,
            min_trades_to_adjust=int(learning_cfg_dict.get("min_trades_to_adjust", 5)),
            review_every_n_trades=int(learning_cfg_dict.get("review_every_n_trades", 3)),
            max_quality_mult=float(learning_cfg_dict.get("max_quality_mult", 1.30)),
            min_quality_mult=float(learning_cfg_dict.get("min_quality_mult", 0.70)),
        )
        learning = LearningEngine(l_cfg)

    # 🧠 GatekeeperLearning — ajusta thresholds do Gatekeeper LLM com base no histórico
    gk_learning_cfg_dict = (cfg.get("gatekeeper") or {}).get("learning", {}) or {}
    gatekeeper_learning: GatekeeperLearning | None = None
    if bool(gk_learning_cfg_dict.get("enabled", True)):
        from core.paths import data_dir
        gk_l_cfg = GkLearningConfig(
            persist_path=data_dir() / "gatekeeper_learning.json",
            min_samples_to_adjust=int(gk_learning_cfg_dict.get("min_samples_to_adjust", 5)),
            review_every_n=int(gk_learning_cfg_dict.get("review_every_n", 1)),
            max_veto_delta=int(gk_learning_cfg_dict.get("max_veto_delta", 20)),
            max_rescue_delta=int(gk_learning_cfg_dict.get("max_rescue_delta", 20)),
        )
        gatekeeper_learning = GatekeeperLearning(gk_l_cfg)

    eng_cfg = EngineConfig(
        symbol=cfg.get("symbol", "BTC/USDT"),
        timeframe=cfg.get("timeframe", "1m"),
        mode=mode,  # type: ignore[arg-type]
        poll_interval_seconds=int(cfg.get("poll_interval_seconds", 5)),
        initial_balance_usdt=float(cfg.get("initial_balance_usdt", 1000.0)),
        rsi_period=int(cfg.get("rsi_period", 14)),
        sma_fast=int(cfg.get("sma_fast", 9)),
        sma_slow=int(cfg.get("sma_slow", 21)),
        macd_fast=int(cfg.get("macd_fast", 12)),
        macd_slow=int(cfg.get("macd_slow", 26)),
        macd_signal=int(cfg.get("macd_signal", 9)),
        # 🎓 Conselheiro Senior LLM (só comenta)
        senior_enabled=bool((cfg.get("senior") or {}).get("enabled", False)),
        senior_url=str((cfg.get("senior") or {}).get("url", "http://localhost:11434")),
        senior_model=str((cfg.get("senior") or {}).get("model", "llama3:latest")),
        senior_timeout_sec=float((cfg.get("senior") or {}).get("timeout_sec", 15.0)),
        senior_min_interval_sec=float((cfg.get("senior") or {}).get("min_interval_sec", 60.0)),
        # 🛡 Gatekeeper Ollama (aprova/veta)
        gatekeeper_enabled=bool((cfg.get("gatekeeper") or {}).get("enabled", False)),
        gatekeeper_url=str((cfg.get("gatekeeper") or {}).get("url", "http://localhost:11434")),
        gatekeeper_model=str((cfg.get("gatekeeper") or {}).get("model", "llama3:latest")),
        gatekeeper_timeout_sec=float((cfg.get("gatekeeper") or {}).get("timeout_sec", 8.0)),
        gatekeeper_min_confidence_to_veto=int((cfg.get("gatekeeper") or {}).get("min_confidence_to_veto", 70)),
        gatekeeper_min_confidence_to_rescue=int((cfg.get("gatekeeper") or {}).get("min_confidence_to_rescue", 75)),
        gatekeeper_rescue_quality_floor=int((cfg.get("gatekeeper") or {}).get("rescue_quality_floor", 35)),
        gatekeeper_rescue_size_pct=int((cfg.get("gatekeeper") or {}).get("rescue_size_pct", 50)),
    )

    app = QApplication(sys.argv)
    app.setApplicationName("AI Trader Copilot")

    # 🎓 Startup Wizard: se primeira vez (wizard_complete=false), mostra dialog
    if not bool(cfg.get("wizard_complete", False)):
        wizard = StartupWizardDialog(CONFIG_PATH)
        wizard.wizard_complete.connect(
            lambda mode: cfg.update({"mode": mode, "wizard_complete": True})
        )
        if wizard.exec() != wizard.DialogCode.Accepted:
            # Usuário cancelou wizard; recarrega config (atualizada pelo wizard)
            cfg = load_config()

    # AppUserModelID: faz o ícone aparecer corretamente na barra de tarefas do Windows
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "AITraderCopilot.Desktop.1"
            )
        except Exception:
            pass

    # Fallback robusto para ícone no Windows/pyinstaller.
    # Ordem: ícone do próprio EXE -> assets no _internal -> assets no _MEIPASS -> dev path.
    icon_candidates = []
    if getattr(sys, "frozen", False):
        icon_candidates.append(Path(sys.executable))
        icon_candidates.append(Path(sys.executable).resolve().parent / "_internal" / "assets" / "app.ico")
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        icon_candidates.append(Path(meipass) / "assets" / "app.ico")
    icon_candidates.append(Path(__file__).resolve().parent / "assets" / "app.ico")

    from PyQt6.QtGui import QIcon
    for p in icon_candidates:
        try:
            if p.exists():
                icon = QIcon(str(p))
                if not icon.isNull():
                    app.setWindowIcon(icon)
                    break
        except Exception:
            continue

    # Tela de boas-vindas com botão "Iniciar Trade"
    welcome = WelcomeDialog(mode=mode, use_testnet=use_testnet)
    if welcome.exec() != welcome.DialogCode.Accepted:
        # Usuário clicou em "Sair" na tela inicial
        return 0

    worker = EngineWorker(
        eng_cfg, client, market, strategy, risk, news,
        agent=agent,
        learning=learning,
        agent_baseline=agent_baseline,
        gatekeeper_learning=gatekeeper_learning,
    )
    controller = EngineController(worker)
    window = Dashboard(
        controller,
        mode=mode,
        use_testnet=use_testnet,
        update_manifest_url=cfg.get("update_manifest_url", ""),
        require_live_confirmation=bool(cfg.get("require_live_confirmation", True)),
    )
    window.show()
    controller.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
