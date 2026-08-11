param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$serverScript = Join-Path $projectRoot "scripts\serve_local.py"
$launcherScript = Join-Path $projectRoot "scripts\launch-convexity.ps1"
$listener = Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
  $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
  $ownedByCommandLine = $process.CommandLine -and $process.CommandLine.IndexOf($serverScript, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
  $ownedByHealth = $false
  try {
    $currentHealth = Invoke-RestMethod http://127.0.0.1:8766/api/health -TimeoutSec 2
    $expectedDatabase = (Resolve-Path (Join-Path $projectRoot "data\convexity.db")).Path
    $ownedByHealth = $currentHealth.port -eq 8766 -and $currentHealth.database -eq $expectedDatabase
  } catch { }
  if (-not $ownedByCommandLine -and -not $ownedByHealth) {
    throw "Port 8766 is not owned by this project."
  }
  Stop-Process -Id $listener.OwningProcess -Force
  Start-Sleep -Milliseconds 500
}

$watch = [Diagnostics.Stopwatch]::StartNew()
$launcher = Start-Process -FilePath powershell.exe -ArgumentList @(
  "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $launcherScript, "-HealthCheck"
) -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
$ready = $false
$deadline = (Get-Date).AddSeconds(60)
do {
  Start-Sleep -Milliseconds 250
  try {
    $health = Invoke-RestMethod http://127.0.0.1:8766/api/health -TimeoutSec 1
    $scheduler = Invoke-RestMethod http://127.0.0.1:8766/api/c1-8/status -TimeoutSec 1
    if (
      $health.status -eq "ready" -and
      $health.experienceRelease -eq "C2.1" -and
      $scheduler.version -eq "C1.8"
    ) {
      $ready = $true
      break
    }
  } catch { }
} while ((Get-Date) -lt $deadline)
$watch.Stop()
$launcherExited = $launcher.WaitForExit(5000)
$launcher.Refresh()

if (-not $ready) {
  if (-not $launcher.HasExited) { Stop-Process -Id $launcher.Id -Force }
  throw "The desktop entry did not become ready within 60 seconds."
}
if (-not $launcherExited) {
  Stop-Process -Id $launcher.Id -Force
  throw "The desktop launcher did not exit after the C2.1 service became ready."
}
if ($launcher.ExitCode -ne 0) {
  throw "The desktop launcher exited with code $($launcher.ExitCode)."
}

$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutName = "$([char]0x4F01)$([char]0x9E45)$([char]0x6295)$([char]0x7814)-$([char]0x51F8)$([char]0x6027).lnk"
$shortcutPath = Join-Path $desktop $shortcutName
if (-not (Test-Path -LiteralPath $shortcutPath)) {
  throw "The desktop shortcut is missing."
}
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
if ($shortcut.TargetPath -ne (Join-Path $PSHOME "powershell.exe")) {
  throw "The desktop shortcut target is not the expected PowerShell launcher."
}
if ($shortcut.Arguments -notlike "*launch-convexity.ps1*") {
  throw "The desktop shortcut does not invoke launch-convexity.ps1."
}
[pscustomobject]@{
  Ready = $ready
  ReadySeconds = [Math]::Round($watch.Elapsed.TotalSeconds, 2)
  LauncherExited = $launcherExited
  LauncherExitCode = $launcher.ExitCode
  ExperienceRelease = $health.experienceRelease
  Shortcut = $shortcutPath
  StartupState = $health.startupRebuild.state
  StartupStarted = $health.startupRebuild.startedAt
  StartupFinished = $health.startupRebuild.finishedAt
} | ConvertTo-Json -Depth 4
