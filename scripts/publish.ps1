<#
.SYNOPSIS
  Create the GitHub repository for this plugin and push this clone to it.

.DESCRIPTION
  Requires the GitHub CLI (winget install GitHub.cli) and `gh auth login`.
  Idempotent: if the repository already exists, it only adds the remote and pushes.

  This file is ASCII-only on purpose and ships with a UTF-8 BOM: Windows
  PowerShell reads a BOM-less script as ANSI, and the mojibake that produces
  from any non-ASCII character can contain a smart quote, which PowerShell
  treats as a real string delimiter.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\publish.ps1
#>
[CmdletBinding()]
param(
    [string] $Name = 'hermes-agent-kanban-enhancements',
    [ValidateSet('public', 'private')] [string] $Visibility = 'public'
)
$ErrorActionPreference = 'Stop'

Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw 'GitHub CLI not found. Install it (winget install GitHub.cli), then run: gh auth login'
}

$user = (gh api user --jq .login)
if ($LASTEXITCODE -ne 0 -or -not $user) {
    throw 'gh is not logged in. Run: gh auth login'
}
$user = $user.Trim()
$slug = "$user/$Name"

gh repo view $slug 2>&1 | Out-Null
$exists = ($LASTEXITCODE -eq 0)

if ($exists) {
    Write-Host "Repository $slug already exists - pushing." -ForegroundColor Cyan
    $remotes = @(git remote)
    if ($remotes -notcontains 'origin') {
        git remote add origin "https://github.com/$slug.git"
    }
    git push -u origin HEAD
} else {
    $description = 'Board stop/start, live parallel-run cap, board-wide model, timestamped worker logs and a desktop page for the Hermes Agent kanban board.'
    $ghArgs = @('repo', 'create', $slug, "--$Visibility", '--source', '.', '--remote', 'origin', '--push', '--description', $description)
    gh @ghArgs
}

if ($LASTEXITCODE -ne 0) {
    throw 'Push failed - see the output above.'
}

Write-Host ''
Write-Host 'Done. Install it on any machine with:' -ForegroundColor Green
Write-Host "  hermes plugins install $slug/kanban-enhancements --enable"
