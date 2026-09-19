<#
.SYNOPSIS
  Install this plugin from a local clone into one or more Hermes profiles.

.DESCRIPTION
  Copies kanban-enhancements/ into <HERMES_HOME>/plugins/ for the default profile
  and (unless -DefaultOnly) every profile under profiles/, then enables it.
  The online equivalent is:
    hermes plugins install spitefr/hermes-agent-kanban-enhancements/kanban-enhancements --enable

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#>
[CmdletBinding()]
param(
    [string] $HermesRoot = (Join-Path $env:LOCALAPPDATA 'hermes'),
    [switch] $DefaultOnly,
    [switch] $NoEnable
)
$ErrorActionPreference = 'Stop'

$source = Join-Path (Split-Path -Parent $PSScriptRoot) 'kanban-enhancements'
if (-not (Test-Path (Join-Path $source 'plugin.yaml'))) {
    throw "plugin.yaml not found in $source - run this from the repository clone."
}
if (-not (Test-Path $HermesRoot)) {
    throw "Hermes root not found: $HermesRoot (pass -HermesRoot <path>)"
}

# The default profile first: the desktop half is app-level and is picked up from there.
$targets = @([pscustomobject]@{ Name = 'default'; Root = $HermesRoot })
if (-not $DefaultOnly) {
    $profilesDir = Join-Path $HermesRoot 'profiles'
    if (Test-Path $profilesDir) {
        foreach ($dir in Get-ChildItem $profilesDir -Directory) {
            if (Test-Path (Join-Path $dir.FullName 'config.yaml')) {
                $targets += [pscustomobject]@{ Name = $dir.Name; Root = $dir.FullName }
            }
        }
    }
}

foreach ($target in $targets) {
    $dest = Join-Path $target.Root 'plugins\kanban-enhancements'
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
    if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
    Copy-Item $source $dest -Recurse
    # __pycache__ from a previous version would shadow renamed modules.
    Get-ChildItem $dest -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
    Write-Host "installed -> $dest" -ForegroundColor Green

    if (-not $NoEnable) {
        $profileArgs = if ($target.Name -eq 'default') { @() } else { @('-p', $target.Name) }
        & hermes @profileArgs plugins enable kanban-enhancements 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "enabled for profile $($target.Name)" -ForegroundColor Green
        } else {
            Write-Host "could not enable for profile $($target.Name) - run: hermes $($profileArgs -join ' ') plugins enable kanban-enhancements" -ForegroundColor Yellow
        }
    }
}

Write-Host "`nNext: hermes gateway restart   (the desktop half loads on its own)" -ForegroundColor Cyan
