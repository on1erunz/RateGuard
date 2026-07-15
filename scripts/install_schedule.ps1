$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $PSScriptRoot "run_scheduled.ps1"
$powershell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$user = if ($env:USERDOMAIN) { "$env:USERDOMAIN\$env:USERNAME" } else { $env:USERNAME }

function Register-RateGuardTask([string]$name, [string]$schedule, [string]$time, [string]$mode) {
    $taskCommand = "`"$powershell`" -NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" -Mode $mode"
    $arguments = @("/Create", "/TN", $name, "/TR", $taskCommand, "/SC", $schedule, "/ST", $time, "/RU", $user, "/IT", "/RL", "LIMITED", "/F")
    & schtasks.exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "Could not register task $name" }
}

Register-RateGuardTask "RateGuard-Ctrip-Hourly" "HOURLY" "00:00" "hourly"
Register-RateGuardTask "RateGuard-Ctrip-Anchors-0000" "DAILY" "00:00" "anchors"
Register-RateGuardTask "RateGuard-Ctrip-Anchors-1200" "DAILY" "12:00" "anchors"

Write-Host "RateGuard Ctrip schedule installed for $user."
