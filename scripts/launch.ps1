<#
    PM Brainstorming Workbench - one-click launcher (Windows).

    Starts three services in their own console windows and waits until both HTTP
    endpoints answer:

        PM-API    : uvicorn main:app            (FastAPI, /api/v2)
        PM-Worker : python -m workers.run_worker (Run worker, lease based)
        PM-Web    : next dev                     (Next.js frontend)

    PIDs are written to <repo>\.run\*.pid so stop.bat can shut the stack down.
    Ports default to 8001 / 3001 because 8000 / 3000 are frequently taken by
    other local tools; a busy port is skipped automatically.
#>
#requires -Version 5.1
[CmdletBinding()]
param(
    [int]$ApiPort = 8001,
    [int]$WebPort = 3001,
    [switch]$NoBrowser,
    [switch]$SkipWorker,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root '.venv\Scripts\python.exe'
$Backend = Join-Path $Root 'backend'
$Frontend = Join-Path $Root 'frontend'
$RunDir = Join-Path $Root '.run'

function Write-Step([string]$Text) { Write-Host ('[*] ' + $Text) -ForegroundColor Cyan }
function Write-Ok([string]$Text) { Write-Host ('[+] ' + $Text) -ForegroundColor Green }
function Write-Warn2([string]$Text) { Write-Host ('[~] ' + $Text) -ForegroundColor Yellow }
function Write-Err2([string]$Text) { Write-Host ('[!] ' + $Text) -ForegroundColor Red }

function Test-PortBusy([int]$Port) {
    try {
        $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop
        if ($connections) { return $true }
        return $false
    } catch {
        return $false
    }
}

function Find-FreePort([int]$Start) {
    $port = $Start
    $limit = $Start + 50
    while ($port -lt $limit) {
        if (-not (Test-PortBusy $port)) { return $port }
        $port++
    }
    throw ('No free port found in range ' + $Start + '-' + $limit)
}

function Wait-Http([string]$Url, [int]$TimeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        & curl.exe -s -f -m 3 --noproxy '*' $Url > $null 2>&1
        if ($LASTEXITCODE -eq 0) { return $true }
        Start-Sleep -Milliseconds 700
    }
    return $false
}

function Get-AlivePid([string]$File) {
    if (-not (Test-Path $File)) { return 0 }
    $raw = (Get-Content $File -Raw).Trim()
    if (-not $raw) { return 0 }
    $value = 0
    if (-not [int]::TryParse($raw, [ref]$value)) { return 0 }
    $proc = Get-Process -Id $value -ErrorAction SilentlyContinue
    if ($proc) { return $value }
    return 0
}

function New-Launcher([string]$Name, [string]$Title, [string]$WorkDir, [string[]]$Lines) {
    $path = Join-Path $RunDir ($Name + '.cmd')
    $body = @(
        '@echo off',
        'chcp 65001>nul',
        ('title ' + $Title),
        ('cd /d "' + $WorkDir + '"')
    ) + $Lines + @(
        '',
        'echo.',
        ('echo [' + $Name + '] process exited. Press any key to close this window.'),
        'pause>nul'
    )
    Set-Content -Path $path -Value $body -Encoding ASCII
    return $path
}

function Start-ServiceWindow([string]$Launcher, [string]$WorkDir) {
    $cmd = 'cmd.exe'
    $argv = @('/k', ('"' + $Launcher + '"'))
    return Start-Process -FilePath $cmd -ArgumentList $argv -WorkingDirectory $WorkDir -PassThru
}

Write-Host ''
Write-Host '=============================================' -ForegroundColor DarkCyan
Write-Host '  PM Brainstorming Workbench / 一键启动' -ForegroundColor Cyan
Write-Host '=============================================' -ForegroundColor DarkCyan

# ---------------------------------------------------------------- preflight
Write-Step ('Repo root : ' + $Root)

if (-not (Test-Path $Py)) {
    Write-Err2 ('Missing Python venv: ' + $Py)
    Write-Err2 'Create it first:  python -m venv .venv  then  .venv\Scripts\pip install -r backend\requirements.txt'
    exit 1
}

Write-Step 'Checking backend dependencies ...'
Push-Location $Backend
& $Py -c "import fastapi, sqlalchemy, pydantic_settings" 2>$null
$depCode = $LASTEXITCODE
Pop-Location
if ($depCode -ne 0) {
    Write-Err2 'Backend dependencies are incomplete (fastapi / sqlalchemy / pydantic-settings).'
    Write-Err2 ('Run:  ' + $Py + ' -m pip install -r backend\requirements.txt')
    exit 1
}

if (-not (Test-Path (Join-Path $Frontend 'node_modules'))) {
    Write-Warn2 'frontend\node_modules is missing - the web UI will be skipped.'
    Write-Warn2 'Run:  cd frontend  &&  npm ci'
    $webEnabled = $false
} else {
    $webEnabled = $true
}

if (-not (Test-Path (Join-Path $Backend '.env'))) {
    Write-Warn2 'backend\.env not found - the API may start with empty keys (health will read "degraded").'
}

if (-not (Test-Path $RunDir)) { New-Item -ItemType Directory -Path $RunDir | Out-Null }

# ------------------------------------------------- already running? (idempotent)
$apiPidFile = Join-Path $RunDir 'api.pid'
$webPidFile = Join-Path $RunDir 'web.pid'
$apiPortFile = Join-Path $RunDir 'api.port'
$webPortFile = Join-Path $RunDir 'web.port'
$runningApiPid = Get-AlivePid $apiPidFile
$runningWebPid = Get-AlivePid $webPidFile

if (-not $Force -and $runningApiPid -gt 0) {
    $knownApiPort = 8000
    if (Test-Path $apiPortFile) { $knownApiPort = [int]((Get-Content $apiPortFile -Raw).Trim()) }
    if (Test-PortBusy $knownApiPort) {
        Write-Ok ('Already running: API pid ' + $runningApiPid + ' on port ' + $knownApiPort)
        if ($webEnabled -and $runningWebPid -gt 0) {
            $knownWebPort = 3000
            if (Test-Path $webPortFile) { $knownWebPort = [int]((Get-Content $webPortFile -Raw).Trim()) }
            Write-Ok ('Already running: Web pid ' + $runningWebPid + ' on port ' + $knownWebPort)
            if (-not $NoBrowser) { Start-Process ('http://127.0.0.1:' + $knownWebPort) }
        }
        Write-Host ''
        Write-Host ('  Open    : http://127.0.0.1:' + $knownWebPort) -ForegroundColor White
        Write-Host ('  API doc : http://127.0.0.1:' + $knownApiPort + '/docs') -ForegroundColor White
        Write-Host '  Stop it : stop.bat' -ForegroundColor DarkGray
        exit 0
    }
    Write-Warn2 'Stale pid file found but the recorded port is closed - starting fresh.'
}

# ---------------------------------------------------------------- ports
if (Test-PortBusy $ApiPort) {
    $free = Find-FreePort ($ApiPort + 1)
    Write-Warn2 ('Port ' + $ApiPort + ' is in use by another program - API will use ' + $free + ' instead.')
    $ApiPort = $free
}
if ($webEnabled -and (Test-PortBusy $WebPort)) {
    $freeWeb = Find-FreePort ($WebPort + 1)
    Write-Warn2 ('Port ' + $WebPort + ' is in use by another program - Web will use ' + $freeWeb + ' instead.')
    $WebPort = $freeWeb
}

# ---------------------------------------------------------------- database
Write-Step 'Initialising SQLite database (idempotent create_all) ...'
Push-Location $Backend
& $Py -c "from repositories.database import initialize_database; initialize_database(); print('db-ready')" 2>&1 | ForEach-Object { Write-Host ('    ' + $_) -ForegroundColor DarkGray }
$dbCode = $LASTEXITCODE
Pop-Location
if ($dbCode -ne 0) {
    Write-Err2 'Database initialisation failed - see the message above.'
    exit 1
}

# ---------------------------------------------------------------- launchers
$corsOrigins = 'http://localhost:' + $WebPort + ',http://127.0.0.1:' + $WebPort + ',http://localhost:' + $ApiPort + ',http://127.0.0.1:' + $ApiPort
$apiLauncher = New-Launcher -Name 'api' -Title ('PM-API :' + $ApiPort) -WorkDir $Backend -Lines @(
    ('set "CORS_ORIGINS=' + $corsOrigins + '"'),
    ('"' + $Py + '" -m uvicorn main:app --host 127.0.0.1 --port ' + $ApiPort)
)

$workerLines = @(
    ('"' + $Py + '" -m workers.run_worker --interval 1')
)
$workerLauncher = New-Launcher -Name 'worker' -Title 'PM-Worker' -WorkDir $Backend -Lines $workerLines

$webLauncher = $null
if ($webEnabled) {
    $webLauncher = New-Launcher -Name 'web' -Title ('PM-Web :' + $WebPort) -WorkDir $Frontend -Lines @(
        ('set "NEXT_PUBLIC_API_URL=http://127.0.0.1:' + $ApiPort + '"'),
        ('call npm run dev -- --hostname 127.0.0.1 --port ' + $WebPort)
    )
}

# ---------------------------------------------------------------- start
Write-Step ('Starting API on 127.0.0.1:' + $ApiPort + ' ...')
$apiProc = Start-ServiceWindow -Launcher $apiLauncher -WorkDir $Backend
Set-Content -Path $apiPidFile -Value $apiProc.Id -Encoding ASCII
Set-Content -Path $apiPortFile -Value $ApiPort -Encoding ASCII

if (-not $SkipWorker) {
    Write-Step 'Starting Worker ...'
    $workerProc = Start-ServiceWindow -Launcher $workerLauncher -WorkDir $Backend
    Set-Content -Path (Join-Path $RunDir 'worker.pid') -Value $workerProc.Id -Encoding ASCII
    Set-Content -Path (Join-Path $RunDir 'worker.port') -Value 0 -Encoding ASCII
}

if ($webEnabled) {
    Write-Step ('Starting Web on 127.0.0.1:' + $WebPort + ' ...')
    $webProc = Start-ServiceWindow -Launcher $webLauncher -WorkDir $Frontend
    Set-Content -Path $webPidFile -Value $webProc.Id -Encoding ASCII
    Set-Content -Path $webPortFile -Value $WebPort -Encoding ASCII
}

# ---------------------------------------------------------------- wait
Write-Step 'Waiting for API health ...'
Write-Host '    ' -NoNewline
$apiReady = Wait-Http -Url ('http://127.0.0.1:' + $ApiPort + '/health') -TimeoutSeconds 120
if (-not $apiReady) {
    Write-Host ''
    Write-Err2 'API did not answer within 120s. Check the "PM-API" window for traceback.'
    exit 1
}
Write-Ok ('API ready  -  http://127.0.0.1:' + $ApiPort + '/health')

if ($webEnabled) {
    Write-Step 'Waiting for Web (first Next.js compile can take a while) ...'
    $webReady = Wait-Http -Url ('http://127.0.0.1:' + $WebPort + '/') -TimeoutSeconds 180
    if (-not $webReady) {
        Write-Host ''
        Write-Err2 'Web did not answer within 180s. Check the "PM-Web" window for errors.'
        exit 1
    }
    Write-Ok ('Web ready  -  http://127.0.0.1:' + $WebPort)
}

Write-Host ''
Write-Host '=============================================' -ForegroundColor DarkCyan
Write-Host '  All services are up' -ForegroundColor Green
Write-Host '=============================================' -ForegroundColor DarkCyan
if ($webEnabled) { Write-Host ('  Frontend : http://127.0.0.1:' + $WebPort) -ForegroundColor White }
Write-Host ('  API      : http://127.0.0.1:' + $ApiPort + '/health') -ForegroundColor White
Write-Host ('  API docs : http://127.0.0.1:' + $ApiPort + '/docs') -ForegroundColor White
Write-Host '  Worker   : running (window "PM-Worker")' -ForegroundColor White
Write-Host ''
Write-Host '  Each service runs in its own console window; close it or run stop.bat to stop.' -ForegroundColor DarkGray
Write-Host ''

if ($webEnabled -and -not $NoBrowser) {
    Write-Step 'Opening browser ...'
    Start-Process ('http://127.0.0.1:' + $WebPort)
}

exit 0
