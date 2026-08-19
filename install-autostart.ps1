# Adds the dictation app to Windows startup.
#   .\install-autostart.ps1          — install
#   .\install-autostart.ps1 -Remove  — remove
param([switch]$Remove)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$startup = [Environment]::GetFolderPath('Startup')
$lnk = Join-Path $startup "Dictation.lnk"

if ($Remove) {
    if (Test-Path $lnk) { Remove-Item $lnk -Force; "Removed from startup." }
    else { "It was not in startup anyway." }
    return
}

$starter = Join-Path $root "start-background.ps1"
if (-not (Test-Path $starter)) { Write-Error "No such file: $starter"; exit 1 }

# Through start-background.ps1 rather than directly: it writes the log and will
# not start a second copy if dictation is already running.
$shell = New-Object -ComObject WScript.Shell
$s = $shell.CreateShortcut($lnk)
$s.TargetPath = "powershell.exe"
$s.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$starter`""
$s.WorkingDirectory = $root
$s.Description = "Dictation: a hotkey -> text in the active window"
$s.Save()

"Done. Shortcut: $lnk"
"Dictation now starts by itself after a reboot (~15 s to load the model)."
