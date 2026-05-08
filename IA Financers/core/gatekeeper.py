"""Gatekeeper — usa o Ollama (SeniorAdvisor) como revisor SÍNCRONO da decisão da heurística.

Política
--------
- Heurística é SEMPRE quem propõe (rápida, determinística).
- O Gatekeeper só atua em entradas (BUY) ou em "quase-entradas" rejeitadas
  por baixa qualidade. Saídas (SELL/SL/TP) NUNCA passam pelo gatekeeper —
  proteção é prioridade absoluta.

Modos:
- VETO: heurística decide BUY → Ollama revisa; se discorda com confiança alta,
  vira HOLD.
- RESCUE: heurística decide HOLD por `quality < min_setup_quality` mas o
  contexto é razoável (>= rescue_quality_floor) → Ollama é consultado; se
  aprovar com confiança alta, libera BUY com tamanho reduzido (rescue_size_pct).

Se Ollama estiver offline / lento / desabilitado, o Gatekeeper não bloqueia
nada (passa decisão da heurística como veio).
"""
from __future__ import annotations

import json
import logging
import time
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Literal

log = logging.getLogger("gatekeeper")

Action = Literal["pass", "veto", "rescue", "skip"]


@dataclass
class GatekeeperConfig:
    enabled: bool = True
    url: str = "http://localhost:11434"
    model: str = "llama3:latest"
    timeout_sec: float = 8.0
    # Confiança mínima do LLM para vetar uma BUY proposta pela heurística.
    min_confidence_to_veto: int = 70
    # Confiança mínima para "resgatar" uma BUY barrada por baixa qualidade.
    min_confidence_to_rescue: int = 75
    # Não considera resgate se quality < este piso.
    rescue_quality_floor: int = 35
    # Em caso de resgate, executa com este % do tamanho normal (proteção).
    rescue_size_pct: int = 50


@dataclass
class GatekeeperVerdict:
    action: Action                 # "pass" | "veto" | "rescue" | "skip"
    final_signal: str              # "BUY" | "SELL" | "HOLD"
    size_pct: int = 100            # 100 = tamanho normal; <100 = reduzido
    reason: str = ""
    confidence: int = 0            # 0-100
    agree: bool = True
    latency_sec: float = 0.0
    error: str = ""


_PROMPT = """Você é um trader sênior revisando a decisão final de um robô.

Contexto:
- Par: {symbol}
- Preço: {price}
- Regime: {regime}
- RSI(14): {rsi}
- Volatilidade %: {volatility}
- MACD hist: {macd_hist}
- Sentimento (-3..+3): {sentiment}
- Notícias (-10..+10): {news}
- Qualidade do setup (0-100): {quality}
- Decisão proposta pela heurística: {proposed}
- Razões da heurística: {reasons}
- Posição atual: {position}
- PnL do dia %: {pnl_day}

Sua tarefa: dizer se a decisão é PRUDENTE para uma conta PEQUENA (saldo R$ 20)
operando em modo {mode}. Foque em PRESERVAÇÃO DE CAPITAL.

Responda APENAS um JSON válido (sem markdown), neste formato:
{{"agree": true|false, "confidence": 0-100, "comment": "frase curta pt-BR (<=120 chars)"}}
"""


class Gatekeeper:
    """Cliente síncrono e curto para o Ollama. Rápido o suficiente para o tick.

    Roda na thread do engine. Use timeout curto (default 8s) para que um Ollama
    travado não congele o loop. Em caso de falha, retorna ação "pass" e o tick
    segue sem interferência.
    """

    def __init__(self, cfg: GatekeeperConfig | None = None) -> None:
        self.cfg = cfg or GatekeeperConfig()

    # ---------- API pública ----------
    def review(
        self,
        proposed_signal: str,
        quality: int,
        min_quality: int,
        context: dict,
        has_position: bool,
    ) -> GatekeeperVerdict:
        """Decide o que fazer com a proposta da heurística."""
        # Saídas: nunca interferimos (proteção sempre passa).
        if proposed_signal == "SELL":
            return GatekeeperVerdict(action="pass", final_signal="SELL",
                                     reason="exit: gatekeeper não interfere")

        if not self.cfg.enabled:
            return GatekeeperVerdict(action="skip", final_signal=proposed_signal,
                                     reason="gatekeeper desabilitado")

        # Caso 1: heurística pediu BUY → veto check.
        if proposed_signal == "BUY":
            adv = self._ask(context | {"proposed": "BUY"})
            if adv.error:
                return GatekeeperVerdict(action="skip", final_signal="BUY",
                                         reason=f"gatekeeper offline: {adv.error}",
                                         error=adv.error, latency_sec=adv.latency_sec)
            if (not adv.agree) and adv.confidence >= self.cfg.min_confidence_to_veto:
                return GatekeeperVerdict(action="veto", final_signal="HOLD",
                                         reason=f"Ollama vetou: {adv.comment}",
                                         confidence=adv.confidence, agree=False,
                                         latency_sec=adv.latency_sec)
            return GatekeeperVerdict(action="pass", final_signal="BUY",
                                     reason=f"Ollama aprovou: {adv.comment}",
                                     confidence=adv.confidence, agree=adv.agree,
                                     latency_sec=adv.latency_sec)

        # Caso 2: heurística pediu HOLD → talvez seja resgatável.
        # Só consideramos resgate se NÃO houver posição (evita acumular)
        # e a qualidade estiver acima do piso (não comprar lixo).
        if proposed_signal == "HOLD" and not has_position:
            if quality < self.cfg.rescue_quality_floor:
                return GatekeeperVerdict(action="skip", final_signal="HOLD",
                                         reason=f"quality {quality} < piso {self.cfg.rescue_quality_floor}")
            if quality >= min_quality:
                # Já estaria liberada — heurística disse HOLD por outro motivo
                # (cooldown, regime, sentimento bearish). Não atropelamos.
                return GatekeeperVerdict(action="skip", final_signal="HOLD",
                                         reason="HOLD não foi por baixa qualidade")
            adv = self._ask(context | {"proposed": "HOLD (heurística rejeitou por baixa qualidade)"})
            if adv.error:
                return GatekeeperVerdict(action="skip", final_signal="HOLD",
                                         reason=f"gatekeeper offline: {adv.error}",
                                         error=adv.error, latency_sec=adv.latency_sec)
            if adv.agree and adv.confidence >= self.cfg.min_confidence_to_rescue:
                # Ollama acha que vale a pena mesmo com qualidade baixa.
                return GatekeeperVerdict(action="rescue", final_signal="BUY",
                                         size_pct=int(self.cfg.rescue_size_pct),
                                         reason=f"Ollama resgatou setup: {adv.comment}",
                                         confidence=adv.confidence, agree=True,
                                         latency_sec=adv.latency_sec)
            return GatekeeperVerdict(action="skip", final_signal="HOLD",
                                     reason=f"Ollama concordou com HOLD ({adv.comment})",
                                     confidence=adv.confidence, agree=adv.agree,
                                     latency_sec=adv.latency_sec)

        return GatekeeperVerdict(action="skip", final_signal=proposed_signal,
                                 reason="sem ação")

    # ---------- Internos ----------
    def _ask(self, context: dict) -> "_RawAdvice":
        prompt = _PROMPT.format(
            symbol=context.get("symbol", "?"),
            price=context.get("price", 0),
            regime=context.get("regime", "?"),
            rsi=context.get("rsi", 0),
            volatility=context.get("volatility", 0),
            macd_hist=context.get("macd_hist", 0),
            sentiment=context.get("sentiment", 0),
            news=context.get("news", 0),
            quality=context.get("quality", 0),
            proposed=context.get("proposed", "?"),
            reasons=context.get("reasons", "?"),
            position=context.get("position", "flat"),
            pnl_day=context.get("pnl_day", 0.0),
            mode=context.get("mode", "simulation"),
        )
        body = json.dumps({
            "model": self.cfg.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.2, "num_predict": 160},
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{self.cfg.url.rstrip('/')}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout_sec) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.URLError as e:
            return _RawAdvice(error=f"offline ({e.reason})", latency_sec=time.time() - t0)
        except Exception as e:  # noqa: BLE001
            return _RawAdvice(error=str(e), latency_sec=time.time() - t0)
        try:
            data = json.loads(raw)
            text = data.get("response", "").strip()
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                import re
                m = re.search(r"\{.*\}", text, re.DOTALL)
                if not m:
                    return _RawAdvice(comment=text[:120], confidence=50, agree=True,
                                      latency_sec=time.time() - t0)
                parsed = json.loads(m.group(0))
            return _RawAdvice(
                comment=str(parsed.get("comment", ""))[:200],
                confidence=int(max(0, min(100, parsed.get("confidence", 50)))),
                agree=bool(parsed.get("agree", True)),
                latency_sec=time.time() - t0,
            )
        except Exception as e:  # noqa: BLE001
            return _RawAdvice(error=f"parse: {e}", latency_sec=time.time() - t0)


@dataclass
class _RawAdvice:
    comment: str = ""
    confidence: int = 50
    agree: bool = True
    error: str = ""
    latency_sec: float = 0.0
