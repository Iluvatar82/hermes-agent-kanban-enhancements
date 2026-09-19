<#
.SYNOPSIS
  Update this plugin in EVERY Hermes profile at once.

.DESCRIPTION
  Finds the default profile plus every profile under <HermesRoot>\profiles\ that
  has a config.yaml, and re-installs the plugin into each of them:

    hermes [-p <profile>] plugins install <spec> --force --enable

  `--force` is what makes this an update rather than an install: it overwrites
  the installed copy whatever it came from, so it also updates a plugin that
  `hermes plugins update` refuses (a catalog pin, or a copy with no .git).

  One profile failing does not stop the others; the exit code is non-zero when
  any of them did.

  -FromClone installs the working copy beside this script instead of pulling
  from GitHub - that is the one to use while developing a change.

  This file is ASCII-only on purpose and ships with a UTF-8 BOM: Windows
  PowerShell reads a BOM-less script as ANSI, and the mojibake that produces
  from any non-ASCII character can contain a smart quote, which PowerShell
  treats as a real string delimiter.

.PARAMETER Ref
  Pin every profile to one immutable 40-character commit SHA instead of the
  default branch (`hermes plugins install --ref` takes nothing shorter).
  Ignored with -FromClone.

.PARAMETER RestartGateway
  Also restart the gateway at the end. It holds the Python half in memory, so
  it keeps serving the OLD code until it restarts; the desktop half reloads on
  its own.

.EXAMPLE
  pwsh -File scripts\update.ps1

.EXAMPLE
  pwsh -File scripts\update.ps1 -RestartGateway

.EXAMPLE
  pwsh -File scripts\update.ps1 -FromClone -RestartGateway
#>
[CmdletBinding()]
param(
    [string] $HermesRoot = (Join-Path $env:LOCALAPPDATA 'hermes'),
    [string] $Spec = 'Iluvatar82/hermes-agent-kanban-enhancements/kanban-enhancements',
    [string] $Ref,
    [switch] $FromClone,
    [switch] $DefaultOnly,
    [switch] $RestartGateway
)
$ErrorActionPreference = 'Stop'

if (-not (Get-Command hermes -ErrorAction SilentlyContinue)) {
    throw 'hermes not found on PATH.'
}
if (-not (Test-Path $HermesRoot)) {
    throw "Hermes root not found: $HermesRoot (pass -HermesRoot <path>)"
}

<#
.SYNOPSIS
  Run hermes and hand back its exit code instead of throwing.
.DESCRIPTION
  A profile whose install fails must not take the other profiles down with it.
  PowerShell 7.4+ turns a non-zero native exit code into a terminating error
  while $ErrorActionPreference is 'Stop', so it is relaxed for the call itself
  and the exit code is the answer.
#>
function Invoke-Hermes {
    param([string[]] $Arguments)

    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        # Out-Host, not the pipeline: a function returns EVERY object written
        # to its output stream, so a bare call would hand back hermes' console
        # lines with the exit code buried at the end of the array.
        & hermes @Arguments | Out-Host
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
}

# A local clone is a different door: install.ps1 already copies the working tree
# into every profile, so -FromClone just hands over to it.
if ($FromClone) {
    $installer = Join-Path $PSScriptRoot 'install.ps1'
    if (-not (Test-Path $installer)) {
        throw 'install.ps1 not found next to this script - -FromClone needs the repository clone.'
    }
    & $installer -HermesRoot $HermesRoot -DefaultOnly:$DefaultOnly
    if ($RestartGateway) { Invoke-Hermes @('gateway', 'restart') | Out-Null }
    return
}

# The default profile first: the desktop half is app-level and is picked up from there.
$profileNames = @('')
if (-not $DefaultOnly) {
    $profilesDir = Join-Path $HermesRoot 'profiles'
    if (Test-Path $profilesDir) {
        foreach ($dir in Get-ChildItem $profilesDir -Directory) {
            if (Test-Path (Join-Path $dir.FullName 'config.yaml')) { $profileNames += $dir.Name }
        }
    }
}

$refArgs = if ($Ref) { @('--ref', $Ref) } else { @() }
$failed = @()

foreach ($name in $profileNames) {
    # An empty name means "no -p", which is the default profile.
    $label = if ($name) { $name } else { 'default' }
    $profileArgs = if ($name) { @('-p', $name) } else { @() }
    $installArgs = $profileArgs + @('plugins', 'install', $Spec, '--force', '--enable') + $refArgs

    Write-Host "updating profile $label ..." -ForegroundColor Cyan
    if ((Invoke-Hermes $installArgs) -eq 0) {
        Write-Host '  updated' -ForegroundColor Green
    } else {
        $failed += $label
        Write-Host "  FAILED - run it by hand: hermes $($installArgs -join ' ')" -ForegroundColor Red
    }
}

Write-Host ''
if ($failed.Count -gt 0) {
    Write-Host "$($failed.Count) of $($profileNames.Count) profile(s) failed: $($failed -join ', ')" -ForegroundColor Red
} else {
    Write-Host "all $($profileNames.Count) profile(s) updated" -ForegroundColor Green
}

if ($RestartGateway) {
    Write-Host 'restarting the gateway ...' -ForegroundColor Cyan
    if ((Invoke-Hermes @('gateway', 'restart')) -ne 0) {
        Write-Host 'gateway restart failed - run: hermes gateway restart' -ForegroundColor Yellow
    }
} else {
    Write-Host 'Next: hermes gateway restart   (the desktop half loads on its own)' -ForegroundColor Cyan
}

if ($failed.Count -gt 0) { exit 1 }
