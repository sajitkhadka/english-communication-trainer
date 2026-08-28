#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Copy the recordings to the home server and confirm they arrived intact.

.DESCRIPTION
    Recordings are the one artifact the data repo does not hold (ADR 0008), so this is
    what stands in for `git push` for them. Four steps, and the order matters:

      1. `ect archive track`   - hash anything new, so there is something to check against
      2. write a `sha256sum -c` manifest next to the recordings
      3. `rclone copy`         - COPY, never sync: this must not delete on the far end
      4. `sha256sum -c` over SSH, and only then `ect archive synced`

    Step 4 is the point of the whole script. `rclone copy` exiting 0 means the transfer
    ran without erroring, not that the bytes on the server are the bytes on this disk.
    `synced_at` is what later licenses deleting a local copy, so it is set from an actual
    check on the far end rather than from the transfer's exit code.

.PARAMETER Remote
    rclone remote and path, e.g. "homeserver:/srv/ect-recordings". Falls back to
    $env:ECT_ARCHIVE_REMOTE.

.PARAMETER SshTarget
    user@host for the verify step, e.g. "sajit@192.168.1.50". Falls back to
    $env:ECT_ARCHIVE_SSH. Must reach the same directory the remote writes to.

.PARAMETER RemotePath
    Absolute path on the server, used by the SSH verify step. Defaults to the path half
    of -Remote.

.EXAMPLE
    ./backup-recordings.ps1 -Remote homeserver:/srv/ect-recordings -SshTarget sajit@192.168.1.50
    ./backup-recordings.ps1 -DryRun          # show what would move, change nothing
    ./backup-recordings.ps1 -SkipVerify      # copy only; does NOT mark anything synced
#>
[CmdletBinding()]
param(
    [string]$Remote = $env:ECT_ARCHIVE_REMOTE,
    [string]$SshTarget = $env:ECT_ARCHIVE_SSH,
    [string]$RemotePath,
    [switch]$DryRun,
    [switch]$SkipVerify
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$recordings = Join-Path $root "data/recordings"
$manifestName = ".manifest.sha256"
$manifest = Join-Path $recordings $manifestName

foreach ($tool in "uv", "rclone") {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "$tool not found on PATH."
    }
}
if (-not $Remote) {
    throw "No remote. Pass -Remote homeserver:/srv/ect-recordings, or set ECT_ARCHIVE_REMOTE."
}
if (-not (Test-Path $recordings)) { throw "No recordings directory at $recordings" }

if (-not $RemotePath -and $Remote -match "^[^:]+:(.+)$") { $RemotePath = $Matches[1] }

Push-Location (Join-Path $root "backend")
try {
    Write-Host "==> hashing anything new" -ForegroundColor Cyan
    uv run ect archive track | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "ect archive track failed" }

    Write-Host "==> writing $manifestName" -ForegroundColor Cyan
    uv run ect archive manifest --format sha256 --out $manifest
    if ($LASTEXITCODE -ne 0) { throw "ect archive manifest failed" }
}
finally { Pop-Location }

# --checksum, not size-and-mtime: a half-written file from an interrupted run has a
# plausible size and a fresh mtime, and is exactly what this is supposed to catch.
# `copy` rather than `sync` - the far end is an archive, so nothing there is ever
# deleted because it is no longer here.
$rcloneArgs = @(
    "copy", $recordings, $Remote,
    "--checksum",
    "--progress",
    "--exclude", "*.partial"
)
if ($DryRun) { $rcloneArgs += "--dry-run" }

Write-Host "==> rclone copy -> $Remote" -ForegroundColor Cyan
& rclone @rcloneArgs
if ($LASTEXITCODE -ne 0) { throw "rclone copy failed - nothing marked synced" }

if ($DryRun) {
    Write-Host "dry run: nothing copied, nothing marked synced." -ForegroundColor Yellow
    return
}

if ($SkipVerify) {
    Write-Host "-SkipVerify: copied, but NOT marked synced." -ForegroundColor Yellow
    Write-Host "Nothing is safe to delete locally until a verify has passed." -ForegroundColor Yellow
    return
}
if (-not $SshTarget) {
    Write-Host "No -SshTarget, so the far end was not checked and nothing was marked synced." -ForegroundColor Yellow
    Write-Host "Verify by hand, then run: ect archive synced --target '$Remote'" -ForegroundColor Yellow
    return
}

Write-Host "==> verifying on $SshTarget" -ForegroundColor Cyan
# `--quiet` prints only failures; the exit code carries the verdict.
$check = "cd '$RemotePath' && sha256sum --quiet -c '$manifestName'"
& ssh $SshTarget $check
$verified = $LASTEXITCODE -eq 0

if (-not $verified) {
    Write-Host ""
    Write-Host "VERIFY FAILED - the files above differ or are missing on the server." -ForegroundColor Red
    Write-Host "Nothing has been marked synced. Do not delete any local recording." -ForegroundColor Red
    exit 1
}

Push-Location (Join-Path $root "backend")
try {
    uv run ect archive synced --target $Remote | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "verified, but recording that failed" }
}
finally { Pop-Location }

Write-Host ""
Write-Host "Verified on the server and recorded as synced." -ForegroundColor Green
Push-Location (Join-Path $root "backend")
try { uv run ect archive status | Select-Object -Last 4 }
finally { Pop-Location }
