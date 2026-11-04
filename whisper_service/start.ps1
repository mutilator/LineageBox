# Starts the speech-to-text service in the background: no window, output in
# %LOCALAPPDATA%\lineagebox-whisper\service.log. Arguments go to the service (see: .venv\Scripts\python -m
# whisper_service --help), e.g.  .\start.ps1 --idle-minutes 5 stop.ps1 stops it. autostart.ps1 makes it start at logon.
#
# LineageBox, on another machine, can reach it only after Windows Firewall lets it in. An administrator runs this once,
# with the home network's addresses:
#   New-NetFirewallRule -DisplayName 'LineageBox Whisper (TCP 9300)' -Direction Inbound -Protocol TCP -LocalPort 9300 -Profile Private -RemoteAddress 192.168.1.0/24 -Action Allow
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
$log = Join-Path $env:LOCALAPPDATA 'lineagebox-whisper\service.log'

$running = @(Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" |
             Where-Object CommandLine -like '*-m whisper_service*')
if ($running) {
    Write-Host "Already running (pid $($running.ProcessId -join ', '))."
    exit
}

New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
$p = Start-Process -FilePath $pythonw -ArgumentList (@('-m', 'whisper_service', '--log', "`"$log`"") + $args) `
                   -WorkingDirectory $root -PassThru
Start-Sleep -Seconds 4
if ($p.HasExited) {
    Write-Host "It stopped at once (exit $($p.ExitCode)). The end of ${log}:"
    Get-Content $log -Tail 15
    exit 1
}
Write-Host "Running (pid $($p.Id)). Log: $log"
