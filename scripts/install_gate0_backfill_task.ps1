[CmdletBinding(SupportsShouldProcess)]
param(
    [switch]$Start
)

$ErrorActionPreference = 'Stop'
$TaskName = 'PenguinConvexity-Gate0-Backfill'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Runner = Join-Path $ProjectRoot 'scripts\gate0_backfill_background.py'
$ProgressBuilder = Join-Path $ProjectRoot 'scripts\build_gate0_backfill_progress.py'
$Python = (Get-Command python -ErrorAction Stop).Source

if (-not (Test-Path -LiteralPath $Runner)) {
    throw "Gate 0 background runner not found: $Runner"
}

# Snapshot the protected scheduler before the new task is registered.  This is
# read-only evidence; the installer never edits, disables, or starts it.
$oldScheduler = Get-ScheduledTask -TaskName 'PenguinConvexity-C1.8-Scheduler' -ErrorAction SilentlyContinue
$oldSchedulerSnapshot = if ($oldScheduler) {
    [ordered]@{
        TaskName = $oldScheduler.TaskName
        State = [string]$oldScheduler.State
        Enabled = [bool]$oldScheduler.Settings.Enabled
        Actions = @($oldScheduler.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" })
        Triggers = @($oldScheduler.Triggers | ForEach-Object { "$($_.TriggerType) $($_.StartBoundary) $($_.Repetition.Interval)" })
    }
} else {
    [ordered]@{ TaskName = 'PenguinConvexity-C1.8-Scheduler'; Missing = $true }
}

$action = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "`"$Runner`" --resume" `
    -WorkingDirectory $ProjectRoot
$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At ((Get-Date).AddMinutes(1)) `
    -RepetitionInterval (New-TimeSpan -Minutes 15) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances Ignore `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description 'Penguin Convexity Gate 0 independent resumable 90-day backfill; never writes product DB or C1.8 scheduler.'

if ($PSCmdlet.ShouldProcess($TaskName, 'Register independent Gate 0 scheduled task')) {
    Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
    if ($Start) {
        Start-ScheduledTask -TaskName $TaskName
    }
}

$newTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
[ordered]@{
    taskName = $TaskName
    runner = $Runner
    progressBuilder = $ProgressBuilder
    startRequested = [bool]$Start
    registered = [bool]$newTask
    newTaskState = if ($newTask) { [string]$newTask.State } else { 'not_registered' }
    protectedSchedulerBefore = $oldSchedulerSnapshot
} | ConvertTo-Json -Depth 8
