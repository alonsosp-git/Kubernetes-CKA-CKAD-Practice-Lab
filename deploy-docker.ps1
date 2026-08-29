# One-command Docker deployment for k8s-practice-lab (Windows PowerShell).
param([int]$Port = 8899)
$ErrorActionPreference = "Stop"
$image = "k8s-practice-lab"; $name = "k8s-lab"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "docker is not installed or not on PATH"; exit 1
}
Write-Host "==> building $image"
docker build -t $image $PSScriptRoot
Write-Host "==> (re)starting $name on port $Port"
docker rm -f $name 2>$null | Out-Null
docker run -d --name $name -p "${Port}:8899" $image | Out-Null
Write-Host "==> waiting for the app"
for ($i = 0; $i -lt 30; $i++) {
    try {
        Invoke-WebRequest "http://localhost:$Port/api/state" -UseBasicParsing -TimeoutSec 2 | Out-Null
        Write-Host "==> ready:  http://localhost:$Port"
        Start-Process "http://localhost:$Port"
        exit 0
    } catch { Start-Sleep -Seconds 1 }
}
Write-Error "the container did not become healthy; check: docker logs $name"
