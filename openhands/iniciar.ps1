# Script de inicializacao do OpenHands CLI
# Uso: .\iniciar.ps1 [opcoes]
#   .\iniciar.ps1                          -> modo interativo (TUI)
#   .\iniciar.ps1 -Tarefa "corrija o bug"  -> modo headless com tarefa
#   .\iniciar.ps1 -Web                     -> interface no navegador

param(
    [string]$Tarefa = "",
    [switch]$Web,
    [switch]$AutoAprovar
)

$env:Path = "C:\Users\Gui_G\.local\bin;$env:Path"

if ($Web) {
    Write-Host "Abrindo OpenHands no navegador..." -ForegroundColor Cyan
    openhands web
} elseif ($Tarefa -ne "") {
    $flags = @("--headless", "-t", $Tarefa)
    if ($AutoAprovar) { $flags += "--always-approve" }
    Write-Host "Executando tarefa: $Tarefa" -ForegroundColor Cyan
    & openhands @flags
} else {
    Write-Host "Iniciando OpenHands CLI interativo..." -ForegroundColor Cyan
    Write-Host "Na primeira execucao voce configurara o modelo de LLM." -ForegroundColor Yellow
    openhands
}
