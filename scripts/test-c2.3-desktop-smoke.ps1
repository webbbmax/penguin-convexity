param(
  [string]$Executable = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($Executable)) {
  $Executable = Join-Path $projectRoot "desktop-host\publish\win-x64\PenguinConvexity.Desktop.exe"
}
if (-not (Test-Path -LiteralPath $Executable)) { throw "C2.3 executable is missing: $Executable" }

$candidateBefore = Get-Content (Join-Path $projectRoot "runtime\c2.2\candidate-production\status.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$candidateLock = Join-Path $projectRoot "runtime\c2.2\candidate-production\worker.lock"
$candidatePid = $null
if (Test-Path -LiteralPath $candidateLock) {
  $candidatePidText = (Get-Content -LiteralPath $candidateLock -Raw -Encoding ASCII).Trim()
  if ($candidatePidText -match '^\d+$') {
    $candidatePid = [int]$candidatePidText
    if (-not (Get-Process -Id $candidatePid -ErrorAction SilentlyContinue)) {
      throw "Candidate production lock points to a stopped process: $candidatePid"
    }
  }
}
$edgeBefore = @(Get-Process msedge -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id | Sort-Object)
$chromeBefore = @(Get-Process chrome -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id | Sort-Object)
$edgeVisibleBefore = @(Get-Process msedge -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -ExpandProperty Id)
$chromeVisibleBefore = @(Get-Process chrome -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -ExpandProperty Id)
$serviceBefore = Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
$desktopLog = Join-Path $projectRoot "runtime\logs\c2.3-desktop.log"
$existingHosts = @(Get-Process PenguinConvexity.Desktop -ErrorAction SilentlyContinue)
if ($existingHosts.Count -gt 1) { throw "Expected at most one C2.3 desktop host before launch, found $($existingHosts.Count)." }
if ($existingHosts.Count -eq 1) {
  $existing = $existingHosts[0]
  if ($existing.MainWindowHandle -eq 0) { throw "Existing C2.3 desktop host has no native window." }
  if ($null -eq $serviceBefore) { throw "Existing C2.3 desktop host has no usable local service." }
  $activationStartedAt = Get-Date
  $activation = Start-Process -FilePath $Executable -WorkingDirectory $projectRoot -PassThru
  if (-not $activation.WaitForExit(2000)) { throw "Second launch did not exit after activating the existing window." }
  $activationMs = [int]((Get-Date) - $activationStartedAt).TotalMilliseconds
  if ($activation.ExitCode -ne 0) { throw "Existing-window activation failed with code $($activation.ExitCode)." }
  $matchingHosts = @(Get-Process PenguinConvexity.Desktop -ErrorAction SilentlyContinue)
  if ($matchingHosts.Count -ne 1 -or $matchingHosts[0].Id -ne $existing.Id) { throw "Existing desktop single-instance ownership changed." }
  $page = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8766/desktop/index.html" -TimeoutSec 10
  $productPage = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8766/candidate-pool.html" -TimeoutSec 10
  if ($page.StatusCode -ne 200 -or $productPage.StatusCode -ne 200 -or $page.Content -notmatch "candidate-pool.html") { throw "Existing desktop product page is not usable." }
  foreach ($processId in ($edgeVisibleBefore + $chromeVisibleBefore)) {
    if (-not (Get-Process -Id $processId -ErrorAction SilentlyContinue)) { throw "A pre-existing ordinary browser window was closed during activation." }
  }
  $candidateAfter = Get-Content (Join-Path $projectRoot "runtime\c2.2\candidate-production\status.json") -Raw -Encoding UTF8 | ConvertFrom-Json
  if ($candidateBefore.currentRun.run_id -ne $candidateAfter.currentRun.run_id) { throw "Candidate production run changed during desktop activation smoke test." }
  if ($null -ne $candidatePid -and -not (Get-Process -Id $candidatePid -ErrorAction SilentlyContinue)) { throw "Candidate production process stopped during desktop activation smoke test." }
  $serviceAfter = Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction Stop | Select-Object -First 1
  if ($serviceAfter.OwningProcess -ne $serviceBefore.OwningProcess) { throw "External local service ownership changed during desktop activation smoke test." }
  [PSCustomObject]@{
    status = "passed"
    mode = "existing_instance_activation"
    existingHostPid = $existing.Id
    duplicateActivationMs = $activationMs
    servicePidUnchanged = $serviceAfter.OwningProcess
    candidatePidUnchanged = if ($null -eq $candidatePid) { "idle" } else { $candidatePid }
    candidateRunIdUnchanged = $candidateAfter.currentRun.run_id
    productPageStatus = $page.StatusCode
    opportunityPageStatus = $productPage.StatusCode
  } | ConvertTo-Json
  exit 0
}

$startedAt = Get-Date
$primary = Start-Process -FilePath $Executable -WorkingDirectory $projectRoot -PassThru
$deadline = (Get-Date).AddSeconds(30)
do {
  Start-Sleep -Milliseconds 200
  $primary.Refresh()
} while (-not $primary.HasExited -and $primary.MainWindowHandle -eq 0 -and (Get-Date) -lt $deadline)
if ($primary.HasExited) { throw "C2.3 desktop exited before showing a window." }
if ($primary.MainWindowHandle -eq 0) { throw "C2.3 desktop did not show a native window within 30 seconds." }
$windowShownMs = [int]((Get-Date) - $startedAt).TotalMilliseconds
if ($windowShownMs -gt 3000) { throw "C2.3 native window exceeded the 3 second target: $windowShownMs ms." }

$pageDeadline = (Get-Date).AddSeconds(30)
$pageReadyAt = $null
do {
  Start-Sleep -Milliseconds 150
  if (Test-Path -LiteralPath $desktopLog) {
    $readyLines = Get-Content -LiteralPath $desktopLog -Tail 120 -Encoding UTF8 |
      Where-Object { $_ -match "\tINFO\tproduct_ready\t" }
    foreach ($line in $readyLines) {
      $parts = $line -split "\t", 4
      if ($parts.Count -ge 4) {
        try {
          $loggedAt = [DateTimeOffset]::Parse($parts[0])
          if ($loggedAt -ge [DateTimeOffset]$startedAt) { $pageReadyAt = $loggedAt }
        } catch { }
      }
    }
  }
} while ($null -eq $pageReadyAt -and (Get-Date) -lt $pageDeadline)
if ($null -eq $pageReadyAt) { throw "C2.3 product page did not become usable within 30 seconds." }
$pageReadyMs = [int]($pageReadyAt - [DateTimeOffset]$startedAt).TotalMilliseconds
if ($pageReadyMs -gt 5000) { throw "C2.3 hot product page exceeded the 5 second target: $pageReadyMs ms." }

$serviceDuring = Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction Stop | Select-Object -First 1
if ($null -ne $serviceBefore -and $serviceDuring.OwningProcess -ne $serviceBefore.OwningProcess) {
  throw "External local service ownership changed during desktop startup."
}
if ($null -eq $serviceBefore) {
  $serviceProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($serviceDuring.OwningProcess)"
  if ($null -eq $serviceProcess -or $serviceProcess.ParentProcessId -ne $primary.Id) {
    throw "Desktop-started local service is not owned by the current desktop session."
  }
}
$page = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8766/desktop/index.html" -TimeoutSec 10
$productPage = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8766/candidate-pool.html" -TimeoutSec 10
if ($page.StatusCode -ne 200 -or $productPage.StatusCode -ne 200 -or $page.Content -notmatch "candidate-pool.html") {
  throw "Desktop product page is not usable."
}

$secondaryStartedAt = Get-Date
$secondary = Start-Process -FilePath $Executable -WorkingDirectory $projectRoot -PassThru
if (-not $secondary.WaitForExit(2000)) {
  throw "Second launch did not exit after activating the existing window."
}
$secondaryMs = [int]((Get-Date) - $secondaryStartedAt).TotalMilliseconds
if ($secondary.ExitCode -ne 0) { throw "Second launch activation failed with code $($secondary.ExitCode)." }

$matchingHosts = @(Get-Process PenguinConvexity.Desktop -ErrorAction SilentlyContinue)
if ($matchingHosts.Count -ne 1) { throw "Expected one C2.3 desktop host, found $($matchingHosts.Count)." }

$edgeAfter = @(Get-Process msedge -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id | Sort-Object)
$chromeAfter = @(Get-Process chrome -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id | Sort-Object)
foreach ($processId in ($edgeVisibleBefore + $chromeVisibleBefore)) {
  if (-not (Get-Process -Id $processId -ErrorAction SilentlyContinue)) {
    throw "A pre-existing ordinary browser window was closed during C2.3 launch."
  }
}
$ordinaryBrowserChildren = @(Get-CimInstance Win32_Process | Where-Object {
  $_.ParentProcessId -eq $primary.Id -and ($_.Name -eq "msedge.exe" -or $_.Name -eq "chrome.exe")
})
if ($ordinaryBrowserChildren.Count -ne 0) { throw "C2.3 launched an ordinary Edge or Chrome child process." }

$candidateAfter = Get-Content (Join-Path $projectRoot "runtime\c2.2\candidate-production\status.json") -Raw -Encoding UTF8 | ConvertFrom-Json
if ($candidateBefore.currentRun.run_id -ne $candidateAfter.currentRun.run_id) { throw "Candidate production run changed during desktop smoke test." }
if ($null -ne $candidatePid -and -not (Get-Process -Id $candidatePid -ErrorAction SilentlyContinue)) { throw "Candidate production process stopped during desktop smoke test." }

$primary.CloseMainWindow() | Out-Null
if (-not $primary.WaitForExit(5000)) {
  $primary.Kill()
  throw "C2.3 desktop did not close normally within 5 seconds."
}
Start-Sleep -Milliseconds 300

$serviceAfter = Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -ne $serviceBefore) {
  if ($null -eq $serviceAfter -or $serviceAfter.OwningProcess -ne $serviceBefore.OwningProcess) {
    throw "External local service ownership changed during desktop smoke test."
  }
} else {
  $serviceDeadline = (Get-Date).AddSeconds(3)
  while ($null -ne $serviceAfter -and (Get-Date) -lt $serviceDeadline) {
    Start-Sleep -Milliseconds 150
    $serviceAfter = Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  }
  if ($null -ne $serviceAfter) { throw "Desktop-owned local service remained after the desktop closed." }
}
if (-not (Test-Path (Join-Path $projectRoot "runtime\window-state-c2.3.json"))) { throw "C2.3 window state was not saved." }

[PSCustomObject]@{
  status = "passed"
  windowShownMs = $windowShownMs
  pageReadyMs = $pageReadyMs
  duplicateActivationMs = $secondaryMs
  primaryExitCode = $primary.ExitCode
  serviceOwnership = if ($null -eq $serviceBefore) { "desktop_started_and_stopped" } else { "external_preserved" }
  servicePid = if ($null -eq $serviceBefore) { $serviceDuring.OwningProcess } else { $serviceAfter.OwningProcess }
  candidatePidUnchanged = if ($null -eq $candidatePid) { "idle" } else { $candidatePid }
  candidateRunIdUnchanged = $candidateAfter.currentRun.run_id
  productPageStatus = $page.StatusCode
  opportunityPageStatus = $productPage.StatusCode
  ordinaryEdgeVisibleWindowsPreserved = $edgeVisibleBefore.Count
  ordinaryChromeVisibleWindowsPreserved = $chromeVisibleBefore.Count
  ordinaryEdgeProcessCountBefore = $edgeBefore.Count
  ordinaryEdgeProcessCountAfter = $edgeAfter.Count
  ordinaryChromeProcessCountBefore = $chromeBefore.Count
  ordinaryChromeProcessCountAfter = $chromeAfter.Count
} | ConvertTo-Json
