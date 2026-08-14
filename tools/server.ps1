param(
    [ValidateSet("start", "stop", "restart", "status", "logs", "tail")]
    [string]$Action = "restart",
    [int]$Port = 8000,
    [int]$Lines = 80,
    [switch]$NoReload
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$RuntimeDir = Join-Path $Root ".runtime"
$PidFile = Join-Path $RuntimeDir "dashboard-server.pid"
$StdoutLog = Join-Path $RuntimeDir "dashboard-server.log"
$StderrLog = Join-Path $RuntimeDir "dashboard-server.err.log"

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null

function Get-PythonPath {
    if ($env:PYTHON -and (Test-Path $env:PYTHON)) {
        return $env:PYTHON
    }

    $venvPython = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        return $venvPython
    }

    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        return $pythonCommand.Source
    }

    throw "python executable not found"
}

function Get-ListeningPids {
    @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique |
        Where-Object { $_ -and $_ -gt 0 })
}

function Get-DashboardPids {
    @(Get-CimInstance Win32_Process |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine -like "*uvicorn*" -and
            $_.CommandLine -like "*src.dashboard:app*" -and
            $_.CommandLine -like "*--port*" -and
            $_.CommandLine -like "*$Port*"
        } |
        Select-Object -ExpandProperty ProcessId -Unique)
}

function Get-PidFilePids {
    if (-not (Test-Path $PidFile)) {
        return @()
    }

    try {
        $content = Get-Content $PidFile -ErrorAction Stop
        return @($content | Where-Object { $_ -match "^\d+$" } | ForEach-Object { [int]$_ })
    } catch {
        return @()
    }
}

function Get-ChildPids([int[]]$ParentIds) {
    $allChildren = @()
    $frontier = @($ParentIds)

    while ($frontier.Count -gt 0) {
        $children = @(Get-CimInstance Win32_Process |
            Where-Object {
                ($frontier -contains $_.ParentProcessId) -and
                ($_.Name -in @("python.exe", "pythonw.exe"))
            } |
            Select-Object -ExpandProperty ProcessId)
        $children = @($children | Where-Object { $allChildren -notcontains $_ })
        $allChildren += $children
        $frontier = $children
    }

    return $allChildren
}

function Get-ServerPids {
    $basePids = @(Get-PidFilePids) + @(Get-ListeningPids) + @(Get-DashboardPids)
    $basePids = @($basePids | Where-Object { $_ -and $_ -gt 0 } | Select-Object -Unique)
    $childPids = @(Get-ChildPids $basePids)
    @($basePids + $childPids | Where-Object { $_ -and $_ -gt 0 } | Select-Object -Unique)
}

function Stop-Server {
    $pids = @(Get-ServerPids)
    if ($pids.Count -eq 0) {
        Write-Host "[server] no dashboard server found on port $Port"
        if (Test-Path $PidFile) {
            Remove-Item -Force $PidFile
        }
        return
    }

    foreach ($processId in $pids) {
        try {
            Stop-Process -Id $processId -Force -ErrorAction Stop
            Write-Host "[server] stopped PID $processId"
        } catch {
            Write-Host "[server] stop skipped PID $processId"
        }
    }

    if (Test-Path $PidFile) {
        Remove-Item -Force $PidFile
    }
}

function Start-Server {
    $listeningPids = @(Get-ListeningPids)
    if ($listeningPids.Count -gt 0) {
        Write-Host "[server] already listening on http://127.0.0.1:$Port (PID $($listeningPids -join ', '))"
        return
    }

    $python = Get-PythonPath
    $args = @("-m", "uvicorn", "src.dashboard:app", "--host", "127.0.0.1", "--port", "$Port")
    if (-not $NoReload) {
        $args += "--reload"
    }

    $process = Start-Process `
        -FilePath $python `
        -ArgumentList $args `
        -WorkingDirectory $Root `
        -RedirectStandardOutput $StdoutLog `
        -RedirectStandardError $StderrLog `
        -WindowStyle Hidden `
        -PassThru

    Set-Content -Path $PidFile -Value $process.Id -Encoding ascii
    Write-Host "[server] started PID $($process.Id) -- http://127.0.0.1:$Port"
    Write-Host "[server] stdout: $StdoutLog"
    Write-Host "[server] stderr: $StderrLog"

    Start-Sleep -Seconds 2
    $listeningPids = @(Get-ListeningPids)
    if ($listeningPids.Count -gt 0) {
        Write-Host "[server] listening PID $($listeningPids -join ', ')"
    } else {
        Write-Host "[server] not listening yet; check logs with: server logs"
    }
}

function Show-Status {
    $listeningPids = @(Get-ListeningPids)
    $serverPids = @(Get-ServerPids)

    if ($listeningPids.Count -gt 0) {
        Write-Host "[server] running: http://127.0.0.1:$Port"
        Write-Host "[server] listening PID: $($listeningPids -join ', ')"
    } else {
        Write-Host "[server] stopped on port $Port"
    }

    if ($serverPids.Count -gt 0) {
        Write-Host "[server] related PID: $($serverPids -join ', ')"
    }
}

function Show-Logs {
    Write-Host "[server] stderr tail: $StderrLog"
    if (Test-Path $StderrLog) {
        Get-Content $StderrLog -Tail $Lines
    }

    Write-Host "[server] stdout tail: $StdoutLog"
    if (Test-Path $StdoutLog) {
        Get-Content $StdoutLog -Tail $Lines
    }
}

function Watch-Logs {
    $paths = @()
    if (Test-Path $StderrLog) {
        $paths += $StderrLog
    }
    if (Test-Path $StdoutLog) {
        $paths += $StdoutLog
    }

    if ($paths.Count -eq 0) {
        Write-Host "[server] no log files found"
        return
    }

    Write-Host "[server] following logs. Press Ctrl+C to stop watching; the server keeps running."
    Write-Host "[server] stderr: $StderrLog"
    Write-Host "[server] stdout: $StdoutLog"
    Get-Content $paths -Tail $Lines -Wait
}

switch ($Action) {
    "start" {
        Start-Server
    }
    "stop" {
        Stop-Server
    }
    "restart" {
        Stop-Server
        Start-Sleep -Seconds 1
        Start-Server
    }
    "status" {
        Show-Status
    }
    "logs" {
        Show-Logs
    }
    "tail" {
        Watch-Logs
    }
}
