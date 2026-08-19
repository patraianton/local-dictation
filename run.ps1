# Runs the dictation app. Works from any folder.
#   .\run.ps1            — run it
#   .\run.ps1 mics       — list the microphones
#   .\run.ps1 keytest    — find the scan code of a key
#   .\run.ps1 selftest   — check everything is in place
#   .\run.ps1 bench x.wav
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Error "No environment at: $py"; exit 1 }
$env:PYTHONUTF8 = "1"
Push-Location $root
try { & $py -m stt @args } finally { Pop-Location }
