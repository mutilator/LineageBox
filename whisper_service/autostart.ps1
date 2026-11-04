# Starts the speech-to-text service when you log on to Windows: a Task Scheduler task, "LineageBox Whisper", that runs
# it with start.ps1's defaults (pythonw, so no window opens). stop.ps1 still stops the running service.
#   .\autostart.ps1           add the task (or update it)
#   .\autostart.ps1 -Remove   remove it
param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$name = 'LineageBox Whisper'
if ($Remove) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed the '$name' task."
    exit
}
$root = Split-Path $PSScriptRoot -Parent
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
$log = Join-Path $env:LOCALAPPDATA 'lineagebox-whisper\service.log'
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
$user = "$env:USERDOMAIN\$env:USERNAME"

$action = New-ScheduledTaskAction -Execute $pythonw -Argument "-m whisper_service --log `"$log`"" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
# no time limit (by default Task Scheduler stops a task after 3 days), one at a time, and on battery too
$options = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
               -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $options -Principal $principal `
    -Description 'LineageBox speech-to-text service on port 9300 (whisper_service; see start.ps1)' -Force | Out-Null
Write-Host "The service now starts when $user logs on (Task Scheduler: '$name')."
