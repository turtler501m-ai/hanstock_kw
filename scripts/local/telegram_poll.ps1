$ErrorActionPreference = "Continue"
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$Root = Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..\..")
Set-Location $Root

New-Item -ItemType Directory -Force -Path ".runtime" | Out-Null
$LogPath = Join-Path $Root ".runtime\telegram_poll.log"

"$(Get-Date -Format o) telegram poll start" | Out-File -FilePath $LogPath -Append -Encoding utf8
python -m src.futures_signals.poll 2>&1 | Out-File -FilePath $LogPath -Append -Encoding utf8
"$(Get-Date -Format o) telegram poll end exit=$LASTEXITCODE" | Out-File -FilePath $LogPath -Append -Encoding utf8
