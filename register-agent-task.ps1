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

# Registered from XML rather than through New-ScheduledTask*, after the cmdlet path
# failed three different ways. The cmdlets emit a Principal with no `id`, and
# `<Actions Context="Author">` has nothing to bind to, so the service rejects the whole
# task with "The parameter is incorrect" pointing at the </Principal> line - which says
# nothing about the actual cause. `Export-ScheduledTask` on any working task shows the
# shape below: id="Author", a SID for the principal, DOMAIN\user for the trigger.
#
# cmd /c with a redirect, because a ScheduledTask action has nowhere to send stdout and
# the log is the only way to see what the drain loop is doing. The redirect characters
# are XML-escaped here, not shell-escaped.
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$taskArgs = "/c uv run python -m app.cli agent run &gt;&gt; &quot;$logPath&quot; 2&gt;&amp;1"

$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Drains the ECT relay inbox and mirrors the offline digest (ADR 0006).</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>$env:USERDOMAIN\$env:USERNAME</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$sid</UserId>
      <LogonType>InteractiveToken</LogonType>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>cmd.exe</Command>
      <Arguments>$taskArgs</Arguments>
      <WorkingDirectory>$backend</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

Register-ScheduledTask -TaskName $TaskName -Xml $xml -Force | Out-Null

# Verify rather than trust. Register-ScheduledTask surfaces some failures as
# non-terminating errors that slip past $ErrorActionPreference, so without this check
# the script cheerfully reports success for a task that does not exist - which is
# exactly what it did while the principal was wrong.
if (-not (Get-Task)) {
    throw "registration reported no error but '$TaskName' does not exist. See above."
}

Write-Host "registered '$TaskName'" -ForegroundColor Green
Write-Host "  relay : $($probe.relay_url)"
Write-Host "  log   : $logPath"
Write-Host "  check : ./register-agent-task.ps1 -Status"

if ($Start) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "started." -ForegroundColor Green
}
