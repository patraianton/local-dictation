# Запуск диктовки. Из любой папки.
#   .\run.ps1            — работать
#   .\run.ps1 mics       — какие есть микрофоны
#   .\run.ps1 keytest    — какой код у клавиши
#   .\run.ps1 selftest   — всё ли на месте
#   .\run.ps1 bench x.wav
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Error "Нет окружения: $py"; exit 1 }
$env:PYTHONUTF8 = "1"
Push-Location $root
try { & $py -m stt @args } finally { Pop-Location }
