param(
  [switch]$HealthCheck
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$serverScript = Join-Path $projectRoot "scripts\serve_local.py"
$windowStateScript = Join-Path $PSScriptRoot "convexity-window-state.ps1"
$runtimeRoot = Join-Path $projectRoot "runtime"
$windowStatePath = Join-Path $runtimeRoot "window-state.json"
$logRoot = Join-Path $runtimeRoot "logs"
$stdoutLog = Join-Path $logRoot "server.stdout.log"
$stderrLog = Join-Path $logRoot "server.stderr.log"
$appUrl = "http://127.0.0.1:8766/desktop/index.html"
$opportunityUrl = "http://127.0.0.1:8766/candidate-pool.html"
$healthUrl = "http://127.0.0.1:8766/api/health"
$c18StatusUrl = "http://127.0.0.1:8766/api/c1-8/status"
$c22StatusUrl = "http://127.0.0.1:8766/api/c2.2/status"
$productTitle = "$([char]0x4F01)$([char]0x9E45)$([char]0x6295)$([char]0x7814)-$([char]0x51F8)$([char]0x6027)"
. $windowStateScript
$singleInstanceMutex = $null
$ownsSingleInstance = $false

function Test-ConvexityServer {
  try {
    $appResponse = Invoke-WebRequest -Uri $appUrl -UseBasicParsing -TimeoutSec 3
    $opportunityResponse = Invoke-WebRequest -Uri $opportunityUrl -UseBasicParsing -TimeoutSec 3
    $healthResponse = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 3
    $c18Response = Invoke-WebRequest -Uri $c18StatusUrl -UseBasicParsing -TimeoutSec 3
    $c22Response = Invoke-WebRequest -Uri $c22StatusUrl -UseBasicParsing -TimeoutSec 3
    $health = $healthResponse.Content | ConvertFrom-Json
    $c18 = $c18Response.Content | ConvertFrom-Json
    $c22 = $c22Response.Content | ConvertFrom-Json
    return (
      $appResponse.StatusCode -eq 200 -and
      $appResponse.Content -match "data-convexity-desktop-shell" -and
      $appResponse.Content -notmatch "RWA" -and
      $opportunityResponse.StatusCode -eq 200 -and
      $opportunityResponse.Content -match "c2-2-front.js" -and
      $healthResponse.StatusCode -eq 200 -and
      $c18Response.StatusCode -eq 200 -and
      $c22Response.StatusCode -eq 200 -and
      $health.product -eq $productTitle -and
      $health.migrationRelease -eq "M1.0" -and
      $health.convexityRelease -eq "C1.7" -and
      $health.experienceRelease -eq "C2.2" -and
      $c18.version -eq "C1.8" -and
      $c22.schemaVersion -eq "c2.2-update-status-v1"
    )
  } catch {
    return $false
  }
}

function Stop-StaleConvexityServer {
  Get-CimInstance Win32_Process |
    Where-Object {
      $_.CommandLine -and
      $_.CommandLine.IndexOf($serverScript, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
    } |
    ForEach-Object {
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Show-ConvexityError([string]$message) {
  Add-Type -AssemblyName PresentationFramework
  [System.Windows.MessageBox]::Show($message, $productTitle, "OK", "Error") | Out-Null
}

if (-not $HealthCheck) {
  $createdNew = $false
  $singleInstanceMutex = [System.Threading.Mutex]::new(
    $true,
    "Local\PenguinResearchConvexityDesktopLauncher",
    [ref]$createdNew
  )
  $ownsSingleInstance = $createdNew
  if (-not $ownsSingleInstance) {
    $existingAppWindow = Wait-PenguinAppWindow -TimeoutSeconds 20
    if ($null -ne $existingAppWindow) {
      Activate-PenguinAppWindow -Handle $existingAppWindow.Handle
    }
    $singleInstanceMutex.Dispose()
    exit 0
  }
}

try {
  New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
  New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
  New-Item -ItemType Directory -Path (Join-Path $runtimeRoot "cache") -Force | Out-Null

  if (-not (Test-ConvexityServer)) {
    Stop-StaleConvexityServer
    Start-Sleep -Milliseconds 300
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    $pythonArguments = @("`"$serverScript`"", "--port", "8766")
    if (-not $python) {
      $python = Get-Command py.exe -ErrorAction SilentlyContinue
      $pythonArguments = @("-3", "`"$serverScript`"", "--port", "8766")
    }
    if (-not $python) {
      throw "Python was not found. The local service cannot start."
    }

    Start-Process -FilePath $python.Source -ArgumentList $pythonArguments -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog
    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt += 1) {
      Start-Sleep -Milliseconds 500
      if (Test-ConvexityServer) {
        $ready = $true
        break
      }
    }
    if (-not $ready) {
      $stderrContent = if (Test-Path -LiteralPath $stderrLog) { Get-Content -LiteralPath $stderrLog -Raw -Encoding UTF8 } else { "" }
      $detail = if ([string]::IsNullOrWhiteSpace($stderrContent)) { "" } else { $stderrContent.Trim() }
      throw "The local convexity service did not start. $detail"
    }
  }

  if ($HealthCheck) {
    Write-Output "Penguin Research Convexity is ready: $appUrl"
    exit 0
  }

  $edgeCandidates = @(
    (Join-Path ${env:ProgramFiles(x86)} "Microsoft\Edge\Application\msedge.exe"),
    (Join-Path $env:ProgramFiles "Microsoft\Edge\Application\msedge.exe")
  )
  $edge = $edgeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
  if ($edge) {
    $savedWindowState = Resolve-PenguinWindowState -State (Read-PenguinWindowState -StatePath $windowStatePath)
    $existingAppWindow = Consolidate-PenguinAppWindows
    if ($null -ne $existingAppWindow) {
      Watch-PenguinAppWindow -Handle $existingAppWindow.Handle -StatePath $windowStatePath
      return
    }
    $existingWindowHandles = @(Get-PenguinEdgeWindowHandles)
    $edgeArguments = @(Get-PenguinWindowLaunchArguments -AppUrl $appUrl -State $savedWindowState)
    Start-Process -FilePath $edge -ArgumentList $edgeArguments
    $appWindow = Find-PenguinAppWindow -BeforeHandles $existingWindowHandles
    if ($null -ne $appWindow) {
      Restore-PenguinAppWindow -Handle $appWindow.Handle -State $savedWindowState
      Watch-PenguinAppWindow -Handle $appWindow.Handle -StatePath $windowStatePath
    }
  } else {
    Start-Process $appUrl
  }
} catch {
  Show-ConvexityError $_.Exception.Message
  exit 1
} finally {
  if ($ownsSingleInstance -and $null -ne $singleInstanceMutex) {
    try { $singleInstanceMutex.ReleaseMutex() } catch { }
    $singleInstanceMutex.Dispose()
  }
}
