param(
  [switch]$Install,
  [switch]$RunOnce,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runner = Join-Path $projectRoot "scripts\run_c2_1_update.py"
$hiddenRunner = Join-Path $projectRoot "scripts\run-c2-1-update-hidden.vbs"
$taskName = "PenguinConvexity-C1.8-Scheduler"
$python = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command py.exe -ErrorAction SilentlyContinue }
if (-not $python) { throw "Python was not found for the independent convexity scheduler." }
$wscript = Join-Path $env:SystemRoot "System32\wscript.exe"
if (-not (Test-Path -LiteralPath $wscript)) { throw "Windows Script Host was not found for hidden scheduler startup." }
if (-not (Test-Path -LiteralPath $hiddenRunner)) { throw "The C2.1 hidden scheduler runner is missing." }

if ($RunOnce) {
  $runArguments = @($runner, "--trigger", "automatic")
  if ($python.Name -eq "py.exe") { $runArguments = @("-3") + $runArguments }
  & $python.Source @runArguments
  exit $LASTEXITCODE
}

if (-not $Install) {
  Write-Output "Usage: -Install or -RunOnce. Rollback uses install-c1.8-scheduler.ps1 -Install."
  exit 0
}

$taskCommand = '"{0}" "{1}" "{2}"' -f $wscript, $hiddenRunner, $python.Source
if ($DryRun) {
  Write-Output "Dry run: would migrate $taskName to the C2.1 hidden runner with a 15-minute recovery wake-up."
  exit 0
}

& schtasks.exe /Create /TN $taskName /TR $taskCommand /SC MINUTE /MO 15 /ST 00:00 /F /RL LIMITED | Out-Null
if ($LASTEXITCODE -ne 0) {
  throw "Could not migrate the existing convexity scheduler. The C1.8 installer remains the rollback path."
}
$registeredTask = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
$registeredTask.Settings.DisallowStartIfOnBatteries = $false
$registeredTask.Settings.StopIfGoingOnBatteries = $false
$registeredTask.Settings.StartWhenAvailable = $true
$registeredTask.Settings.MultipleInstances = "IgnoreNew"
$registeredTask.Settings.ExecutionTimeLimit = "PT12H"
Set-ScheduledTask -InputObject $registeredTask | Out-Null
Write-Output "Migrated ${taskName}: the same hidden task now checks C2.1 due or recoverable work every 15 minutes."
