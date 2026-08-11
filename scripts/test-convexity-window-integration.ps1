$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$launcherPath = Join-Path $PSScriptRoot "launch-convexity.ps1"
$windowStatePath = Join-Path $projectRoot "runtime\window-state.json"
$backupPath = "$windowStatePath.integration-backup"
$shortcutName = "$([char]0x4F01)$([char]0x9E45)$([char]0x6295)$([char]0x7814)-$([char]0x51F8)$([char]0x6027).lnk"
$shortcutPath = Join-Path ([Environment]::GetFolderPath("Desktop")) $shortcutName
. (Join-Path $PSScriptRoot "convexity-window-state.ps1")

if (-not (Test-Path -LiteralPath $shortcutPath)) {
  throw "The convexity desktop shortcut is missing."
}
$shortcutShell = New-Object -ComObject WScript.Shell
$shortcut = $shortcutShell.CreateShortcut($shortcutPath)
if ($shortcut.TargetPath -ne (Join-Path $PSHOME "powershell.exe") -or $shortcut.Arguments -notlike "*$launcherPath*") {
  throw "The convexity desktop shortcut does not target the current launcher."
}

function Assert-Near {
  param(
    [Parameter(Mandatory = $true)][int]$Actual,
    [Parameter(Mandatory = $true)][int]$Expected,
    [string]$Label,
    [int]$Tolerance = 16
  )

  if ([Math]::Abs($Actual - $Expected) -gt $Tolerance) {
    throw "$Label expected $Expected but received $Actual."
  }
}

function Start-TestApp {
  $process = Start-Process -FilePath $shortcutPath -PassThru
  $window = Wait-PenguinAppWindow -TimeoutSeconds 25
  if ($null -eq $window) {
    if (-not $process.HasExited) {
      $process.Kill()
    }
    throw "The convexity desktop window did not open."
  }

  return [PSCustomObject]@{
    Process = $process
    Window = $window
  }
}

function Close-TestApp {
  param($App)

  if ($null -eq $App) {
    return
  }

  Close-PenguinAppWindow -Handle ([long]$App.Window.Handle)
  if (-not $App.Process.WaitForExit(15000)) {
    $App.Process.Kill()
    throw "The convexity launcher did not exit after its window closed."
  }
}

function Close-AllConvexityWindows {
  Get-PenguinAppWindows | ForEach-Object {
    Close-PenguinAppWindow -Handle $_.Handle
  }
  $deadline = (Get-Date).AddSeconds(10)
  while ((Get-PenguinAppWindows).Count -and (Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 250
  }
}

$firstApp = $null
$secondApp = $null
$hadExistingState = Test-Path -LiteralPath $windowStatePath
$hadExistingApp = (Get-PenguinAppWindows).Count -gt 0
try {
  Close-AllConvexityWindows
  Start-Sleep -Milliseconds 800
  if ($hadExistingState) {
    Copy-Item -LiteralPath $windowStatePath -Destination $backupPath -Force
  }

  Add-Type -AssemblyName System.Windows.Forms
  $area = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
  $initialState = [PSCustomObject]@{
    Left = $area.Left + 40
    Top = $area.Top + 40
    Width = [Math]::Min(1080, $area.Width - 100)
    Height = [Math]::Min(720, $area.Height - 100)
    Maximized = $false
  }
  Save-PenguinWindowState -StatePath $windowStatePath -State $initialState

  $firstApp = Start-TestApp
  Start-Sleep -Milliseconds 800
  $initialPlacement = [PenguinWindowNative]::ReadPlacement([long]$firstApp.Window.Handle)
  Assert-Near -Actual $initialPlacement.Left -Expected $initialState.Left -Label "Initial left"
  Assert-Near -Actual $initialPlacement.Top -Expected $initialState.Top -Label "Initial top"
  Assert-Near -Actual $initialPlacement.Width -Expected $initialState.Width -Label "Initial width"
  Assert-Near -Actual $initialPlacement.Height -Expected $initialState.Height -Label "Initial height"

  $duplicateLauncher = Start-Process -FilePath $shortcutPath -PassThru
  if (-not $duplicateLauncher.WaitForExit(10000)) {
    $duplicateLauncher.Kill()
    throw "A repeated convexity desktop launch did not return control to the existing window."
  }
  Start-Sleep -Milliseconds 500
  $openWindows = @(Get-PenguinAppWindows)
  if ($openWindows.Count -ne 1) {
    throw "Repeated launch created $($openWindows.Count) convexity windows instead of one."
  }
  if ([long]$openWindows[0].Handle -ne [long]$firstApp.Window.Handle) {
    throw "Repeated launch did not preserve the original convexity window."
  }

  $changedState = [PSCustomObject]@{
    Left = $area.Left + 120
    Top = $area.Top + 90
    Width = [Math]::Min(960, $area.Width - 180)
    Height = [Math]::Min(640, $area.Height - 160)
    Maximized = $false
  }
  [PenguinWindowNative]::RestorePlacement(
    [long]$firstApp.Window.Handle,
    $changedState.Left,
    $changedState.Top,
    $changedState.Width,
    $changedState.Height,
    $false
  ) | Out-Null

  $saved = $null
  $deadline = (Get-Date).AddSeconds(10)
  do {
    Start-Sleep -Milliseconds 500
    $saved = Read-PenguinWindowState -StatePath $windowStatePath
  } while (
    (Get-Date) -lt $deadline -and
    ($null -eq $saved -or [Math]::Abs($saved.Left - $changedState.Left) -gt 16)
  )
  if ($null -eq $saved) {
    throw "The changed convexity window state was not saved."
  }
  Assert-Near -Actual $saved.Left -Expected $changedState.Left -Label "Saved left"
  Assert-Near -Actual $saved.Top -Expected $changedState.Top -Label "Saved top"
  Assert-Near -Actual $saved.Width -Expected $changedState.Width -Label "Saved width"
  Assert-Near -Actual $saved.Height -Expected $changedState.Height -Label "Saved height"

  Close-TestApp -App $firstApp
  $firstApp = $null

  $secondApp = Start-TestApp
  Start-Sleep -Milliseconds 800
  $restoredPlacement = [PenguinWindowNative]::ReadPlacement([long]$secondApp.Window.Handle)
  Assert-Near -Actual $restoredPlacement.Left -Expected $changedState.Left -Label "Restored left"
  Assert-Near -Actual $restoredPlacement.Top -Expected $changedState.Top -Label "Restored top"
  Assert-Near -Actual $restoredPlacement.Width -Expected $changedState.Width -Label "Restored width"
  Assert-Near -Actual $restoredPlacement.Height -Expected $changedState.Height -Label "Restored height"

  Write-Output "Penguin Research Convexity desktop integration test passed: launch, singleton, save, restore."
} finally {
  if ($null -ne $firstApp) {
    Close-TestApp -App $firstApp
  }
  if ($null -ne $secondApp) {
    Close-TestApp -App $secondApp
  }

  if ($hadExistingState -and (Test-Path -LiteralPath $backupPath)) {
    Move-Item -LiteralPath $backupPath -Destination $windowStatePath -Force
  } else {
    Remove-Item -LiteralPath $windowStatePath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $backupPath -Force -ErrorAction SilentlyContinue
  }

  if ($hadExistingApp) {
    Start-Process -FilePath $shortcutPath | Out-Null
  }
}
