# Stops the speech-to-text service that start.ps1 started, and with it whisper-server (which then frees its GPU memory).
$procs = @(Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" |
           Where-Object CommandLine -like '*-m whisper_service*')
foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }

# whisper-server ends with the service on its own (it runs in a job that closes with it); this is only a backstop
$bin = Join-Path $env:LOCALAPPDATA 'lineagebox-whisper\bin'
$servers = @(Get-Process whisper-server -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$bin\*" })
$servers | Stop-Process -Force -ErrorAction SilentlyContinue

$ids = @($procs | ForEach-Object ProcessId) + @($servers | ForEach-Object Id)
if ($ids) { Write-Host "Stopped (pid $($ids -join ', '))." }
else { Write-Host 'It was not running.' }
