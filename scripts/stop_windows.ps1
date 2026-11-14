<#
.SYNOPSIS
    Stop and remove the FinAlly container (Windows PowerShell). Idempotent.
    The 'finally-data' volume is kept, so your portfolio persists.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Container = 'finally'

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error 'docker is not installed or not on PATH.'
    exit 1
}

# Relaxed: Windows PowerShell 5.1 turns redirected native stderr into errors under 'Stop'.
$ErrorActionPreference = 'Continue'
& docker container inspect $Container *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Host "Stopping and removing container '$Container'..."
    & docker rm -f $Container *> $null
    if ($LASTEXITCODE -ne 0) { Write-Error "Could not remove container '$Container'."; exit 1 }
    Write-Host "Stopped. Data volume 'finally-data' was kept."
} else {
    Write-Host "Container '$Container' is not running. Nothing to do."
}

exit 0
