<#
.SYNOPSIS
  WattNot one-time setup for Windows (PowerShell).

.DESCRIPTION
  Makes a .venv and installs WattNot into it. No GPU, no network beyond PyPI,
  no Meta/Baseten/Modal key, no money spent.

  From the repo folder, in PowerShell:

      powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
      .\.venv\Scripts\Activate.ps1
      wattnot demo

  The last line opens the dashboard on the bundled replay.

  Exit codes: 0 ok, 1 no suitable Python found, 2 install failed.
#>
$ErrorActionPreference = 'Stop'
# Run from the repo root regardless of where the script was invoked.
Set-Location (Join-Path $PSScriptRoot '..')

function Die($msg, $code = 2) {
    Write-Error "setup.ps1: $msg"
    exit $code
}

# Find the newest Python >= 3.11. The Windows launcher (`py`) is preferred when
# present; otherwise fall back to `python`/`python3` on the PATH.
function Find-Python {
    $check = 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)'
    foreach ($cmd in @(
        @('py', '-3.13'), @('py', '-3.12'), @('py', '-3.11'), @('py', '-3'),
        @('python'), @('python3')
    )) {
        $exe = $cmd[0]
        if (Get-Command $exe -ErrorAction SilentlyContinue) {
            $rest = @($cmd[1..($cmd.Length - 1)])
            & $exe @rest -c $check 2>$null
            if ($LASTEXITCODE -eq 0) {
                return , ($cmd)
            }
        }
    }
    return $null
}

$py = Find-Python
if (-not $py) {
    Die @"
could not find Python 3.11 or newer.
  Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH"),
  open a new PowerShell window, and run this script again.
"@ 1
}

$pyExe = $py[0]
$pyArgs = @($py[1..($py.Length - 1)])
$ver = (& $pyExe @pyArgs -V) 2>&1
Write-Host "Using $ver"

if (-not (Test-Path '.venv')) {
    Write-Host 'Creating a virtual environment in .venv ...'
    & $pyExe @pyArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { Die 'could not create .venv.' }
}
else {
    Write-Host 'Reusing the existing .venv'
}

$vpy = Join-Path '.venv' 'Scripts\python.exe'
if (-not (Test-Path $vpy)) { Die 'the virtual environment looks broken; delete .venv and run this script again.' }

Write-Host 'Upgrading pip ...'
& $vpy -m pip install --upgrade pip | Out-Null
if ($LASTEXITCODE -ne 0) { Die 'could not upgrade pip.' }

Write-Host 'Installing WattNot (pip install -e .) ...'
& $vpy -m pip install -e .
if ($LASTEXITCODE -ne 0) { Die 'install failed; see the error above and the README troubleshooting section.' }

Write-Host ''
Write-Host 'Done. Next, in this terminal:'
Write-Host '    .\.venv\Scripts\Activate.ps1'
Write-Host '    wattnot demo'
Write-Host ''
Write-Host 'That opens the dashboard on the bundled replay - no GPU, no network, no keys.'
