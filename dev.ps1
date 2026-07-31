#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Run the API and the frontend together, from anywhere.

.DESCRIPTION
    Starts uvicorn in the background and Vite in the foreground, so Ctrl+C stops both.
    Both write to this console; uvicorn lines are prefixed by its own logger, Vite's are
    not, which is enough to tell them apart.

.EXAMPLE
    ./dev.ps1
    ./dev.ps1 -Port 8080          # API on 8080; the Vite proxy follows automatically
    ./dev.ps1 -NoReload           # skip the uvicorn reloader (lower CPU, no auto-restart)
#>
[CmdletBinding()]
param(
    [int]$Port = 8000,
    [switch]$NoReload
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

foreach ($tool in "uv", "npm") {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "$tool is not on PATH. See docs/setup.md."
    }
}

function Get-Listener([int]$p) {
    $conn = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($conn) { Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue }
}

# A leftover server from an earlier run is the failure mode worth catching early: the API
# port silently belongs to stale code, or Vite quietly moves to 5174 and the tab you open
# is the old one. Name the process instead of letting either happen.
$stale = Get-Listener $Port
if ($stale) {
    throw "port $Port is already served by $($stale.ProcessName) (pid $($stale.Id)). " +
          "Stop it first: taskkill /PID $($stale.Id) /T /F"
}
$staleWeb = Get-Listener 5173
if ($staleWeb) {
    Write-Warning ("port 5173 is already served by $($staleWeb.ProcessName) " +
                   "(pid $($staleWeb.Id)) - Vite will start on another port. " +
                   "taskkill /PID $($staleWeb.Id) /T /F to free it.")
}

if (-not (Test-Path "$root/frontend/node_modules")) {
    Write-Host "frontend/node_modules missing - running npm install" -ForegroundColor Yellow
    Push-Location "$root/frontend"
    try { npm install } finally { Pop-Location }
}

$uvArgs = @("run", "uvicorn", "app.main:app", "--port", $Port)
if (-not $NoReload) { $uvArgs += "--reload" }

Write-Host "API      http://127.0.0.1:$Port  (/docs for the API browser)" -ForegroundColor Cyan
Write-Host "frontend http://localhost:5173" -ForegroundColor Cyan
Write-Host "Ctrl+C stops both." -ForegroundColor DarkGray

$backend = Start-Process -FilePath "uv" -ArgumentList $uvArgs `
    -WorkingDirectory "$root/backend" -NoNewWindow -PassThru

try {
    # The Vite proxy has no retry: a request that arrives before uvicorn is listening
    # fails the page rather than waiting, so hold the frontend until /api/health answers.
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline) {
        if ($backend.HasExited) { throw "the API exited during startup (exit $($backend.ExitCode))" }
        try {
            Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2 `
                -UseBasicParsing | Out-Null
            break
        } catch {
            Start-Sleep -Milliseconds 400
        }
    }

    $env:ECT_API_PORT = $Port   # vite.config.ts points its /api proxy at this
    Push-Location "$root/frontend"
    try { npm run dev } finally { Pop-Location }
} finally {
    if ($backend -and -not $backend.HasExited) {
        # /T because `uvicorn --reload` runs the app in a child process: killing only the
        # parent leaves that child holding the port, and the next run fails to bind.
        taskkill /PID $backend.Id /T /F 2>&1 | Out-Null
    }
}
