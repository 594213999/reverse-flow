#requires -Version 5.1
<#
.SYNOPSIS
    Optional fallback: run reverse-index sync at logon.
.DESCRIPTION
    The primary trigger is the Reasonix SessionStart hook (install-hook).
    Use this if you also want a sync when Windows logs in, without starting
    Reasonix. Remove with -Remove.
#>
[CmdletBinding()]
param([switch]$Remove)

$ErrorActionPreference = 'Stop'
$taskName = 'ReasonixReverseIndexSync'
$root = Split-Path -Parent $PSScriptRoot
$sync = Join-Path $root 'scripts\sync.ps1'

if ($Remove) {
    schtasks.exe /Delete /TN $taskName /F | Out-Null
    Write-Host "Removed scheduled task: $taskName"
    exit 0
}

if (-not (Test-Path -LiteralPath $sync)) {
    throw "Missing sync script: $sync"
}

# schtasks wants one command line; quote the PowerShell path and script.
$tr = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$sync`""
schtasks.exe /Create /TN $taskName /TR $tr /SC ONLOGON /F | Out-Null
Write-Host "Created scheduled task: $taskName (at logon)"
Write-Host "Remove with: powershell -File `"$PSCommandPath`" -Remove"
