# Installs the daily routine in the Windows Task Scheduler (on-premise deployment).
#
# Usage (PowerShell, at the repository root):
#   powershell -ExecutionPolicy Bypass -File scripts\register_scheduled_task.ps1
#
# The task runs every day at 08:00. If the computer is off at that time, it runs as soon
# as it is on (StartWhenAvailable); a run missed for a whole day is recovered by the
# routine's own CATCHUP_DAYS window. Idempotency guarantees nothing is sent twice.

$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $repo "scripts\run_daily.cmd"
if (-not (Test-Path $runner)) {
    throw "Could not find $runner - run this from the repository root."
}

$name = "Settlement reminders"
$action = New-ScheduledTaskAction -Execute $runner
$trigger = New-ScheduledTaskTrigger -Daily -At 08:00
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $name `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Daily settlement reminder routine (mailbox sweep + reminders + collections + digest). Log in logs\reminders.log" `
    -Force | Out-Null

Write-Host "Task '$name' installed (daily, 08:00, recovers missed runs)."
Write-Host "Run now:   Start-ScheduledTask -TaskName '$name'"
Write-Host "Follow:    Get-Content logs\reminders.log -Tail 30"
Write-Host "Remove:    Unregister-ScheduledTask -TaskName '$name' -Confirm:`$false"
