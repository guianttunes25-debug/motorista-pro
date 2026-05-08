# 🚀 AI Trader Copilot — Guia de Setup

## ⚡ Primeiras 3 passos

### 1️⃣ Salve suas API Keys (UMA VEZ)

```bash
python scripts/setup_keys.py
```

Vai pedir para digitar:
- API Key da Binance (Testnet/Simulação)
- API Secret da Binance (Testnet/Simulação)
- API Key da Binance (REAL)
- API Secret da Binance (REAL)

✅ Pronto! As chaves ficam criptografadas no Windows Credential Manager.

---

### 2️⃣ Abra o App

Clique em `dist/AITraderCopilot/AITraderCopilot.exe`

---

### 3️⃣ Escolha o Modo

![Banner de modo](assets/banner.png)

**🟢 SIMULAÇÃO** — Testa a IA com dinheiro fake (Testnet)  
**🔴 REAL** — Operação com seu dinheiro verdadeiro

Escolha um e pronto! Chaves carregam automaticamente.

---

## 🎯 Fluxo Completo

```
┌─────────────────┐
│  Primeira vez   │
│  python setup_  │
│     keys.py     │  ← Digita as 4 chaves UMA VEZ
└────────┬────────┘
         │
         ↓
┌────────────────────┐
│  Abre o app        │
│  AITraderCopilot   │
└────────┬───────────┘
         │
         ↓
┌────────────────────────┐
│  Wizard pergunta:      │
│  🟢 SIM ou 🔴 REAL?   │
└────────┬───────────────┘
         │
    ┌────┴────┐
    ↓         ↓
 SIM      REAL
 │         │
 ├─→ Testa com fake
 └─→ Opera com $.real
```

---

## 🔐 Segurança

- ✅ Chaves guardadas no Windows Credential Manager (criptografadas)
- ✅ Nunca aparecem em config.json
- ✅ API Keys com **READ ONLY** recomendado
- ✅ Crie 2 chaves na Binance: 1 para SIM, 1 para REAL

---

## 🆘 Troubleshooting

**P: "Erro ao salvar chaves"**  
R: Certifique-se que está rodando em Windows com Python 3.10+

**P: "Esqueci de salvar as chaves"**  
R: Rode `python scripts/setup_keys.py` novamente!

**P: "Quer trocar de conta (SIM → REAL ou vice-versa)"**  
R: Abra o app, wizard pergunta toda vez. Escolha o modo desejado.

---

## 📋 Arquivos Importantes

- `dist/AITraderCopilot/AITraderCopilot.exe` — App principal
- `dist/AITraderCopilot/config.json` — Configuração (chaves ficam em `***KEYRING***`)
- `scripts/setup_keys.py` — Setup de credenciais
- `data/app.log` — Log de operações

---

## ✅ Pronto!

Agora é só:
1. Rodar `setup_keys.py` (uma vez)
2. Abrir o app
3. Escolher modo
4. Usar!

Qualquer dúvida, abra `data/app.log` pra diagnosticar. 🎓
