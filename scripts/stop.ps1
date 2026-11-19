<#
    Stop the stack started by start.bat.

    Primary path: kill the cmd.exe trees recorded in <repo>\.run\*.pid
    (taskkill /T also kills the python / node children of each window).
    Fallback: stop any window whose title still starts with PM-API / PM-Worker / PM-Web.
#>
#requires -Version 5.1
[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'

$Root = Split-Path -Parent $PSScriptRoot
$RunDir = Join-Path $Root '.run'

$names = @('api', 'worker', 'web')
$stopped = 0

foreach ($name in $names) {
    $pidFile = Join-Path $RunDir ($name + '.pid')
    if (-not (Test-Path $pidFile)) { continue }
    $raw = (Get-Content $pidFile -Raw).Trim()
    if ($raw) {
        & taskkill.exe /PID $raw /T /F 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host ('[+] stopped ' + $name + ' (pid ' + $raw + ')') -ForegroundColor Green
            $stopped++
        } else {
            Write-Host ('[~] ' + $name + ' (pid ' + $raw + ') was not running') -ForegroundColor Yellow
        }
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

# Fallback: window-title based cleanup (covers manual launches / lost pid files).
Get-Process -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowTitle -match '^PM-(API|Worker|Web)' } |
    ForEach-Object {
        Write-Host ('[~] closing leftover window "' + $_.MainWindowTitle + '" (pid ' + $_.Id + ')') -ForegroundColor Yellow
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
        $stopped++
    }

# Fallback: orphaned python uvicorn / worker processes belonging to this repo.
$rootPattern = [regex]::Escape($Root)
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match $rootPattern -and $_.CommandLine -match 'uvicorn|workers\.run_worker' } |
    ForEach-Object {
        Write-Host ('[~] killing orphaned python pid ' + $_.ProcessId) -ForegroundColor Yellow
        & taskkill.exe /PID $_.ProcessId /T /F 2>$null | Out-Null
        $stopped++
    }

Remove-Item (Join-Path $RunDir 'api.port') -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $RunDir 'web.port') -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $RunDir 'worker.port') -Force -ErrorAction SilentlyContinue

Write-Host ''
if ($stopped -gt 0) {
    Write-Host ('[+] stop complete (' + $stopped + ' process group(s) handled)') -ForegroundColor Green
} else {
    Write-Host '[i] nothing was running' -ForegroundColor DarkGray
}
Write-Host ''
