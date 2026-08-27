#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Register (or remove) the scheduled task that runs `ect agent` at logon.

.DESCRIPTION
    `ect agent` drains recordings captured on a phone out of the relay's inbox, keeps
    the relay's picture of whether this PC is up, and mirrors the offline digest
    (docs/adr/0006-remote-capture-local-processing.md).

    It gets its OWN task rather than riding along with dev.ps1, on purpose: its whole
    job is to keep retrying against a local API that may be down, including while you
    are restarting that API. Folded into dev.ps1, every Ctrl+C would silently stop
    remote recordings from ever arriving.

    Runs at logon, not at boot. CUDA under a session-0 service account is a well-known
    failure, and the agent drives transcription through the API in the same session -
    so the safe answer is the one where a real user is logged in. The cost is that
    captures wait until the next logon; Wake-on-LAN plus autologin closes that gap if
    it ever matters.

.EXAMPLE
    ./register-agent-task.ps1               # register, using backend/.env for config
    ./register-agent-task.ps1 -Start        # register and start it now
    ./register-agent-task.ps1 -Remove       # unregister
    ./register-agent-task.ps1 -Status       # is it registered, running, and connected?
#>
[CmdletBinding()]
param(
    [string]$TaskName = "ECT Agent",
    [switch]$Start,
    [switch]$Remove,
    [switch]$Status
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$backend = Join-Path $root "backend"
$logPath = Join-Path $env:LOCALAPPDATA "ect-agent.log"

function Get-Task { Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue }

if ($Remove) {
    if (Get-Task) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "removed scheduled task '$TaskName'" -ForegroundColor Green
    } else {
        Write-Host "no scheduled task '$TaskName' to remove" -ForegroundColor DarkGray
    }
    return
}

if ($Status) {
    $task = Get-Task
    if (-not $task) {
        Write-Host "not registered. Run ./register-agent-task.ps1" -ForegroundColor Yellow
    } else {
        $info = Get-ScheduledTaskInfo -TaskName $TaskName
        Write-Host "task    : $($task.State)"
        Write-Host "last run: $($info.LastRunTime) (result $($info.LastTaskResult))"
        Write-Host "log     : $logPath"
    }
    # The reachability half is the agent's own question to answer, and it reports
    # *which* end is unreachable rather than just failing.
    Push-Location $backend
    try { uv run python -m app.cli agent status } finally { Pop-Location }
    return
}

foreach ($tool in "uv") {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "$tool is not on PATH. See docs/setup.md."
    }
}

# Refuse to register an agent that has nothing to talk to. `ect agent run` would exit 2
# immediately, and a task that dies on every logon is worse than no task: Task Scheduler
# reports it as "ran successfully" the moment the process exits.
Push-Location $backend
try {
    $probe = uv run python -m app.cli agent status | ConvertFrom-Json
} finally {
    Pop-Location
}
if (-not $probe.relay_url) {
    throw ("ECT_RELAY_URL is not set. Put it and ECT_RELAY_TOKEN in backend/.env " +
           "before registering the task - see docs/relay.md.")
}
if (-not $probe.relay_token_set) {
    throw "ECT_RELAY_TOKEN is not set in backend/.env. The relay rejects unauthenticated agents."
}
if ($probe.relay -isnot [pscustomobject]) {
    Write-Warning ("the relay at $($probe.relay_url) is not answering yet ($($probe.relay)). " +
                   "Registering anyway - the agent retries forever, which is the point.")
}

# cmd /c with a redirect, because ScheduledTask actions have nowhere to send stdout and
# the log is the only way to see what the drain loop is doing.
$command = "uv run python -m app.cli agent run >> `"$logPath`" 2>&1"
$action = New-ScheduledTaskAction -Execute "cmd.exe" `
    -Argument "/c $command" -WorkingDirectory $backend

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartInterval (New-TimeSpan -Minutes 1) -RestartCount 3
# The loop is meant to run forever; a time limit would kill it mid-drain.

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited

if (Get-Task) { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false }
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description "Drains the ECT relay inbox and mirrors the offline digest (ADR 0006)." | Out-Null

Write-Host "registered '$TaskName'" -ForegroundColor Green
Write-Host "  relay : $($probe.relay_url)"
Write-Host "  log   : $logPath"
Write-Host "  check : ./register-agent-task.ps1 -Status"

if ($Start) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "started." -ForegroundColor Green
}
