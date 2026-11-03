# WattNot for judges on Windows (PowerShell). Same modes as ./run-local.sh:
#   .\run-local.ps1            # doctor, then the best mode this machine supports
#   .\run-local.ps1 smoke      # under a minute, no GPU, no key, no spend
#   .\run-local.ps1 savers     # live token savers on the Meta API (needs META_API_KEY in .env)
#   .\run-local.ps1 replay     # dashboard on our recorded H100 run (MEASURED)
#   .\run-local.ps1 doctor | sim | gpu
# If scripts are blocked, run once:  Set-ExecutionPolicy -Scope Process Bypass
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$ok = $false
if (Test-Path $py) { & $py -c "import wattnot" 2>$null; $ok = ($LASTEXITCODE -eq 0) }
if (-not $ok) {
  $base = $null
  foreach ($v in @("3.13", "3.12", "3.11")) {
    & py "-$v" -c "import sys" 2>$null
    if ($LASTEXITCODE -eq 0) { $base = @("py", "-$v"); break }
  }
  if (-not $base) {
    Write-Host "Python 3.11, 3.12 or 3.13 not found. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then open a new PowerShell."
    exit 1
  }
  Write-Host "First run: installing WattNot into .venv (about a minute)..."
  & $base[0] $base[1] -m venv .venv
  & $py -m pip install -q --upgrade pip
  & $py -m pip install -q -r requirements-lock.txt
  if ($LASTEXITCODE -ne 0) { Write-Host "note: pinned versions did not install here; using the ranges in pyproject.toml" }
  & $py -m pip install -q -e .
  if ($LASTEXITCODE -ne 0) { Write-Host "install failed; see the pip output above"; exit 1 }
}
& $py -m wattnot.judge @args
exit $LASTEXITCODE
