param(
  [switch]$Install,
  [switch]$Uninstall,
  [switch]$RunOnce,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$scheduler = Join-Path $projectRoot "scripts\run_c1_8_scheduler.py"
$hiddenRunner = Join-Path $projectRoot "scripts\run-c1-8-scheduler-hidden.vbs"
$taskName = "PenguinConvexity-C1.8-Scheduler"
$python = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command py.exe -ErrorAction SilentlyContinue }
if (-not $python) { throw "Python was not found for the independent convexity scheduler." }
$wscript = Join-Path $env:SystemRoot "System32\wscript.exe"
if (-not (Test-Path -LiteralPath $wscript)) { throw "Windows Script Host was not found for hidden scheduler startup." }
if (-not (Test-Path -LiteralPath $hiddenRunner)) { throw "The hidden scheduler runner is missing." }

if ($RunOnce) {
  $runArguments = @($scheduler)
  if ($python.Name -eq "py.exe") {
    $runArguments = @("-3") + $runArguments
  }
  if ($DryRun) { $runArguments += "--dry-run" }
  & $python.Source @runArguments
  exit $LASTEXITCODE
}

if ($Uninstall) {
  & schtasks.exe /Delete /TN $taskName /F 2>$null | Out-Null
  Write-Output "Removed $taskName (Penguin Research Convexity only)."
  exit 0
}

if (-not $Install) {
  Write-Output "Usage: -Install, -Uninstall, or -RunOnce."
  exit 0
}

$taskCommand = '"{0}" "{1}" "{2}"' -f $wscript, $hiddenRunner, $python.Source
if ($DryRun) {
  Write-Output "Dry run: would register $taskName as an hourly, current-user, limited, hidden-window task."
  exit 0
}
& schtasks.exe /Create /TN $taskName /TR $taskCommand /SC HOURLY /MO 1 /ST 00:00 /F /RL LIMITED | Out-Null
if ($LASTEXITCODE -ne 0) {
  throw "Could not install the project scheduler. Check the Windows Task Scheduler service and current-user task permissions."
}
$registeredTask = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
$registeredTask.Settings.DisallowStartIfOnBatteries = $false
$registeredTask.Settings.StopIfGoingOnBatteries = $false
$registeredTask.Settings.StartWhenAvailable = $true
$registeredTask.Settings.MultipleInstances = "IgnoreNew"
$registeredTask.Settings.ExecutionTimeLimit = "PT55M"
Set-ScheduledTask -InputObject $registeredTask | Out-Null
Write-Output "Enabled ${taskName}: wakes hourly while the user is signed in; daily full update follows project config."
