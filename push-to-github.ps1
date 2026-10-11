# Publishes the committed system to its GitHub repository (origin, branch main).
# Stops before pushing if anything personal would go out:
#   1. a private file is tracked: recordings, logs, state, the three word
#      lists, handover notes, benchmark transcripts;
#   2. privacy_check.py finds the home folder path, the user name, a person's
#      name from glossary.txt, or a phrase from a real take in a line that is
#      not published yet.
# Your own data goes to a private repository instead: loop/sync_private.py.
#   .\push-to-github.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$leak = git ls-files | Select-String -Pattern 'recordings/|^logs/|^state/|HANDOVER|eval-result|transcripts\.txt|\.wav$|^glossary\.txt$|^fixes\.tsv$|^mywords\.txt$'
if ($leak) {
    Write-Error "STOP: a private file is tracked:`n$($leak -join "`n")"
    exit 1
}

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
& $python privacy_check.py
if ($LASTEXITCODE -ne 0) {
    Write-Error "STOP: privacy_check.py found personal data (listed above). Nothing was pushed."
    exit 1
}

git push origin main
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
"pushed: $(git remote get-url origin)"
