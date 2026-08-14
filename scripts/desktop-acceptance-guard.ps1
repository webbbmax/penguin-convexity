function Get-DesktopAcceptanceProcessRecord {
  param([Parameter(Mandatory = $true)][int]$ProcessId)

  $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
  if ($null -eq $process) { return $null }
  $created = if ($process.CreationDate -is [DateTime]) {
    $process.CreationDate.ToUniversalTime().ToString("o")
  } else {
    [string]$process.CreationDate
  }
  return [PSCustomObject]@{
    Id = [int]$process.ProcessId
    ParentId = [int]$process.ParentProcessId
    Name = [string]$process.Name
    Created = $created
    Identity = "$([int]$process.ProcessId)|$created"
    CommandLine = [string]$process.CommandLine
  }
}

function Get-DesktopAcceptanceState {
  $desktopHosts = @(Get-CimInstance Win32_Process -Filter "Name='PenguinConvexity.Desktop.exe'" -ErrorAction SilentlyContinue |
    ForEach-Object { Get-DesktopAcceptanceProcessRecord -ProcessId $_.ProcessId } |
    Where-Object { $null -ne $_ } |
    Sort-Object Identity)
  $webViews = @(Get-CimInstance Win32_Process -Filter "Name='msedgewebview2.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match '(?i)PenguinConvexity\.Desktop(?:\.exe)?' } |
    ForEach-Object { Get-DesktopAcceptanceProcessRecord -ProcessId $_.ProcessId } |
    Where-Object { $null -ne $_ } |
    Sort-Object Identity)
  $portOwners = @(Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique |
    ForEach-Object { Get-DesktopAcceptanceProcessRecord -ProcessId $_ } |
    Where-Object { $null -ne $_ } |
    Sort-Object Identity)
  return [PSCustomObject]@{
    DesktopHosts = $desktopHosts
    WebViews = $webViews
    Port8766Owners = $portOwners
  }
}

function New-DesktopAcceptanceGuard {
  param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][string]$ProjectRoot
  )

  return [PSCustomObject]@{
    Name = $Name
    ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
    Baseline = Get-DesktopAcceptanceState
    Tracked = [System.Collections.Generic.List[object]]::new()
    StartedAt = Get-Date
  }
}

function Register-DesktopAcceptanceProcess {
  param(
    [Parameter(Mandatory = $true)]$Guard,
    [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process,
    [Parameter(Mandatory = $true)][string]$Role
  )

  $record = Get-DesktopAcceptanceProcessRecord -ProcessId $Process.Id
  if ($null -ne $record) {
    $Guard.Tracked.Add([PSCustomObject]@{ Role = $Role; Process = $record }) | Out-Null
  }
}

function Test-DesktopAcceptanceIdentity {
  param([Parameter(Mandatory = $true)]$Record)

  $current = Get-DesktopAcceptanceProcessRecord -ProcessId $Record.Id
  return $null -ne $current -and $current.Identity -eq $Record.Identity
}

function Register-DesktopAcceptanceDescendants {
  param([Parameter(Mandatory = $true)]$Guard)

  $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
  $known = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
  $queue = [System.Collections.Generic.Queue[int]]::new()
  foreach ($tracked in @($Guard.Tracked)) {
    $known.Add($tracked.Process.Identity) | Out-Null
    $queue.Enqueue([int]$tracked.Process.Id)
  }
  while ($queue.Count -gt 0) {
    $parentId = $queue.Dequeue()
    foreach ($child in @($all | Where-Object { [int]$_.ParentProcessId -eq $parentId })) {
      $record = Get-DesktopAcceptanceProcessRecord -ProcessId $child.ProcessId
      if ($null -ne $record -and $known.Add($record.Identity)) {
        $Guard.Tracked.Add([PSCustomObject]@{ Role = "test_descendant"; Process = $record }) | Out-Null
        $queue.Enqueue([int]$record.Id)
      }
    }
  }
}

function Stop-DesktopAcceptanceRecord {
  param(
    [Parameter(Mandatory = $true)]$Record,
    [switch]$CloseWindowFirst
  )

  if (-not (Test-DesktopAcceptanceIdentity -Record $Record)) { return }
  if ($CloseWindowFirst) {
    $process = Get-Process -Id $Record.Id -ErrorAction SilentlyContinue
    if ($null -ne $process) {
      $process.CloseMainWindow() | Out-Null
      $process.WaitForExit(5000) | Out-Null
    }
  }
  if (Test-DesktopAcceptanceIdentity -Record $Record) {
    Stop-Process -Id $Record.Id -Force -ErrorAction SilentlyContinue
  }
}

function Get-DesktopAcceptanceIdentityList {
  param($Records)
  return @($Records | ForEach-Object { $_.Identity } | Sort-Object)
}

function Compare-DesktopAcceptanceState {
  param(
    [Parameter(Mandatory = $true)]$Expected,
    [Parameter(Mandatory = $true)]$Actual
  )

  $differences = [System.Collections.Generic.List[string]]::new()
  foreach ($group in @(
    @{ Name = "desktop"; Before = $Expected.DesktopHosts; After = $Actual.DesktopHosts },
    @{ Name = "webview2"; Before = $Expected.WebViews; After = $Actual.WebViews },
    @{ Name = "port8766"; Before = $Expected.Port8766Owners; After = $Actual.Port8766Owners }
  )) {
    $before = @(Get-DesktopAcceptanceIdentityList -Records $group.Before)
    $after = @(Get-DesktopAcceptanceIdentityList -Records $group.After)
    if (($before -join ",") -ne ($after -join ",")) {
      $differences.Add("$($group.Name): before=[$($before -join ',')] after=[$($after -join ',')]") | Out-Null
    }
  }
  return @($differences)
}

function Complete-DesktopAcceptanceGuard {
  param([Parameter(Mandatory = $true)]$Guard)

  Register-DesktopAcceptanceDescendants -Guard $Guard
  $baselineDesktop = @(Get-DesktopAcceptanceIdentityList -Records $Guard.Baseline.DesktopHosts)
  $baselineWebViews = @(Get-DesktopAcceptanceIdentityList -Records $Guard.Baseline.WebViews)
  $baselinePort = @(Get-DesktopAcceptanceIdentityList -Records $Guard.Baseline.Port8766Owners)

  foreach ($record in @((Get-DesktopAcceptanceState).DesktopHosts)) {
    if ($record.Identity -notin $baselineDesktop) {
      Stop-DesktopAcceptanceRecord -Record $record -CloseWindowFirst
    }
  }
  foreach ($tracked in @($Guard.Tracked)) {
    Stop-DesktopAcceptanceRecord -Record $tracked.Process
  }

  $deadline = (Get-Date).AddSeconds(10)
  do {
    Start-Sleep -Milliseconds 150
    $state = Get-DesktopAcceptanceState
    $newWebViews = @($state.WebViews | Where-Object { $_.Identity -notin $baselineWebViews })
    if ($newWebViews.Count -eq 0) { break }
  } while ((Get-Date) -lt $deadline)
  foreach ($record in $newWebViews) {
    Stop-DesktopAcceptanceRecord -Record $record
  }

  $serverScript = [System.IO.Path]::GetFullPath((Join-Path $Guard.ProjectRoot "scripts\serve_local.py"))
  foreach ($record in @((Get-DesktopAcceptanceState).Port8766Owners)) {
    if ($record.Identity -in $baselinePort) { continue }
    $isTracked = @($Guard.Tracked | Where-Object { $_.Process.Identity -eq $record.Identity }).Count -gt 0
    $isProjectService = $record.CommandLine -and $record.CommandLine.IndexOf($serverScript, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
    if ($isTracked -or $isProjectService) {
      Stop-DesktopAcceptanceRecord -Record $record
    }
  }

  $deadline = (Get-Date).AddSeconds(10)
  do {
    $finalState = Get-DesktopAcceptanceState
    $differences = @(Compare-DesktopAcceptanceState -Expected $Guard.Baseline -Actual $finalState)
    $trackedAlive = @($Guard.Tracked | Where-Object { Test-DesktopAcceptanceIdentity -Record $_.Process })
    if ($differences.Count -eq 0 -and $trackedAlive.Count -eq 0) { break }
    Start-Sleep -Milliseconds 200
  } while ((Get-Date) -lt $deadline)

  if ($trackedAlive.Count -gt 0) {
    $differences += "test_processes: $((@($trackedAlive | ForEach-Object { "$($_.Role)=$($_.Process.Identity)" })) -join ',')"
  }
  if ($differences.Count -gt 0) {
    throw "Desktop acceptance cleanup did not restore the baseline: $($differences -join '; ')"
  }
  return [PSCustomObject]@{
    status = "passed"
    name = $Guard.Name
    desktopRestored = $true
    webView2Restored = $true
    port8766Restored = $true
    trackedProcessesStopped = $true
    elapsedSeconds = [Math]::Round(((Get-Date) - $Guard.StartedAt).TotalSeconds, 2)
  }
}
