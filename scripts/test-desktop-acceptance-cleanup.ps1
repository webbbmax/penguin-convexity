$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $PSScriptRoot "desktop-acceptance-guard.ps1")

$baseline = Get-DesktopAcceptanceState
$guard = New-DesktopAcceptanceGuard -Name "desktop-cleanup-self-test" -ProjectRoot $projectRoot
$testProcess = $null
$portProcess = $null
$cleanup = $null
$simulatedFailureObserved = $false
$portScenario = "baseline_preserved"

try {
  $testProcess = Start-Process -FilePath "powershell.exe" -ArgumentList @(
    "-NoProfile", "-Command", "Start-Sleep -Seconds 60"
  ) -WindowStyle Hidden -PassThru
  Register-DesktopAcceptanceProcess -Guard $guard -Process $testProcess -Role "self_test_process"

  if (@($baseline.Port8766Owners).Count -eq 0 -and @($baseline.DesktopHosts).Count -eq 0) {
    $python = (Get-Command python.exe -ErrorAction Stop).Source
    $portProcess = Start-Process -FilePath $python -ArgumentList @(
      "-m", "http.server", "8766", "--bind", "127.0.0.1"
    ) -WindowStyle Hidden -PassThru
    Register-DesktopAcceptanceProcess -Guard $guard -Process $portProcess -Role "self_test_8766"
    $deadline = (Get-Date).AddSeconds(5)
    do {
      Start-Sleep -Milliseconds 100
      $listener = Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue
    } while ($null -eq $listener -and (Get-Date) -lt $deadline)
    if ($null -eq $listener) { throw "Self-test process did not listen on port 8766." }
    $portScenario = "test_listener_removed"
  }

  try {
    throw "intentional acceptance failure"
  } catch {
    if ($_.Exception.Message -ne "intentional acceptance failure") { throw }
    $simulatedFailureObserved = $true
  }
} finally {
  $cleanup = Complete-DesktopAcceptanceGuard -Guard $guard
}

if (-not $simulatedFailureObserved) { throw "The intentional failure path was not exercised." }
if ($null -ne $testProcess -and (Get-Process -Id $testProcess.Id -ErrorAction SilentlyContinue)) {
  throw "The tracked self-test process remained after cleanup."
}
if ($null -ne $portProcess -and (Get-Process -Id $portProcess.Id -ErrorAction SilentlyContinue)) {
  throw "The tracked port 8766 process remained after cleanup."
}

$same = @(Compare-DesktopAcceptanceState -Expected $baseline -Actual (Get-DesktopAcceptanceState))
if ($same.Count -ne 0) { throw "The self-test baseline was not restored: $($same -join '; ')" }

$fake = [PSCustomObject]@{ Identity = "999|fake" }
$empty = [PSCustomObject]@{ DesktopHosts = @(); WebViews = @(); Port8766Owners = @() }
foreach ($group in @("DesktopHosts", "WebViews", "Port8766Owners")) {
  $changed = [PSCustomObject]@{ DesktopHosts = @(); WebViews = @(); Port8766Owners = @() }
  $changed.$group = @($fake)
  if (@(Compare-DesktopAcceptanceState -Expected $empty -Actual $changed).Count -ne 1) {
    throw "State comparison did not detect a $group leak."
  }
}

[PSCustomObject]@{
  status = "passed"
  intentionalFailureCleanup = $true
  desktopStateCompared = $true
  webView2StateCompared = $true
  port8766Scenario = $portScenario
  trackedProcessesStopped = $cleanup.trackedProcessesStopped
} | ConvertTo-Json -Depth 3
