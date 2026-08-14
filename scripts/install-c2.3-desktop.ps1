param(
  [switch]$Preview,
  [switch]$Release
)

$ErrorActionPreference = "Stop"
if ($Preview -eq $Release) {
  throw "Choose exactly one mode: -Preview or -Release"
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$exe = Join-Path $projectRoot "desktop-host\publish\win-x64\PenguinConvexity.Desktop.exe"
$icon = Join-Path $projectRoot "desktop\assets\penguin-research-clean.ico"
$productTitle = "$([char]0x4F01)$([char]0x9E45)$([char]0x6295)$([char]0x7814)-$([char]0x51F8)$([char]0x6027)"
$previewLabel = "$([char]0x9884)$([char]0x89C8)"
if (-not (Test-Path -LiteralPath $exe)) { throw "Build C2.3 before installing the shortcut." }

$shell = New-Object -ComObject WScript.Shell
if ($Preview) {
  $previewRoot = Join-Path $projectRoot "runtime\c2.3-preview"
  New-Item -ItemType Directory -Path $previewRoot -Force | Out-Null
  $shortcutPath = Join-Path $previewRoot "${productTitle}-C2.3${previewLabel}.lnk"
} else {
  $shortcutPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "${productTitle}.lnk"
  $rollbackRoot = Join-Path $projectRoot "runtime\c2.3-rollback"
  New-Item -ItemType Directory -Path $rollbackRoot -Force | Out-Null
  if (Test-Path -LiteralPath $shortcutPath) {
    Copy-Item -LiteralPath $shortcutPath -Destination (Join-Path $rollbackRoot "${productTitle}.pre-c2.3.lnk") -Force
  }
}

$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $exe
$shortcut.Arguments = ""
$shortcut.WorkingDirectory = $projectRoot
$shortcut.IconLocation = "$icon,0"
$shortcut.Description = if ($Preview) { "$productTitle C2.3 Preview" } else { $productTitle }
$shortcut.Save()

[PSCustomObject]@{
  status = "success"
  mode = if ($Preview) { "preview" } else { "release" }
  shortcut = $shortcutPath
  target = $exe
  officialDesktopEntryChanged = [bool]$Release
} | ConvertTo-Json
