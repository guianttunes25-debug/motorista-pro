# Build do executável AI Trader Copilot
# Uso: .\scripts\build.ps1
param(
    [switch]$Clean
)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)

if ($Clean) {
    Write-Host "Limpando build/ e dist/..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
}

Write-Host "Gerando ícone..." -ForegroundColor Cyan
& '.\venv\Scripts\python.exe' scripts\generate_icon.py

Write-Host "Compilando .exe com PyInstaller..." -ForegroundColor Cyan
& '.\venv\Scripts\python.exe' -m PyInstaller --noconfirm AITraderCopilot.spec

if (Test-Path .\dist\AITraderCopilot\AITraderCopilot.exe) {
    Write-Host "✅ Build OK -> dist\AITraderCopilot\AITraderCopilot.exe" -ForegroundColor Green
} else {
    Write-Error "❌ Build falhou."
}
