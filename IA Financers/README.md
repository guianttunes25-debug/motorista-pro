# AI Trader Copilot

App desktop (Python + PyQt6) que conecta na Binance, mostra preço/indicadores em
tempo real, gera sinais de IA via score multifator (RSI + SMA + MACD + notícias),
simula trades e tem motor de risco com volatilidade, limites diários e kill switch.

> ⚠️ **Modo padrão = SIMULAÇÃO**. Nunca opere com dinheiro real antes de testar
> exaustivamente em sandbox/testnet.

## Estrutura

```
IA Financers/
├── main.py
├── config.json
├── requirements.txt
├── ui/
│   └── dashboard.py
├── core/
│   ├── market.py     # snapshot de preço + indicadores
│   ├── strategy.py   # score multifator -> BUY/SELL/HOLD
│   ├── risk.py       # limites, vol, kill switch
│   ├── news.py       # CryptoPanic
│   └── engine.py     # controller (QThread)
├── exchange/
│   └── binance.py    # ccxt + sandbox
└── data/             # (cache futuro)
```

## Setup

```powershell
cd "c:\projetos\IA Financers"
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

## Botões da UI
- **Ativar IA** — habilita execução automática (simulada por padrão).
- **Modo Manual** — desliga a IA (você só observa).
- **Pausar Operações** — bloqueia novas ordens, mantém posição.
- **⛔ Trava de Emergência** — kill switch global imediato.

## Score multifator
Cada fator soma +1, 0 ou -1:
- RSI < 30 → +1, RSI > 70 → -1
- SMA rápida > lenta → +1, < → -1
- MACD > sinal e hist > 0 → +1, opostos → -1
- Sentimento notícias → +1/-1 (se habilitado)

`score >= score_buy_threshold` → BUY · `score <= score_sell_threshold` → SELL · senão HOLD.

## Risk Engine
- `max_daily_loss_pct` — trava perda diária (kill switch automático).
- `max_volatility_pct` — bloqueia trades em alta volatilidade.
- `max_position_pct` — exposição máxima ao ativo.
- `trade_size_pct` — % do patrimônio por trade.

## Notícias (CryptoPanic)
1. Pegue um token grátis em https://cryptopanic.com/developers/api/
2. Em `config.json`: `"news_enabled": true`, `"cryptopanic_token": "SEU_TOKEN"`.

## Build do `.exe` (com ícone próprio)

```powershell
.\scripts\build.ps1            # use -Clean para forçar rebuild
```

Saída: `dist\AITraderCopilot.exe` — single-file, sem console, com `assets/app.ico`
embutido. O `AppUserModelID` configurado em `main.py` faz o ícone aparecer
corretamente também na barra de tarefas do Windows (não mostra o ícone do Python).

## Modo real (dinheiro de verdade)

1. Crie API Key na Binance com permissão **Spot Trading** (sem **Withdraw**),
   restrinja por IP se possível.
2. Edite `config.json`:
   ```json
   {
     "mode": "live",
     "use_testnet": false,
     "api_key": "SEU_KEY",
     "api_secret": "SEU_SECRET",
     "require_live_confirmation": true
   }
   ```
   Recomendado validar primeiro com `use_testnet: true`.
3. Ao abrir o app o título mostra **🔴 LIVE (dinheiro real)** e ao clicar em
   "Ativar IA" aparece um diálogo de confirmação.

## Atualização automática (sem recompilar para o usuário)

O app se atualiza sozinho — você só hospeda dois arquivos.

1. **Hospede em URL pública** (S3, GitHub Releases, seu site...) o novo
   `AITraderCopilot.exe` e um `manifest.json`:
   ```json
   {
     "version": "1.0.1",
     "url": "https://exemplo.com/releases/AITraderCopilot-1.0.1.exe",
     "sha256": "<sha256 hex do .exe>",
     "notes": "Correções e melhorias."
   }
   ```
   Veja `assets/update_manifest.example.json`.
2. No `config.json` distribuído com o app:
   ```json
   { "update_manifest_url": "https://exemplo.com/releases/manifest.json",
     "auto_check_update": true }
   ```
3. **Para publicar uma atualização:**
   1. Incremente `__version__` em `version.py`.
   2. Rode `.\scripts\build.ps1 -Clean`.
   3. Calcule o sha256:
      ```powershell
      (Get-FileHash .\dist\AITraderCopilot.exe -Algorithm SHA256).Hash.ToLower()
      ```
   4. Suba o `.exe` e atualize o `manifest.json` (mesmo URL).
4. O usuário clica em **"Verificar Atualização"** — o app baixa, valida o
   sha256, troca o próprio executável via um `.bat` temporário (`move /Y`) e
   relança. Não é preciso novo instalador, nem recompilar para cada usuário.

> O updater só age quando rodando empacotado (`sys.frozen=True`). No modo dev
> (`python main.py`) ele apenas reporta a versão disponível.

## Segurança
- Nunca commit de `config.json` com chaves preenchidas (já no `.gitignore`).
- Rotacione credenciais expostas antes de qualquer push.
- Comece com `use_testnet: true` antes de qualquer operação real.
- Em produção, sirva o `manifest.json` e os `.exe` por **HTTPS** e considere
  assinar o binário (Authenticode) para evitar SmartScreen.

## Roadmap
1. ✅ App + preço real-time + simulação
2. ✅ Score multifator + indicadores + risk vol
3. ✅ Notícias CryptoPanic
4. ✅ Build .exe distribuível com ícone próprio
5. ✅ Modo real (live) com confirmação
6. ✅ Auto-update in-place (manifest + sha256)
7. ⬜ Backtesting + métricas (Sharpe, drawdown)
8. ⬜ ML real (modelos preditivos)
