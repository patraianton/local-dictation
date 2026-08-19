# Ставит диктовку в автозапуск при входе в Windows.
#   .\install-autostart.ps1          — поставить
#   .\install-autostart.ps1 -Remove  — убрать
param([switch]$Remove)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$startup = [Environment]::GetFolderPath('Startup')
$lnk = Join-Path $startup "Диктовка.lnk"

if ($Remove) {
    if (Test-Path $lnk) { Remove-Item $lnk -Force; "Убрал из автозапуска." }
    else { "В автозапуске её и не было." }
    return
}

$starter = Join-Path $root "start-background.ps1"
if (-not (Test-Path $starter)) { Write-Error "Нет файла: $starter"; exit 1 }

# Через start-background.ps1, а не напрямую: он пишет журнал и не поднимает
# вторую копию, если диктовка уже работает.
$shell = New-Object -ComObject WScript.Shell
$s = $shell.CreateShortcut($lnk)
$s.TargetPath = "powershell.exe"
$s.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$starter`""
$s.WorkingDirectory = $root
$s.Description = "Диктовка: горячая клавиша -> текст в активном окне"
$s.Save()

"Готово. Ярлык: $lnk"
"Теперь после перезагрузки диктовка поднимается сама (~15 секунд на загрузку модели)."
