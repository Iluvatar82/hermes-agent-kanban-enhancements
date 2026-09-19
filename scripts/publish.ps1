<#
.SYNOPSIS
  Create the public GitHub repository and push this clone to it.

.DESCRIPTION
  Requires the GitHub CLI (winget install GitHub.cli) and `gh auth login`.
  Idempotent: if the repo already exists it just adds the remote and pushes.

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
    throw 'GitHub CLI not found. Install it (winget install GitHub.cli) and run: gh auth login'
}
$user = (gh api user --jq .login).Trim()
if (-not $user) { throw 'gh is not logged in — run: gh auth login' }

$exists = $false
gh repo view "$user/$Name" 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) { $exists = $true }

if ($exists) {
    Write-Host "repo $user/$Name already exists — pushing" -ForegroundColor Cyan
    if (-not (git remote | Select-String -SimpleMatch 'origin')) {
        git remote add origin "https://github.com/$user/$Name.git"
    }
    git push -u origin HEAD
} else {
    gh repo create "$user/$Name" "--$Visibility" --source . --remote origin --push `
        --description 'Board stop/start, live parallel-run cap, board-wide model, timestamped worker logs and a desktop page for the Hermes Agent kanban board.'
}

Write-Host "`nInstall it anywhere with:" -ForegroundColor Cyan
Write-Host "  hermes plugins install $user/$Name/kanban-enhancements --enable"
