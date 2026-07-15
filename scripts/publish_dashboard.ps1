$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$snapshot = Join-Path $root "output\dashboard\dashboard.json"
$futurePriceSheet = Join-Path $root "output\dashboard\future-room-prices.json"
$dashboard = Join-Path $root "vercel-dashboard"

if ([string]::IsNullOrWhiteSpace($env:BLOB_READ_WRITE_TOKEN)) {
    $localEnv = Join-Path $dashboard ".env.local"
    if (Test-Path $localEnv) {
        $line = Get-Content $localEnv | Where-Object { $_ -match '^BLOB_READ_WRITE_TOKEN=' } | Select-Object -First 1
        if ($line) { $env:BLOB_READ_WRITE_TOKEN = $line.Substring("BLOB_READ_WRITE_TOKEN=".Length) }
    }
}

if (-not (Test-Path $snapshot)) {
    Write-Host "Dashboard snapshot is not available yet."
    exit 0
}
if (-not (Test-Path $futurePriceSheet)) {
    Write-Host "Future room price sheet is not available yet."
    exit 0
}
if ([string]::IsNullOrWhiteSpace($env:BLOB_READ_WRITE_TOKEN)) {
    Write-Host "Vercel Blob is not configured; local snapshot was updated but not published."
    exit 0
}

Push-Location $dashboard
try {
    $npx = (Get-Command npx.cmd -ErrorAction Stop).Source
    $stdout = Join-Path $env:TEMP ("rateguard-vercel-upload-{0}.out" -f $PID)
    $stderr = Join-Path $env:TEMP ("rateguard-vercel-upload-{0}.err" -f $PID)
    foreach ($item in @(
        @{ Source = $snapshot; Pathname = "data/dashboard.json" },
        @{ Source = $futurePriceSheet; Pathname = "data/future-room-prices.json" }
    )) {
        $arguments = @(
            "vercel", "blob", "put", $item.Source,
            "--pathname", $item.Pathname,
            "--access", "private",
            "--content-type", "application/json",
            "--allow-overwrite",
            "--rw-token", $env:BLOB_READ_WRITE_TOKEN
        )
        $upload = Start-Process -FilePath $npx -ArgumentList $arguments -WorkingDirectory $dashboard `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
        $deadline = (Get-Date).AddSeconds(90)
        while (-not $upload.HasExited -and (Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 2
            $upload.Refresh()
        }
        if (-not $upload.HasExited) {
            Stop-Process -Id $upload.Id -Force -ErrorAction SilentlyContinue
            Write-Error "Vercel Blob upload timed out after 90 seconds: $($item.Pathname)"
            exit 124
        }
        $upload.WaitForExit()
        $exitCode = [int]$upload.ExitCode
        if (Test-Path $stdout) { Get-Content $stdout | Write-Output }
        if (Test-Path $stderr) { Get-Content $stderr | Write-Output }
        if ($exitCode -ne 0) { throw "Vercel Blob upload failed ($exitCode): $($item.Pathname)" }
    }
}
finally {
    Remove-Item $stdout,$stderr -Force -ErrorAction SilentlyContinue
    Pop-Location
}
