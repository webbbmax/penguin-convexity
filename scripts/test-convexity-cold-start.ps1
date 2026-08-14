param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$serverScript = Join-Path $projectRoot "scripts\serve_local.py"
$launcherScript = Join-Path $projectRoot "scripts\launch-convexity.ps1"
. (Join-Path $PSScriptRoot "desktop-acceptance-guard.ps1")

$listenerBefore = Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listenerBefore) {
  $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listenerBefore.OwningProcess)"
  $ownedByCommandLine = $process.CommandLine -and $process.CommandLine.IndexOf($serverScript, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
  $ownedByHealth = $false
  try {
    $currentHealth = Invoke-RestMethod http://127.0.0.1:8766/api/health -TimeoutSec 2
    $expectedDatabase = (Resolve-Path (Join-Path $projectRoot "data\convexity.db")).Path
    $ownedByHealth = $currentHealth.port -eq 8766 -and $currentHealth.database -eq $expectedDatabase
  } catch { }
  if (-not $ownedByCommandLine -and -not $ownedByHealth) {
    throw "Port 8766 is not owned by this project; the test made no changes."
  }
}

$cleanupGuard = New-DesktopAcceptanceGuard -Name "legacy-cold-start" -ProjectRoot $projectRoot
$cleanupResult = $null
$result = $null
try {
  $watch = [Diagnostics.Stopwatch]::StartNew()
  $launcher = Start-Process -FilePath powershell.exe -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $launcherScript, "-HealthCheck"
  ) -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
  Register-DesktopAcceptanceProcess -Guard $cleanupGuard -Process $launcher -Role "legacy_health_launcher"

  $ready = $false
  $deadline = (Get-Date).AddSeconds(60)
  do {
    Start-Sleep -Milliseconds 250
    try {
      $health = Invoke-RestMethod http://127.0.0.1:8766/api/health -TimeoutSec 1
      $scheduler = Invoke-RestMethod http://127.0.0.1:8766/api/c1-8/status -TimeoutSec 1
      if ($health.status -eq "ready" -and $health.experienceRelease -eq "C2.1" -and $scheduler.version -eq "C1.8") {
        $ready = $true
        break
      }
    } catch { }
  } while ((Get-Date) -lt $deadline)
  $watch.Stop()
  $launcherExited = $launcher.WaitForExit(5000)
  $launcher.Refresh()

  if (-not $ready) { throw "The desktop entry did not become ready within 60 seconds." }
  if (-not $launcherExited) { throw "The desktop launcher did not exit after the service became ready." }
  if ($launcher.ExitCode -ne 0) { throw "The desktop launcher exited with code $($launcher.ExitCode)." }

  $desktop = [Environment]::GetFolderPath("Desktop")
  $shortcutName = "$([char]0x4F01)$([char]0x9E45)$([char]0x6295)$([char]0x7814)-$([char]0x51F8)$([char]0x6027).lnk"
  $shortcutPath = Join-Path $desktop $shortcutName
  if (-not (Test-Path -LiteralPath $shortcutPath)) { throw "The desktop shortcut is missing." }
  $shell = New-Object -ComObject WScript.Shell
  $shortcut = $shell.CreateShortcut($shortcutPath)
  if ($shortcut.TargetPath -ne (Join-Path $PSHOME "powershell.exe")) {
    throw "The desktop shortcut target is not the expected legacy PowerShell launcher."
  }
  if ($shortcut.Arguments -notlike "*launch-convexity.ps1*") {
    throw "The desktop shortcut does not invoke launch-convexity.ps1."
  }

  $result = [PSCustomObject]@{
    Ready = $ready
    ReadySeconds = [Math]::Round($watch.Elapsed.TotalSeconds, 2)
    LauncherExited = $launcherExited
    LauncherExitCode = $launcher.ExitCode
    ExperienceRelease = $health.experienceRelease
    Shortcut = $shortcutPath
    StartupState = $health.startupRebuild.state
    StartupStarted = $health.startupRebuild.startedAt
    StartupFinished = $health.startupRebuild.finishedAt
    ServiceMode = if ($listenerBefore) { "pre_existing_preserved" } else { "test_started_then_removed" }
  }
} finally {
  $cleanupResult = Complete-DesktopAcceptanceGuard -Guard $cleanupGuard
}
$result | Add-Member -NotePropertyName cleanup -NotePropertyValue $cleanupResult
$result | ConvertTo-Json -Depth 4
