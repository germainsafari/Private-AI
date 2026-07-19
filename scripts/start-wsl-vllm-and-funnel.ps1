# Start vLLM in WSL + FastAPI gateway on Windows + Tailscale Funnel.
# Usage (PowerShell):
#   .\scripts\start-wsl-vllm-and-funnel.ps1
#   .\scripts\start-wsl-vllm-and-funnel.ps1 -FunnelTarget vllm   # Funnel :8080 for Vercel→vLLM
#   .\scripts\start-wsl-vllm-and-funnel.ps1 -FunnelTarget gateway # Funnel :3000 (recommended demo)

param(
    [ValidateSet("gateway", "vllm", "none")]
    [string]$FunnelTarget = "gateway",
    [string]$Distro = "Ubuntu",
    [string]$VllmPort = "8080",
    [string]$GatewayPort = "3000",
    [string]$Model = "Qwen/Qwen2.5-VL-7B-Instruct",
    [string]$GpuMemUtil = "0.85",
    # Adjust if your vLLM venv path differs
    [string]$VllmActivate = "~/vllm-env/bin/activate"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$AppDir = Join-Path $RepoRoot "app"

Write-Host "==> Checking / starting vLLM in WSL ($Distro) on port $VllmPort ..." -ForegroundColor Cyan

$check = wsl -d $Distro -- bash -lc "curl -sS -m 2 http://127.0.0.1:$VllmPort/v1/models >/dev/null 2>&1 && echo UP || echo DOWN"
if ($check.Trim() -eq "UP") {
    Write-Host "    vLLM already responding on :$VllmPort" -ForegroundColor Green
} else {
    Write-Host "    Launching vLLM (leaves a WSL process running) ..." -ForegroundColor Yellow
    # Start detached inside WSL; logs to /tmp/vllm-serve.log
    wsl -d $Distro -- bash -lc @"
set -e
source $VllmActivate
nohup vllm serve $Model \
  --served-model-name $Model \
  --host 0.0.0.0 \
  --port $VllmPort \
  --dtype auto \
  --max-model-len 4096 \
  --gpu-memory-utilization $GpuMemUtil \
  --limit-mm-per-prompt '{"image":4}' \
  > /tmp/vllm-serve.log 2>&1 &
echo started pid=\$!
"@
    Write-Host "    Waiting for model to become ready (can take several minutes) ..."
    for ($i = 0; $i -lt 120; $i++) {
        Start-Sleep -Seconds 5
        $ready = wsl -d $Distro -- bash -lc "curl -sS -m 2 http://127.0.0.1:$VllmPort/v1/models >/dev/null 2>&1 && echo UP || echo DOWN"
        if ($ready.Trim() -eq "UP") { break }
        Write-Host "    still loading... ($([int]($i*5))s)"
    }
    if ($ready.Trim() -ne "UP") {
        Write-Host "vLLM did not become ready. Check: wsl -d $Distro -- bash -lc 'tail -n 80 /tmp/vllm-serve.log'" -ForegroundColor Red
        exit 1
    }
    Write-Host "    vLLM ready." -ForegroundColor Green
}

Write-Host "==> Starting FastAPI gateway on Windows :$GatewayPort ..." -ForegroundColor Cyan
$py = Join-Path $AppDir ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "Create the venv first: cd app; python -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt" -ForegroundColor Red
    exit 1
}

$existing = Get-NetTCPConnection -LocalPort ([int]$GatewayPort) -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "    Gateway already listening on :$GatewayPort" -ForegroundColor Green
} else {
    $env:OPENAI_BASE_URL = "http://127.0.0.1:$VllmPort/v1"
    $env:MODEL_NAME = $Model
    $env:MAX_CONCURRENCY = "8"
    Start-Process -FilePath $py -ArgumentList "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", $GatewayPort -WorkingDirectory $AppDir -WindowStyle Minimized
    Start-Sleep -Seconds 2
    Write-Host "    Gateway started." -ForegroundColor Green
}

Write-Host "==> Tailscale Funnel ..." -ForegroundColor Cyan
if ($FunnelTarget -eq "none") {
    Write-Host "    Skipped (-FunnelTarget none)"
} elseif ($FunnelTarget -eq "gateway") {
    tailscale funnel reset 2>$null
    tailscale funnel "http://127.0.0.1:$GatewayPort"
    Write-Host "    Funnel → gateway :$GatewayPort (recommended). Open the HTTPS URL Tailscale printed." -ForegroundColor Green
} else {
    tailscale funnel reset 2>$null
    tailscale funnel "http://127.0.0.1:$VllmPort"
    Write-Host "    Funnel → vLLM :$VllmPort. Set Vercel OPENAI_BASE_URL to https://<host>/v1 and protect with an API key." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Local chat:      http://127.0.0.1:$GatewayPort" -ForegroundColor Cyan
Write-Host "Local dashboard: http://127.0.0.1:$GatewayPort/dashboard" -ForegroundColor Cyan
Write-Host "Funnel status:   tailscale funnel status" -ForegroundColor Cyan
Write-Host "Stop Funnel:     tailscale funnel reset" -ForegroundColor Cyan
