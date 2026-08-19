# Запускает диктовку отдельным процессом — она живёт сама по себе и не зависит
# от окна, из которого её запустили.
#   .\start-background.ps1          — поднять (если уже работает, ничего не делает)
#   .\start-background.ps1 -Restart — перезапустить
#   .\start-background.ps1 -Stop    — остановить
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
        "остановил процесс $($p.ProcessId)"
    }
    if (-not $running) { "она и не работала" }
    if ($Stop) { return }
    Start-Sleep -Milliseconds 500
} elseif ($running) {
    "уже работает (процесс $($running[0].ProcessId)). Перезапустить: -Restart"
    return
}

if (-not (Test-Path $py)) { Write-Error "Нет окружения: $py"; exit 1 }

$env:PYTHONUTF8 = "1"
$env:HF_HUB_OFFLINE = "1"   # модели уже на диске, в сеть не ходим
Start-Process -FilePath $py -ArgumentList "-u", "-m", "stt" `
    -WorkingDirectory $root -WindowStyle Hidden `
    -RedirectStandardOutput $out -RedirectStandardError $err | Out-Null

Start-Sleep -Seconds 3
$now = @(Get-Dictation)
if ($now) {
    "запустил, процесс $($now[0].ProcessId)"
    "журнал: $out"
    "страница: http://127.0.0.1:8756/"
    "Модель грузится ~2 секунды. F13 заработает сразу после этого."
} else {
    Write-Error "не поднялась — смотри $err"
    if (Test-Path $err) { Get-Content $err -Tail 15 }
}
