# WattNot one-command kit for Windows (PowerShell 5.1+ or pwsh). Same commands as ./start.sh:
#   .\start.cmd [replay|check|keys|live|baseten|modal|gpu]
# Docker is not needed. No admin rights needed beyond winget. Key values are never printed.
param([string]$Mode = "replay")
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$Guide = "GETTING-STARTED.md#troubleshooting"
$IsWin = ($env:OS -eq "Windows_NT")
$Py = if ($IsWin) { Join-Path $PSScriptRoot ".venv\Scripts\python.exe" } else { Join-Path $PSScriptRoot ".venv/bin/python" }
$WnExe = if ($IsWin) { Join-Path $PSScriptRoot ".venv\Scripts\wattnot.exe" } else { Join-Path $PSScriptRoot ".venv/bin/wattnot" }
$script:Missing = @(); $script:BasePy = $null

function Say($t) { Write-Host ""; Write-Host "== $t" }
function Fail($code, $msg, $exit = 1) { Write-Host ""; Write-Host "ERROR ${code}: $msg"; Write-Host "  Fix: see $Guide (entry $code)"; exit $exit }
function Row($a, $b, $c) { Write-Host ("  {0,-26} {1,-8} {2}" -f $a, $b, $c) }
function IsSet($k) { -not [string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable($k)) }

function Load-Env {
  if (-not (Test-Path .env)) { return }
  foreach ($line in Get-Content .env) {
    if ($line -match '^([A-Z_][A-Z0-9_]*)=(.*)$') {
      $k = $Matches[1]; $v = ($Matches[2] -replace '\s+#.*$', '').Trim()
      if ($v -eq '' -or $v -like 'your-*-here') { continue }
      if (-not (IsSet $k)) { [Environment]::SetEnvironmentVariable($k, $v) }
    }
  }
}

function Find-Python {
  $cands = @()
  if (Get-Command py -ErrorAction SilentlyContinue) { foreach ($v in "3.13","3.12","3.11") { $cands += ,@("py", "-$v") } }
  foreach ($n in "python3.13","python3.12","python3.11","python3","python") { if (Get-Command $n -ErrorAction SilentlyContinue) { $cands += ,@($n) } }
  foreach ($c in $cands) {
    $exe = $c[0]; $rest = @($c | Select-Object -Skip 1)
    & $exe @rest -c "import sys; raise SystemExit(sys.version_info[:2] < (3, 11))" 2>$null
    if ($LASTEXITCODE -eq 0) { return ,$c }
  }
  return $null
}
function Run-Base { $exe = $script:BasePy[0]; $rest = @($script:BasePy | Select-Object -Skip 1); & $exe @rest @args }

function Preflight {
  Say "1/5 Preflight: what this machine has"
  Row "CHECK" "STATUS" "DETAIL"; Row "-----" "------" "------"
  $script:Missing = @()
  $script:BasePy = Find-Python
  if ($script:BasePy) { Row "Python >= 3.11" "OK" ((Run-Base -c "import sys; print(sys.version.split()[0])") -join ' ') } else { Row "Python >= 3.11" "MISSING" ""; $script:Missing += "python" }
  if ($script:BasePy) { Run-Base -c "import venv, ensurepip" 2>$null; if ($LASTEXITCODE -eq 0) { Row "pip / venv" "OK" "" } else { Row "pip / venv" "MISSING" ""; $script:Missing += "venv" } }
  if (Get-Command git -ErrorAction SilentlyContinue) { Row "git" "OK" "" } else { Row "git" "MISSING" ""; $script:Missing += "git" }
  if (Get-Command curl -ErrorAction SilentlyContinue) { Row "curl" "OK" "" } else { Row "curl" "MISSING" "optional on Windows" }
  $drive = Get-PSDrive -Name ((Get-Location).Drive.Name) -ErrorAction SilentlyContinue
  $free = if ($drive) { [int]($drive.Free / 1GB) } else { 99 }
  if ($free -ge 2) { Row "free disk" "OK" "$free GB (need 2)" } else { Row "free disk" "LOW" "$free GB"; $script:Missing += "disk" }
  $port = if ($env:WATTNOT_PORT) { [int]$env:WATTNOT_PORT } else { 8765 }
  $busy = $false; try { $c = New-Object Net.Sockets.TcpClient; $c.Connect("127.0.0.1", $port); $busy = $true; $c.Close() } catch {}
  if ($busy) { Row "port $port" "BUSY" "another free port will be picked" } else { Row "port $port" "OK" "free" }
  $installed = $false; if (Test-Path $Py) { & $Py -c "import wattnot" 2>$null; $installed = ($LASTEXITCODE -eq 0) }
  if ($installed) { Row "PyPI network" "SKIP" "already installed" }
  else { try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 8 https://pypi.org/simple/pip/ | Out-Null; Row "PyPI network" "OK" "" } catch { Row "PyPI network" "FAIL" "pypi.org not reachable"; $script:Missing += "network" } }
  Row "Docker" "N/A" "not needed"
}

function Install-System {
  if ($script:Missing -contains "disk") { Fail E05 "less than 2 GB free disk" }
  if ($script:Missing -contains "network") { Fail E06 "cannot reach pypi.org" }
  $need = @($script:Missing | Where-Object { $_ -in "python","venv","git" })
  if ($need.Count -eq 0) { return }
  Say "2/5 Install: missing $($need -join ', ')"
  if (-not $IsWin) { Fail E01 "missing $($need -join ', '); on Mac/Linux use ./start.sh" }
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { Fail E03 "winget is missing (install 'App Installer' from the Microsoft Store)" }
  $ans = if ($env:WATTNOT_YES -eq "1") { "y" } else { Read-Host "  Run: winget install Python.Python.3.12 Git.Git ? [y/N]" }
  if ($ans -notmatch '^[Yy]$') { Fail E01 "missing $($need -join ', '); install declined" }
  if ($need -contains "python" -or $need -contains "venv") { winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements }
  if ($need -contains "git") { winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements }
  Write-Host "  Installed. Open a NEW PowerShell window so PATH updates, then run .\start.cmd again."
  exit 0
}

function Install-WattNot {
  if (Test-Path $Py) { & $Py -c "import wattnot" 2>$null; if ($LASTEXITCODE -eq 0) { Write-Host "  WattNot already installed in .venv."; return } }
  Say "2/5 Install: .venv + WattNot from requirements-lock.txt (about a minute)"
  Run-Base -m venv .venv
  & $Py -m pip install -q --upgrade pip
  & $Py -m pip install -q -r requirements-lock.txt; if ($LASTEXITCODE -ne 0) { Fail E07 "pip install of requirements-lock.txt failed" }
  & $Py -m pip install -q --no-deps -e .; if ($LASTEXITCODE -ne 0) { Fail E07 "pip install of WattNot failed" }
}

function Compat {
  Say "3/5 Compatibility check"
  Row "OS" "OK" ([Runtime.InteropServices.RuntimeInformation]::OSDescription)
  Row "CPU arch" "OK" ([Runtime.InteropServices.RuntimeInformation]::OSArchitecture)
  Row "Python" "OK" ((& $Py -V 2>&1) -join ' ')
  & $Py -c "import wattnot" 2>$null; if ($LASTEXITCODE -ne 0) { Fail E08 "import wattnot failed in .venv" }; Row "import wattnot" "OK" ""
  $gpu = "none (fine: only gpu mode needs one)"
  if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) { $l = (& nvidia-smi -L 2>$null | Select-Object -First 1); if ($l) { $gpu = $l } }
  Row "NVIDIA GPU" "INFO" $gpu
  Write-Host "  Smoke test (no GPU, no keys, about a minute)..."
  & $Py -m wattnot.judge smoke *> (Join-Path ([IO.Path]::GetTempPath()) "wattnot-smoke.log")
  if ($LASTEXITCODE -ne 0) { Fail E09 "smoke test failed" }
  Write-Host "SMOKE PASS"
}
function Prepare { Load-Env; Preflight; Install-System; Install-WattNot; Compat }

function Next-Steps {
  Write-Host ""; Write-Host "== Next steps"
  Write-Host "  Verify a signed receipt:  $WnExe verify receipts/h100-1104/rcpt_20261104-032655-text.json"
  Write-Host "  Add your own keys:        .\start.cmd keys"
  Write-Host "  Live modes:               .\start.cmd live | baseten | modal | gpu"
  Write-Host "  Full guide:               GETTING-STARTED.md"
}

function Replay {
  Prepare
  $start = if ($env:WATTNOT_PORT) { $env:WATTNOT_PORT } else { "8765" }
  $port = (& $Py -c "from wattnot.demo import find_free_port; print(find_free_port($start))").Trim()
  $url = "http://127.0.0.1:$port/"
  Say "4/5 Start: dashboard on the recorded H100 run (REPLAY of MEASURED numbers)"
  $log = Join-Path ([IO.Path]::GetTempPath()) "wattnot-dash.log"
  $p = Start-Process -FilePath $Py -ArgumentList @("-m","wattnot.dash","--replay-only","--events","replays/h100-max-1103/events.jsonl","--port",$port) -RedirectStandardOutput $log -RedirectStandardError "$log.err" -PassThru -NoNewWindow
  $ok = $false
  for ($i = 0; $i -lt 60; $i++) {
    try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "${url}api/dash/health" | Out-Null; $ok = $true; break } catch {}
    if ($p.HasExited) { Fail E10 "dashboard exited during start (log: $log.err)" }
    Start-Sleep 1
  }
  if (-not $ok) { Stop-Process $p -ErrorAction SilentlyContinue; Fail E10 "dashboard health check timed out" }
  Write-Host "  Healthy: ${url}api/dash/health"
  Say "5/5 Open: $url  (Ctrl+C to stop)"
  if ($env:WATTNOT_NO_BROWSER -ne "1") { try { Start-Process $url } catch { Write-Host "  Open $url in your browser." } }
  Next-Steps
  try { Wait-Process -Id $p.Id } finally { Stop-Process -Id $p.Id -ErrorAction SilentlyContinue }
}

$Keys = [ordered]@{ META_API_KEY="live"; BASETEN_METRICS_KEY="baseten"; WATTNOT_MODAL_URL="modal"; MODAL_PROXY_TOKEN_ID="modal"; MODAL_PROXY_TOKEN_SECRET="modal"; WATTNOT_VLLM_URL="gpu"; WATTNOT_MAX_SPEND="live (USD cap)" }
function Key-Table { Row "KEY" "STATUS" "NEEDED FOR"; Row "---" "------" "----------"; foreach ($k in $Keys.Keys) { Row $k ($(if (IsSet $k) {"set"} else {"not set"})) $Keys[$k] }; Row "(none)" "-" "replay, check" }
function Protect-Env {
  if ($IsWin) { icacls .env /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null } else { chmod 600 .env }
}
function Keys {
  if (-not (Test-Path .env)) { Copy-Item .env.example .env; Write-Host "Created .env from .env.example (gitignored)." }
  Protect-Env; Load-Env
  Say "Keys (values are never shown)"; Key-Table
  if ([Console]::IsInputRedirected) { Write-Host ""; Write-Host "Not a terminal: edit .env by hand."; return }
  Write-Host ""; Write-Host "Press Enter to skip a key. Input is hidden."
  foreach ($k in $Keys.Keys) {
    $s = Read-Host "  $k ($($Keys[$k]))" -AsSecureString
    $v = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))
    if ($v) {
      $lines = @(Get-Content .env | Where-Object { $_ -notmatch "^$k=" }) + "$k=$v"
      Set-Content -Path .env -Value $lines; [Environment]::SetEnvironmentVariable($k, $v)
    }
    $v = $null
  }
  Protect-Env; Say "Saved to .env (owner-only)"; Key-Table
}
function Need($m, [string[]]$vars) { $miss = @($vars | Where-Object { -not (IsSet $_) }); if ($miss.Count) { Fail E11 "$m mode needs $($miss -join ' ') (run .\start.cmd keys)" 2 } }
$Spend = { if ($env:WATTNOT_MAX_SPEND) { $env:WATTNOT_MAX_SPEND } else { "0.50" } }

switch ($Mode) {
  { $_ -in "replay","demo" } { Replay; break }
  "check" { Prepare; Next-Steps; break }
  "keys" { Keys; break }
  "live" { Load-Env; Need "live" @("META_API_KEY"); Prepare; Say "Live token savers on Meta models (cap $(& $Spend) USD)"; & .\run-local.ps1 savers; exit $LASTEXITCODE }
  "baseten" { Load-Env; Need "baseten" @("BASETEN_METRICS_KEY"); Prepare; & $WnExe cloud tune --live --budget-usd (& $Spend); exit $LASTEXITCODE }
  "modal" { Load-Env; if (-not ((IsSet "WATTNOT_MODAL_URL") -or (IsSet "MODAL_ENDPOINT_URL"))) { Fail E11 "modal mode needs WATTNOT_MODAL_URL (run .\start.cmd keys)" 2 }
            Need "modal" @("MODAL_PROXY_TOKEN_ID","MODAL_PROXY_TOKEN_SECRET"); Prepare; & $WnExe cloud tune --provider modal --live --budget-usd (& $Spend); exit $LASTEXITCODE }
  "gpu" { Load-Env; Prepare
          $has = $false; if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) { & nvidia-smi -L 2>$null | Out-Null; $has = ($LASTEXITCODE -eq 0) }
          if (-not $has) { Fail E12 "gpu mode needs an NVIDIA GPU with a working nvidia-smi; none found here" 3 }
          Fail E14 "power caps run on Linux only (root + nvidia-smi -pl). Use ./start.sh gpu on a Linux GPU host." 3 }
  default { Get-Content $PSCommandPath -TotalCount 3; Fail E00 "unknown command '$Mode'" 2 }
}
