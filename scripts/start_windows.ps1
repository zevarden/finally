<#
.SYNOPSIS
    Start the FinAlly container (Windows PowerShell). Idempotent: safe to run repeatedly.
.PARAMETER Build
    Force a rebuild of the Docker image.
.PARAMETER NoOpen
    Don't open the browser.
.EXAMPLE
    .\scripts\start_windows.ps1 -Build
#>
[CmdletBinding()]
param(
    [switch]$Build,
    [switch]$NoOpen
)

$ErrorActionPreference = 'Stop'

$Image = 'finally'
$Container = 'finally'
$Volume = 'finally-data'
$Port = if ($env:FINALLY_PORT) { $env:FINALLY_PORT } else { '8000' }
$Url = "http://localhost:$Port"
# Health probe uses 127.0.0.1: "localhost" may resolve to ::1 first, which can hang on Docker Desktop.

$RootDir = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $RootDir '.env'
$EnvExample = Join-Path $RootDir '.env.example'

# Run docker quietly and report success. EAP is relaxed locally because Windows
# PowerShell 5.1 turns redirected native stderr into terminating errors under 'Stop'.
function Test-DockerOk {
    param([string[]]$DockerArgs)
    $ErrorActionPreference = 'Continue'
    & docker @DockerArgs *> $null
    return ($LASTEXITCODE -eq 0)
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error 'docker is not installed or not on PATH.'
    exit 1
}
if (-not (Test-DockerOk @('info'))) {
    Write-Error 'The Docker daemon is not running. Start Docker Desktop and try again.'
    exit 1
}

if (-not (Test-Path $EnvFile)) {
    if (Test-Path $EnvExample) {
        Write-Warning '.env not found; creating it from .env.example. Edit .env to add your LITELLM_MASTER_KEY for AI chat.'
        Copy-Item $EnvExample $EnvFile
    } else {
        Write-Error "Neither .env nor .env.example exists in $RootDir."
        exit 1
    }
}

if ($Build -or -not (Test-DockerOk @('image', 'inspect', $Image))) {
    Write-Host "Building Docker image '$Image'..."
    & docker build -t $Image $RootDir
    if ($LASTEXITCODE -ne 0) { Write-Error 'docker build failed.'; exit 1 }
}

if (Test-DockerOk @('container', 'inspect', $Container)) {
    Write-Host "Removing existing container '$Container'..."
    if (-not (Test-DockerOk @('rm', '-f', $Container))) { Write-Error "Could not remove container '$Container'."; exit 1 }
}

Write-Host "Starting container '$Container'..."
& docker run -d --name $Container --add-host=host.docker.internal:host-gateway -v "${Volume}:/app/db" -p "${Port}:8000" --env-file $EnvFile $Image | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Error 'docker run failed.'; exit 1 }

Write-Host -NoNewline 'Waiting for FinAlly to become ready'
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/health" -UseBasicParsing -TimeoutSec 2
        if ($resp.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
    Write-Host -NoNewline '.'
    Start-Sleep -Seconds 1
}
Write-Host ''

if ($ready) {
    Write-Host "FinAlly is running at $Url"
} else {
    Write-Host "FinAlly is starting at $Url (not healthy yet; check: docker logs $Container)"
}

if (-not $NoOpen) {
    Start-Process $Url | Out-Null
}

exit 0
