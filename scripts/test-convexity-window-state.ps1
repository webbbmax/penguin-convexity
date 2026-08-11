$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "convexity-window-state.ps1")

function Assert-ConvexityWindowState {
  param(
    [Parameter(Mandatory = $true)][bool]$Condition,
    [Parameter(Mandatory = $true)][string]$Message
  )

  if (-not $Condition) {
    throw $Message
  }
}

$testDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ("convexity-window-state-" + [Guid]::NewGuid().ToString("N"))
$statePath = Join-Path $testDirectory "window-state.json"
New-Item -ItemType Directory -Path $testDirectory | Out-Null

try {
  $originalState = [PSCustomObject]@{
    Left = 180
    Top = 120
    Width = 1180
    Height = 760
    Maximized = $false
  }
  Save-PenguinWindowState -StatePath $statePath -State $originalState
  $savedState = Read-PenguinWindowState -StatePath $statePath

  Assert-ConvexityWindowState ($savedState.Left -eq 180) "Window left position was not preserved."
  Assert-ConvexityWindowState ($savedState.Top -eq 120) "Window top position was not preserved."
  Assert-ConvexityWindowState ($savedState.Width -eq 1180) "Window width was not preserved."
  Assert-ConvexityWindowState ($savedState.Height -eq 760) "Window height was not preserved."
  Assert-ConvexityWindowState (-not $savedState.Maximized) "Window maximized state was not preserved."

  $launchArguments = @(Get-PenguinWindowLaunchArguments -AppUrl "http://127.0.0.1:8766/desktop/index.html" -State $savedState)
  Assert-ConvexityWindowState ($launchArguments -contains "--window-position=180,120") "Launch position argument is missing."
  Assert-ConvexityWindowState ($launchArguments -contains "--window-size=1180,760") "Launch size argument is missing."
  Assert-ConvexityWindowState (-not ($launchArguments -contains "--start-maximized")) "Normal windows must not be forced to maximize."

  $maximizedState = [PSCustomObject]@{
    Left = 180
    Top = 120
    Width = 1180
    Height = 760
    Maximized = $true
  }
  $maximizedArguments = @(Get-PenguinWindowLaunchArguments -AppUrl "http://127.0.0.1:8766/desktop/index.html" -State $maximizedState)
  Assert-ConvexityWindowState ($maximizedArguments -contains "--start-maximized") "Maximized windows must restore maximized."

  Set-Content -LiteralPath $statePath -Value "{invalid json" -Encoding UTF8
  Assert-ConvexityWindowState ($null -eq (Read-PenguinWindowState -StatePath $statePath)) "Invalid state files must be ignored."

  $offscreenState = [PSCustomObject]@{
    Left = 999999
    Top = 999999
    Width = 1180
    Height = 760
    Maximized = $false
  }
  $resolvedState = Resolve-PenguinWindowState -State $offscreenState
  $visibleOnScreen = @([System.Windows.Forms.Screen]::AllScreens) | Where-Object {
    $area = $_.WorkingArea
    $resolvedState.Left -lt $area.Right -and
      ($resolvedState.Left + $resolvedState.Width) -gt $area.Left -and
      $resolvedState.Top -lt $area.Bottom -and
      ($resolvedState.Top + $resolvedState.Height) -gt $area.Top
  }
  Assert-ConvexityWindowState ($visibleOnScreen.Count -gt 0) "Off-screen state must be restored onto an active display."

  Write-Output "Penguin Research Convexity window-state tests passed."
} finally {
  if (Test-Path -LiteralPath $testDirectory) {
    Remove-Item -LiteralPath $testDirectory -Recurse -Force
  }
}
