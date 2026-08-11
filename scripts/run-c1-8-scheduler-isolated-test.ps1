param(
  [Parameter(Mandatory = $true)]
  [string]$TestRoot
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command py.exe -ErrorAction SilentlyContinue }
if (-not $python) { throw "Python was not found." }

$arguments = @(
  (Join-Path $projectRoot "scripts\run_c1_8_scheduler.py"),
  "--db", (Join-Path $TestRoot "convexity.db"),
  "--config", (Join-Path $TestRoot "config.json"),
  "--state", (Join-Path $TestRoot "state.json"),
  "--lock", (Join-Path $TestRoot "scheduler.lock"),
  "--dry-run",
  "--force"
)
if ($python.Name -eq "py.exe") { $arguments = @("-3") + $arguments }
& $python.Source @arguments
exit $LASTEXITCODE
