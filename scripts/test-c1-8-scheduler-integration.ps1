param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$listener = Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
  throw "Close the Convexity desktop window and local server before this scheduler integration test."
}

$python = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command py.exe -ErrorAction SilentlyContinue }
if (-not $python) { throw "Python was not found." }

$stamp = Get-Date -Format "yyyyMMddTHHmmss"
$testRoot = Join-Path $projectRoot "runtime\cache\s18-$stamp"
New-Item -ItemType Directory -Path $testRoot -Force | Out-Null
$dbPath = Join-Path $testRoot "convexity.db"
$configPath = Join-Path $testRoot "config.json"
$statePath = Join-Path $testRoot "state.json"
$lockPath = Join-Path $testRoot "scheduler.lock"
Copy-Item -LiteralPath (Join-Path $projectRoot "data\convexity.db") -Destination $dbPath
@{
  enabled = $true
  paused = $false
  dailyTime = "08:00"
  timezone = "Asia/Shanghai"
  hourlyDueCheck = $true
} | ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding UTF8

$beforeHash = (Get-FileHash -LiteralPath $dbPath -Algorithm SHA256).Hash
$runner = Join-Path $projectRoot "scripts\run-c1-8-scheduler-isolated-test.ps1"
$taskName = "PenguinConvexity-C1.8-Scheduler-SolAcceptance"
$taskCommand = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}" -TestRoot "{1}"' -f $runner, $testRoot
$startAt = (Get-Date).AddMinutes(5).ToString("HH:mm")

try {
  & schtasks.exe /Create /TN $taskName /TR $taskCommand /SC ONCE /ST $startAt /F /RL LIMITED | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Could not create the isolated acceptance task." }
  & schtasks.exe /Run /TN $taskName | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Could not start the isolated acceptance task." }

  $deadline = (Get-Date).AddSeconds(30)
  do {
    Start-Sleep -Milliseconds 250
    if (Test-Path -LiteralPath $statePath) {
      try { $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { $state = $null }
      if ($state -and $state.status -eq "queued") { break }
    }
  } while ((Get-Date) -lt $deadline)

  if (-not $state -or $state.status -ne "queued" -or $state.lastRunKind -ne "forced") {
    throw "The isolated scheduled run did not complete its dry-run selection."
  }
  $afterHash = (Get-FileHash -LiteralPath $dbPath -Algorithm SHA256).Hash
  if ($beforeHash -ne $afterHash) { throw "The dry-run changed the isolated business database." }
  Write-Output "C1.8 scheduler integration passed: desktop closed, Windows task woke, isolated dry-run completed, database unchanged."
  Write-Output "Evidence: $testRoot"
} finally {
  try { & schtasks.exe /Delete /TN $taskName /F 2>$null | Out-Null } catch {}
}
