param(
  [string]$Executable = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($Executable)) {
  $Executable = Join-Path $projectRoot "runtime\c2.3-acceptance\publish\PenguinConvexity.Desktop.exe"
}
$fixtureScript = Join-Path $projectRoot "scripts\c2_3_fault_fixture_server.py"
$desktopLog = Join-Path $projectRoot "runtime\logs\c2.3-desktop.log"
$candidateStatusPath = Join-Path $projectRoot "runtime\c2.2\candidate-production\status.json"
$resultPath = Join-Path $projectRoot "runtime\c2.3-acceptance\lifecycle-result.json"
$python = "C:\Python312\python.exe"
. (Join-Path $PSScriptRoot "desktop-acceptance-guard.ps1")

function Get-ListenerPid {
  $connection = Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($null -eq $connection) { return 0 }
  return [int]$connection.OwningProcess
}

function Wait-Listener([bool]$Expected, [int]$TimeoutSeconds = 10) {
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  do {
    $present = (Get-ListenerPid) -ne 0
    if ($present -eq $Expected) { return }
    Start-Sleep -Milliseconds 100
  } while ((Get-Date) -lt $deadline)
  throw "Port 8766 did not reach expected listener state: $Expected"
}

function Wait-ProductReady([DateTimeOffset]$After, [int]$TimeoutSeconds = 30) {
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  do {
    if (Test-Path -LiteralPath $desktopLog) {
      foreach ($line in (Get-Content -LiteralPath $desktopLog -Tail 200 -Encoding UTF8)) {
        $parts = $line -split "\t", 4
        if ($parts.Count -ge 3 -and $parts[2] -eq "product_ready") {
          try {
            $loggedAt = [DateTimeOffset]::Parse($parts[0])
            if ($loggedAt -ge $After) { return $loggedAt }
          } catch { }
        }
      }
    }
    Start-Sleep -Milliseconds 150
  } while ((Get-Date) -lt $deadline)
  throw "Product page did not become ready within $TimeoutSeconds seconds."
}

function Wait-Fault([DateTimeOffset]$After, [string]$Kind, [int]$TimeoutSeconds = 10) {
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  do {
    if (Test-Path -LiteralPath $desktopLog) {
      foreach ($line in (Get-Content -LiteralPath $desktopLog -Tail 200 -Encoding UTF8)) {
        $parts = $line -split "\t", 4
        if ($parts.Count -ge 4 -and $parts[2] -eq "user_fault" -and $parts[3] -match "kind=$Kind") {
          try {
            if ([DateTimeOffset]::Parse($parts[0]) -ge $After) { return }
          } catch { }
        }
      }
    }
    Start-Sleep -Milliseconds 100
  } while ((Get-Date) -lt $deadline)
  throw "Expected user fault was not logged: $Kind"
}

function Close-Host([System.Diagnostics.Process]$Process) {
  $Process.Refresh()
  if ($Process.HasExited) { return }
  $Process.CloseMainWindow() | Out-Null
  if (-not $Process.WaitForExit(5000)) {
    throw "C2.3 host did not close normally."
  }
}

function Start-HiddenProcess([string]$FilePath, [string[]]$Arguments) {
  return Start-Process -FilePath $FilePath -ArgumentList $Arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
}

$candidateBefore = Get-Content -LiteralPath $candidateStatusPath -Raw -Encoding UTF8 | ConvertFrom-Json
$candidatePid = $null
$candidateStart = $null
$candidateLock = Join-Path $projectRoot "runtime\c2.2\candidate-production\worker.lock"
if (Test-Path -LiteralPath $candidateLock) {
  $candidatePidText = (Get-Content -LiteralPath $candidateLock -Raw -Encoding ASCII).Trim()
  if ($candidatePidText -match '^\d+$') {
    $candidatePid = [int]$candidatePidText
    $candidateProcess = Get-Process -Id $candidatePid -ErrorAction Stop
    $candidateStart = $candidateProcess.StartTime
  }
}
$edgeVisible = @(Get-Process msedge -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -ExpandProperty Id)
$chromeVisible = @(Get-Process chrome -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -ExpandProperty Id)
$originalServicePid = Get-ListenerPid
if ($originalServicePid -ne 0) { throw "Independent lifecycle test requires port 8766 to be idle and will not replace a pre-existing service." }
if (@(Get-Process PenguinConvexity.Desktop -ErrorAction SilentlyContinue).Count -ne 0) {
  throw "Independent lifecycle test requires no pre-existing Penguin desktop and will not close a user-owned window."
}
$cleanupGuard = New-DesktopAcceptanceGuard -Name "c2.3-independent-lifecycle" -ProjectRoot $projectRoot
$cleanupResult = $null

$results = [ordered]@{
  schemaVersion = "c2.3-independent-lifecycle-v2"
  startedAt = (Get-Date).ToString("o")
  originalServicePid = $originalServicePid
  candidatePid = $candidatePid
  candidateRunId = $candidateBefore.currentRun.run_id
}
$testHostIds = [System.Collections.Generic.List[int]]::new()
$fixtureIds = [System.Collections.Generic.List[int]]::new()

try {
  # The cold-start contract means "the page service is stopped", not the first
  # execution of a newly copied unsigned binary while Windows scans that file.
  $prewarmHost = Start-Process -FilePath $Executable -WorkingDirectory $projectRoot -PassThru
  $testHostIds.Add($prewarmHost.Id)
  Register-DesktopAcceptanceProcess -Guard $cleanupGuard -Process $prewarmHost -Role "prewarm_host"
  $prewarmStarted = [DateTimeOffset]::Now
  Wait-ProductReady -After $prewarmStarted | Out-Null
  Close-Host $prewarmHost
  Wait-Listener -Expected $false

  $coldStarted = [DateTimeOffset]::Now
  $coldHost = Start-Process -FilePath $Executable -WorkingDirectory $projectRoot -PassThru
  $testHostIds.Add($coldHost.Id)
  Register-DesktopAcceptanceProcess -Guard $cleanupGuard -Process $coldHost -Role "cold_host"
  $windowDeadline = (Get-Date).AddSeconds(3)
  do {
    Start-Sleep -Milliseconds 50
    $coldHost.Refresh()
  } while (-not $coldHost.HasExited -and $coldHost.MainWindowHandle -eq 0 -and (Get-Date) -lt $windowDeadline)
  if ($coldHost.HasExited -or $coldHost.MainWindowHandle -eq 0) { throw "Cold start did not show a native window within 3 seconds." }
  $coldWindowMs = [int]([DateTimeOffset]::Now - $coldStarted).TotalMilliseconds
  $readyAt = Wait-ProductReady -After $coldStarted
  $coldReadyMs = [int]($readyAt - $coldStarted).TotalMilliseconds
  if ($coldReadyMs -gt 30000) { throw "Cold start exceeded 30 seconds." }
  $ownedServicePid = Get-ListenerPid
  if ($ownedServicePid -eq 0) { throw "Cold start did not create an owned page service." }
  Close-Host $coldHost
  Wait-Listener -Expected $false
  $results.coldStart = [ordered]@{ status = "passed"; windowMs = $coldWindowMs; pageReadyMs = $coldReadyMs; ownedServicePid = $ownedServicePid; ownedServiceStoppedOnClose = $true }

  foreach ($fixtureMode in @("port_conflict", "identity_mismatch")) {
    $fixture = Start-HiddenProcess -FilePath $python -Arguments @($fixtureScript, "--mode", $fixtureMode, "--port", "8766")
    $fixtureIds.Add($fixture.Id)
    Register-DesktopAcceptanceProcess -Guard $cleanupGuard -Process $fixture -Role "fixture_$fixtureMode"
    Wait-Listener -Expected $true
    $fixturePid = Get-ListenerPid
    if ($fixturePid -ne $fixture.Id) { throw "Fixture listener PID mismatch for $fixtureMode." }
    $faultStarted = [DateTimeOffset]::Now
    $faultHost = Start-Process -FilePath $Executable -WorkingDirectory $projectRoot -PassThru
    $testHostIds.Add($faultHost.Id)
    Register-DesktopAcceptanceProcess -Guard $cleanupGuard -Process $faultHost -Role "fault_host_$fixtureMode"
    $expectedKind = if ($fixtureMode -eq "port_conflict") { "PortConflict" } else { "IdentityFailure" }
    Wait-Fault -After $faultStarted -Kind $expectedKind
    if (-not (Get-Process -Id $fixturePid -ErrorAction SilentlyContinue)) { throw "C2.3 stopped the unknown 8766 fixture process." }
    Close-Host $faultHost
    Stop-Process -Id $fixturePid -Force
    Wait-Listener -Expected $false
    $results[$fixtureMode] = [ordered]@{ status = "passed"; faultKind = $expectedKind; unknownProcessPreservedUntilTestCleanup = $true }
  }

  $loader = Join-Path (Split-Path -Parent $Executable) "WebView2Loader.dll"
  $loaderHold = "$loader.acceptance-hold"
  if (-not (Test-Path -LiteralPath $loader)) { throw "WebView2Loader.dll is missing before the fault test." }
  Move-Item -LiteralPath $loader -Destination $loaderHold
  try {
    $webViewStarted = [DateTimeOffset]::Now
    $webViewHost = Start-Process -FilePath $Executable -WorkingDirectory $projectRoot -PassThru
    $testHostIds.Add($webViewHost.Id)
    Register-DesktopAcceptanceProcess -Guard $cleanupGuard -Process $webViewHost -Role "webview_fault_host"
    Wait-Fault -After $webViewStarted -Kind "WebViewUnavailable"
    Close-Host $webViewHost
    $results.webViewUnavailable = [ordered]@{ status = "passed"; faultKind = "WebViewUnavailable"; runtimeUninstalled = $false; isolatedLoaderSimulation = $true }
  } finally {
    if (Test-Path -LiteralPath $loaderHold) { Move-Item -LiteralPath $loaderHold -Destination $loader -Force }
  }
} finally {
  try {
    foreach ($hostId in $testHostIds) {
      $hostProcess = Get-Process -Id $hostId -ErrorAction SilentlyContinue
      if ($hostProcess) {
        $hostProcess.CloseMainWindow() | Out-Null
        $hostProcess.WaitForExit(3000) | Out-Null
      }
    }
    foreach ($fixtureId in $fixtureIds) {
      if (Get-Process -Id $fixtureId -ErrorAction SilentlyContinue) {
        Stop-Process -Id $fixtureId -Force -ErrorAction SilentlyContinue
      }
    }
    Start-Sleep -Milliseconds 250
    $existing = Get-ListenerPid
    if ($existing -ne 0) {
      $process = Get-CimInstance Win32_Process -Filter "ProcessId=$existing"
      if ($process -and $process.CommandLine -and $process.CommandLine.IndexOf($fixtureScript, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
        Stop-Process -Id $existing -Force -ErrorAction SilentlyContinue
        Wait-Listener -Expected $false
      }
    }
  } finally {
    $cleanupResult = Complete-DesktopAcceptanceGuard -Guard $cleanupGuard
  }
}

if ((Get-ListenerPid) -ne 0) { throw "Port 8766 was not restored to its idle baseline." }
$candidateAfter = Get-Content -LiteralPath $candidateStatusPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($candidateAfter.currentRun.run_id -ne $candidateBefore.currentRun.run_id) { throw "Candidate production runId changed." }
if ($null -ne $candidatePid) {
  $candidateProcessAfter = Get-Process -Id $candidatePid -ErrorAction Stop
  if ($candidateProcessAfter.StartTime -ne $candidateStart) { throw "Candidate production process restarted." }
}
foreach ($processId in ($edgeVisible + $chromeVisible)) {
  if (-not (Get-Process -Id $processId -ErrorAction SilentlyContinue)) { throw "A pre-existing ordinary browser window process was closed." }
}

$results.restoredService = [ordered]@{ status = "passed"; state = "idle"; baseline = "idle" }
$results.backgroundIsolation = [ordered]@{ status = "passed"; candidatePid = $candidatePid; candidateRunId = $candidateAfter.currentRun.run_id; candidateState = $candidateAfter.currentRun.state; localScannedCount = $candidateAfter.localScannedCount }
$results.ordinaryBrowserVisibleProcessesPreserved = $true
$results.cleanup = $cleanupResult
$results.finishedAt = (Get-Date).ToString("o")
$results.status = "passed"
$results | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath "$resultPath.tmp" -Encoding UTF8
Move-Item -LiteralPath "$resultPath.tmp" -Destination $resultPath -Force
$results | ConvertTo-Json -Depth 8
