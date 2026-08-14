$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$productTitle = "$([char]0x4F01)$([char]0x9E45)$([char]0x6295)$([char]0x7814)-$([char]0x51F8)$([char]0x6027)"
$desktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "${productTitle}.lnk"
$backupShortcut = Join-Path $projectRoot "runtime\c2.3-rollback\${productTitle}.pre-c2.3.lnk"

if (Test-Path -LiteralPath $backupShortcut) {
  Copy-Item -LiteralPath $backupShortcut -Destination $desktopShortcut -Force
} else {
  $shell = New-Object -ComObject WScript.Shell
  $shortcut = $shell.CreateShortcut($desktopShortcut)
  $shortcut.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
  $shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$projectRoot\scripts\launch-convexity.ps1`""
  $shortcut.WorkingDirectory = $projectRoot
  $shortcut.IconLocation = "$projectRoot\desktop\assets\penguin-research-clean.ico,0"
  $shortcut.Description = "$productTitle C2.2 Rollback"
  $shortcut.Save()
}

[PSCustomObject]@{
  status = "success"
  restored = $desktopShortcut
  target = "PowerShell C2.2 launcher"
  c23FilesRetained = $true
} | ConvertTo-Json
