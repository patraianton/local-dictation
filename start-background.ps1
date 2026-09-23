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
# pythonw, not python: a console-less build. python.exe always gets a console,
# even a hidden one, and dies the moment that console is torn down - Windows
# sends every attached process a close event, and the recognizer's Intel
# runtime turns it into an instant abort:
#   forrtl: error (200): program aborting due to window-CLOSE event
# That is how dictation died on 24.08.2026 at 17:15:58 after five and a half
# healthy hours, with nothing of its own in the log and no sleep, logoff or
# shutdown anywhere in the Windows event log.
# Measured the same day, killing the console host under a live process:
#   python.exe                                  -> dies
#   python.exe + FOR_DISABLE_CONSOLE_CTRL_HANDLER -> dies as well
#   pythonw.exe                                 -> survives
# pythonw has no console to lose, so no close event can reach it. Redirection
# still works: it writes to the handles given here, log and all.
$pyw = Join-Path $root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pyw)) { $pyw = $py }
Start-Process -FilePath $pyw -ArgumentList "-u", "-m", "stt" `
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
