param(
    [string]$WorkingDirectory = "",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ForwardArgs = @()
)

$ErrorActionPreference = "Stop"

if ($WorkingDirectory -and (Test-Path -LiteralPath $WorkingDirectory)) {
    Set-Location -LiteralPath $WorkingDirectory
}

$projectRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")
$localCodexScript = Join-Path $projectRoot "codex-c.ps1"

if (-not (Test-Path -LiteralPath $localCodexScript)) {
    Write-Warning "Could not find local script: $localCodexScript"
    exit 1
}

$args = @("--dangerously-bypass-approvals-and-sandbox")
if ($ForwardArgs.Count -gt 0) {
    $args += $ForwardArgs
}

Write-Host "`n=== Starting CLI in YOLO Mode ===" -ForegroundColor Cyan
Write-Host "Script: $localCodexScript" -ForegroundColor Yellow
Write-Host "WorkingDirectory: $(Get-Location)" -ForegroundColor Yellow
Write-Host "Note: Confirmations for file edits and terminal executions will be skipped.`n" -ForegroundColor DarkGray

& $localCodexScript @args
