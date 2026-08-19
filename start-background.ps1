# Starts dictation as its own process: it lives on its own and does not depend
# on the window it was launched from.
#   .\start-background.ps1          — start (does nothing if already running)
#   .\start-background.ps1 -Restart — restart
#   .\start-background.ps1 -Stop    — stop
param([switch]$Restart, [switch]$Stop)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $root ".venv\Scripts\python.exe"
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$out = Join-Path $logDir "service.log"
$err = Join-Path $logDir "service-errors.log"

function Get-Dictation {
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -like "*-m stt*" }
}

$running = @(Get-Dictation)

if ($Stop -or $Restart) {
    foreach ($p in $running) {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        "stopped process $($p.ProcessId)"
    }
    if (-not $running) { "it was not running" }
    if ($Stop) { return }
    Start-Sleep -Milliseconds 500
} elseif ($running) {
    "already running (process $($running[0].ProcessId)). To restart: -Restart"
    return
}

if (-not (Test-Path $py)) { Write-Error "No environment at: $py"; exit 1 }

$env:PYTHONUTF8 = "1"
$env:HF_HUB_OFFLINE = "1"   # models are already on disk; stay off the network
Start-Process -FilePath $py -ArgumentList "-u", "-m", "stt" `
    -WorkingDirectory $root -WindowStyle Hidden `
    -RedirectStandardOutput $out -RedirectStandardError $err | Out-Null

Start-Sleep -Seconds 3
$now = @(Get-Dictation)
if ($now) {
    "started, process $($now[0].ProcessId)"
    "log: $out"
    "page: http://127.0.0.1:8756/"
    "The model loads in ~2 seconds. F13 works right after that."
} else {
    Write-Error "it did not start — see $err"
    if (Test-Path $err) { Get-Content $err -Tail 15 }
}
