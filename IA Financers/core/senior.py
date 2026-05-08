"""Conselheiro Senior LLM (Ollama local).

Roda em thread separada (fire-and-forget) — NUNCA bloqueia o tick da heurística.
Recebe contexto da decisão e devolve {comment, confidence, agree} via callback.

Uso defensivo: se Ollama estiver off ou demorar > timeout, simplesmente não emite.
NÃO bloqueia trades, NÃO altera tamanho — só comenta. Heurística manda.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

import urllib.request
import urllib.error


@dataclass
class SeniorConfig:
    enabled: bool = False
    url: str = "http://localhost:11434"
    model: str = "llama3:latest"
    timeout_sec: float = 8.0
    min_interval_sec: float = 30.0  # anti-spam: 1 análise a cada 30s no máximo


@dataclass
class SeniorAdvice:
    comment: str = ""
    confidence: int = 0  # 0-100
    agree: bool = True
    model: str = ""
    latency_sec: float = 0.0
    error: str = ""


PROMPT_TEMPLATE = """Você é um trader senior experiente revisando a decisão de um robô de trading.
Sua função é COMENTAR e dar um nível de confiança — você NÃO decide nada, só observa.

Contexto de mercado AGORA:
- Par: {symbol}
- Preço atual: {price}
- Tendência (EMA20 vs EMA50): {trend}
- RSI(14): {rsi}
- Última decisão do robô: {decision}
- Razão da heurística: {reason}
- Notícias (sentimento -10 a +10): {news_score}
- Posição atual: {position}
- PnL do dia: {pnl_day}%

Responda APENAS um JSON válido (sem markdown, sem ```), neste formato exato:
{{"comment": "frase curta em pt-BR (máximo 100 chars)", "confidence": 0-100, "agree": true|false}}

Onde:
- comment: sua opinião curta e prática
- confidence: 0=desconfiado, 100=certeza absoluta na decisão do robô
- agree: true se concorda com a decisão, false se você faria diferente
"""


class SeniorAdvisor:
    """Cliente Ollama assíncrono + thread-safe."""

    def __init__(self, cfg: Optional[SeniorConfig] = None):
        self.cfg = cfg or SeniorConfig()
        self._last_call_ts: float = 0.0
        self._lock = threading.Lock()
        self._inflight = False

    def _can_call_now(self) -> bool:
        import time
        with self._lock:
            if self._inflight:
                return False
            if time.time() - self._last_call_ts < self.cfg.min_interval_sec:
                return False
            return True

    def ask(self, context: dict, callback: Callable[[SeniorAdvice], None]) -> bool:
        """Dispara análise em thread separada. Retorna True se enfileirou."""
        if not self.cfg.enabled:
            return False
        if not self._can_call_now():
            return False
        with self._lock:
            self._inflight = True
        t = threading.Thread(
            target=self._worker,
            args=(context, callback),
            daemon=True,
            name="SeniorAdvisor",
        )
        t.start()
        return True

    def _worker(self, context: dict, callback: Callable[[SeniorAdvice], None]) -> None:
        import time
        t0 = time.time()
        try:
            advice = self._call_ollama(context)
            advice.latency_sec = round(time.time() - t0, 2)
            advice.model = self.cfg.model
        except Exception as e:  # noqa: BLE001
            advice = SeniorAdvice(error=str(e), latency_sec=round(time.time() - t0, 2))
        finally:
            with self._lock:
                self._inflight = False
                self._last_call_ts = time.time()
        try:
            callback(advice)
        except Exception:  # noqa: BLE001
            pass

    def _call_ollama(self, context: dict) -> SeniorAdvice:
        prompt = PROMPT_TEMPLATE.format(
            symbol=context.get("symbol", "?"),
            price=context.get("price", 0),
            trend=context.get("trend", "?"),
            rsi=context.get("rsi", 0),
            decision=context.get("decision", "HOLD"),
            reason=context.get("reason", "?"),
            news_score=context.get("news_score", 0),
            position=context.get("position", "flat"),
            pnl_day=context.get("pnl_day", 0.0),
        )
        body = json.dumps({
            "model": self.cfg.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.3, "num_predict": 200},
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{self.cfg.url.rstrip('/')}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.cfg.timeout_sec) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
        text = data.get("response", "").strip()
        # Tenta parsear JSON da resposta
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            # fallback: pega primeiro {…}
            import re
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if not m:
                return SeniorAdvice(comment=text[:100], confidence=50, agree=True)
            parsed = json.loads(m.group(0))
        return SeniorAdvice(
            comment=str(parsed.get("comment", ""))[:200],
            confidence=int(max(0, min(100, parsed.get("confidence", 50)))),
            agree=bool(parsed.get("agree", True)),
        )

    def health_check(self) -> tuple[bool, str]:
        """Testa se Ollama está alcançável e modelo disponível."""
        try:
            req = urllib.request.Request(f"{self.cfg.url.rstrip('/')}/api/tags")
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            models = [m.get("name", "") for m in data.get("models", [])]
            if self.cfg.model not in models:
                return False, f"modelo '{self.cfg.model}' não está em {models}"
            return True, "ok"
        except urllib.error.URLError as e:
            return False, f"Ollama offline ({e.reason})"
        except Exception as e:  # noqa: BLE001
            return False, str(e)
