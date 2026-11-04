<#
.SYNOPSIS
    Builds standalone Python runtime-bundled packages for Windows, CentOS, and Ubuntu.
.DESCRIPTION
    1. Builds `dist/releases/vdl-windows-x86_64.zip` locally using `.venv`.
    2. If Docker is available (and -SkipDocker is not set), builds:
       - `dist/releases/vdl-centos-x86_64.tar.gz` (via docker/Dockerfile.centos)
       - `dist/releases/vdl-ubuntu-x86_64.tar.gz` (via docker/Dockerfile.ubuntu)
#>
param(
    [switch]$SkipDocker
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

New-Item -ItemType Directory -Force -Path "$ProjectRoot\dist\releases" | Out-Null

Write-Host "=== [1/3] Building Windows x86_64 Package ===" -ForegroundColor Cyan
& "$ProjectRoot\.venv\Scripts\python.exe" build.py --clean --archive --target-name vdl-windows-x86_64

if (-not $SkipDocker) {
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        Write-Host "=== [2/3] Building CentOS x86_64 Package via Docker ===" -ForegroundColor Cyan
        docker build -f docker/Dockerfile.centos -t vdl-builder-centos .
        $cidCentos = docker create vdl-builder-centos
        docker cp "${cidCentos}:/workspace/dist/releases/vdl-centos-x86_64.tar.gz" "$ProjectRoot\dist\releases\vdl-centos-x86_64.tar.gz"
        docker rm $cidCentos | Out-Null

        Write-Host "=== [3/3] Building Ubuntu x86_64 Package via Docker ===" -ForegroundColor Cyan
        docker build -f docker/Dockerfile.ubuntu -t vdl-builder-ubuntu .
        $cidUbuntu = docker create vdl-builder-ubuntu
        docker cp "${cidUbuntu}:/workspace/dist/releases/vdl-ubuntu-x86_64.tar.gz" "$ProjectRoot\dist\releases\vdl-ubuntu-x86_64.tar.gz"
        docker rm $cidUbuntu | Out-Null
    } else {
        Write-Warning "Docker was not found in PATH. Skipping local Linux (CentOS/Ubuntu) builds. Push a `v*` tag to GitHub to build all 3 OS packages via GitHub Actions."
    }
}

Write-Host "`nGenerated Release Archives in dist/releases/:" -ForegroundColor Green
Get-ChildItem "$ProjectRoot\dist\releases"

