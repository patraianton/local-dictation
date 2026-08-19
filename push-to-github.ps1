# Создаёт репозиторий на GitHub и заливает туда проект.
# Запускать после того, как у токена появится право заводить репозитории.
#   .\push-to-github.ps1                 — публичный
#   .\push-to-github.ps1 -Private        — закрытый
#   .\push-to-github.ps1 -Name другое-имя
param([string]$Name = "local-dictation", [switch]$Private)

$ErrorActionPreference = "Stop"
$tok = (Get-Content "$env:USERPROFILE\.secrets\github-token" -Raw).Trim()
$h = @{ Authorization = "Bearer $tok"; "User-Agent" = "stt"; Accept = "application/vnd.github+json" }

$me = (Invoke-RestMethod -Uri "https://api.github.com/user" -Headers $h).login
"аккаунт: $me"

# Личное наружу не выпускаем: проверяем ещё раз, прямо перед заливкой.
$leak = git ls-files | Select-String -Pattern 'recordings/|^logs/|^state/|HANDOVER|eval-result|transcripts\.txt|\.wav$|^glossary\.txt$|^fixes\.tsv$|^mywords\.txt$'
if ($leak) {
    Write-Error "СТОП: в коммит попало личное:`n$($leak -join "`n")"
    exit 1
}
"проверка на личные данные: чисто ($(git ls-files | Measure-Object -Line | Select-Object -ExpandProperty Lines) файлов)"

$body = @{
    name        = $Name
    description = "Local push-to-talk dictation for Windows: faster-whisper + a locally constrained LLM corrector. No internet, no subscription."
    private     = [bool]$Private
    has_issues  = $true
    has_wiki    = $false
    auto_init   = $false
} | ConvertTo-Json

try {
    $repo = Invoke-RestMethod -Uri "https://api.github.com/user/repos" -Method Post -Headers $h -Body $body -ContentType "application/json"
} catch {
    if ($_.ErrorDetails.Message -match "already exists") {
        $repo = Invoke-RestMethod -Uri "https://api.github.com/repos/$me/$Name" -Headers $h
        "репозиторий уже был, заливаю в него"
    } else {
        Write-Error "не смог создать: $($_.ErrorDetails.Message)"
        exit 1
    }
}

git remote remove origin 2>$null
git remote add origin $repo.clone_url
git push -u origin main

""
"готово: $($repo.html_url)"
"видимость: $(if ($repo.private) { 'закрытый' } else { 'ПУБЛИЧНЫЙ' })"
