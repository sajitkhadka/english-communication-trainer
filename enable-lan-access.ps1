#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Step 1 of ADR 0006: let the relay reach this PC's API, and nothing else.

.DESCRIPTION
    The API is loopback-only by default, and that default is doing real work:
    `PUT /api/notes` and `DELETE /api/sessions/{id}` are completely unauthenticated.
    Moving it onto the LAN so the relay can proxy to it removes that protection, so
    this script puts a narrower one in its place - an inbound rule on the API port
    scoped to the relay host's address alone.

    That scoping is load-bearing, not hygiene. Read `-WhatIf` output before running it
    for real.

    It also prints the two values the relay's ConfigMap needs (this machine's LAN
    address and its MAC, for Wake-on-LAN) and the `backend/.env` lines that go with
    them, because getting those from `ipconfig` by hand is where this usually goes
    wrong.

.EXAMPLE
    ./enable-lan-access.ps1 -RelayHost 192.168.0.120 -WhatIf
    ./enable-lan-access.ps1 -RelayHost 192.168.0.120          # needs an elevated shell
    ./enable-lan-access.ps1 -RelayHost 192.168.0.120 -Remove
    ./enable-lan-access.ps1 -Report                            # just print, change nothing
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$RelayHost,
    [int]$Port = 8000,
    [string]$RuleName = "ECT API (relay only)",
    [switch]$Remove,
    [switch]$Report
)

$ErrorActionPreference = "Stop"

function Get-LanAdapter {
    # The adapter carrying the default route is the one the relay will reach us on -
    # more reliable than picking the first "Ethernet"-shaped name, which on this
    # machine could be a WSL or Hyper-V virtual switch.
    $route = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue |
        Sort-Object RouteMetric | Select-Object -First 1
    if (-not $route) { return $null }
    Get-NetAdapter -InterfaceIndex $route.InterfaceIndex -ErrorAction SilentlyContinue
}

$adapter = Get-LanAdapter
$address = if ($adapter) {
    (Get-NetIPAddress -InterfaceIndex $adapter.InterfaceIndex -AddressFamily IPv4 `
        -ErrorAction SilentlyContinue | Select-Object -First 1).IPAddress
}

Write-Host "This PC" -ForegroundColor Cyan
Write-Host "  adapter : $(if ($adapter) { "$($adapter.Name) ($($adapter.InterfaceDescription))" } else { 'not found' })"
Write-Host "  address : $(if ($address) { $address } else { 'not found' })"
Write-Host "  MAC     : $(if ($adapter) { $adapter.MacAddress } else { 'not found' })"
Write-Host ""

if ($address) {
    Write-Host "For the relay's ConfigMap (k8s-config/ect-relay/ect-relay-configmap.yaml):" -ForegroundColor Cyan
    Write-Host "  ECT_RELAY_PC_URL: `"http://${address}:$Port`""
    if ($adapter) {
        Write-Host "  ECT_RELAY_WOL_MAC: `"$($adapter.MacAddress.Replace('-', ':').ToLower())`""
    }
    Write-Host ""
    Write-Host "For backend/.env on this machine:" -ForegroundColor Cyan
    Write-Host "  ECT_HOST=$address"
    Write-Host "  ECT_RELAY_URL=https://ect.sajitkhadka.com"
    Write-Host "  ECT_RELAY_TOKEN=<the token you sealed into ect-relay-secrets>"
    Write-Host ""
    Write-Host "Give this machine a DHCP reservation for $address before relying on it -" -ForegroundColor Yellow
    Write-Host "the relay's ConfigMap hard-codes the address, and a moved lease breaks" -ForegroundColor Yellow
    Write-Host "remote capture with nothing useful in any log." -ForegroundColor Yellow
    Write-Host ""
}

if ($Report) { return }

$existing = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue

if ($Remove) {
    if ($existing) {
        if ($PSCmdlet.ShouldProcess($RuleName, "Remove firewall rule")) {
            $existing | Remove-NetFirewallRule
            Write-Host "removed firewall rule '$RuleName'" -ForegroundColor Green
        }
    } else {
        Write-Host "no firewall rule '$RuleName' to remove" -ForegroundColor DarkGray
    }
    Write-Host "Remember to put ECT_HOST back to 127.0.0.1 in backend/.env." -ForegroundColor Yellow
    return
}

if (-not $RelayHost) {
    throw "-RelayHost is required: the rule is scoped to the relay's address, never opened to the LAN."
}
if ($RelayHost -notmatch '^\d{1,3}(\.\d{1,3}){3}$') {
    throw "-RelayHost must be a literal IPv4 address, not a name - firewall scoping resolves nothing at match time."
}

$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin -and -not $WhatIfPreference) {
    throw "creating a firewall rule needs an elevated shell. Re-run this from an Administrator prompt."
}

if ($existing) {
    if ($PSCmdlet.ShouldProcess($RuleName, "Replace existing firewall rule")) {
        $existing | Remove-NetFirewallRule
    }
}

if ($PSCmdlet.ShouldProcess("TCP $Port from $RelayHost only", "Create inbound firewall rule '$RuleName'")) {
    New-NetFirewallRule -DisplayName $RuleName `
        -Description "Inbound to the ECT API from the relay host only (ADR 0006)." `
        -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port `
        -RemoteAddress $RelayHost -Profile Private | Out-Null
    Write-Host "created '$RuleName': TCP $Port inbound, from $RelayHost only" -ForegroundColor Green
}

Write-Host ""
Write-Host "Next:" -ForegroundColor Cyan
Write-Host "  1. put ECT_HOST=<the address above> in backend/.env"
Write-Host "  2. restart the API, then from the server:  curl http://<address>:$Port/api/health"
Write-Host "  3. ./register-agent-task.ps1 -Start"
Write-Host ""
Write-Host "If the profile above is wrong for your network (Public rather than Private)," -ForegroundColor DarkGray
Write-Host "the rule will not match. Get-NetConnectionProfile will tell you which it is." -ForegroundColor DarkGray
