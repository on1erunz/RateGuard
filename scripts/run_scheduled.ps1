param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("hourly", "anchors")]
    [string]$Mode
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
Set-Location $root

# The hourly task is registered as a simple hourly trigger.  Keep the trigger
# harmless outside the requested 08:00-24:00 window: do not even publish the
# dashboard when the collection itself is intentionally skipped.
$now = Get-Date
if ($Mode -eq "hourly" -and ($now.Hour -lt 8 -or $now.Hour -gt 23)) {
    exit 0
}

# Python writes normal logger output to stderr. Run it through cmd.exe so
# PowerShell does not turn those log lines into NativeCommandError records.
$collectionLog = Join-Path $logDir "scheduled-$Mode.log"
$collectionCommand = "`"$python`" -m src.scheduled_run --mode $Mode >> `"$collectionLog`" 2>&1"
& cmd.exe /d /s /c $collectionCommand
$collectionExit = $LASTEXITCODE
if ($collectionExit -ne 0) { exit $collectionExit }

$publishLog = Join-Path $logDir "dashboard-sync.log"
$publishCommand = "`"$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe`" -NoProfile -ExecutionPolicy Bypass -File `"$PSScriptRoot\publish_dashboard.ps1`" >> `"$publishLog`" 2>&1"
& cmd.exe /d /s /c $publishCommand
$publishExit = $LASTEXITCODE
exit $publishExit
