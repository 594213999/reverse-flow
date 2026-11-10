#requires -Version 5.1
<#
.SYNOPSIS
    Sync the local reverse-index subset from GitHub.
.DESCRIPTION
    Thin wrapper for re_index.py sync --hook. Safe to call from Task Scheduler:
    never throws, always exits 0, keeps old data on failure.
#>
[CmdletBinding()]
param(
    [string]$Source,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$script = Join-Path $root 'scripts\re_index.py'
$log = Join-Path $root 'sync.log'

$python = $null
$cmd = Get-Command python -ErrorAction SilentlyContinue
if ($cmd) { $python = $cmd.Source }
if (-not $python) {
    $fallback = 'C:\Python313\python.exe'
    if (Test-Path -LiteralPath $fallback) { $python = $fallback }
}
if (-not $python) {
    "[$(Get-Date -Format o)] sync.ps1: python not found" | Add-Content -LiteralPath $log -Encoding UTF8
    exit 0
}

$args = @($script, 'sync', '--hook', '--max-age', '3600', '--quiet')
if ($Source) { $args += @('--source', $Source) }
if ($Force) { $args += '--force' }

try {
    & $python @args
    exit 0
}
catch {
    "[$(Get-Date -Format o)] sync.ps1: $($_.Exception.Message)" | Add-Content -LiteralPath $log -Encoding UTF8
    exit 0
}
