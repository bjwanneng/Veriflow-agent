#!/usr/bin/env pwsh
# Veriflow Gateway Starter
# Activate venv -> Install deps -> Check port -> Start service -> Open browser

param(
    [int]$Port = 18789,
    [string]$HostAddr = "127.0.0.1",
    [switch]$SkipInstall,
    [switch]$Verbose
)

$ErrorActionPreference = "Continue"

# -- Utility Functions --

function Write-Status {
    param([string]$Message, [string]$Color = "White")
    Write-Host $Message -ForegroundColor $Color
}

function Test-CommandExists {
    param([string]$Command)
    return [bool](Get-Command -Name $Command -ErrorAction SilentlyContinue)
}

function Test-PortInUse {
    param([int]$Port)
    $conns = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
    return ($null -ne $conns -and @($conns).Count -gt 0)
}

function Stop-PortProcess {
    param([int]$Port)
    $conns = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
    if ($conns) {
        foreach ($conn in @($conns)) {
            $procId = $conn.OwningProcess
            Write-Status "  Killing PID=$procId..." "Yellow"
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 2
        return $true
    }
    return $false
}

# -- Resolve project root & venv --

$ProjectRoot = $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$VenvPip    = Join-Path $ProjectRoot ".venv\Scripts\pip.exe"

# Determine available Python
if (Test-Path $VenvPython) {
    $Python = $VenvPython
    $Pip    = $VenvPip
    Write-Status "[venv] Using project virtual environment" "Green"
} elseif (Test-CommandExists "python") {
    $Python = "python"
    $Pip    = "pip"
    Write-Status "[system] Using system Python" "Yellow"
} else {
    Write-Status "[ERROR] Python not found. Create a venv or install Python 3.10+" "Red"
    exit 1
}

$PyVer = & $Python --version 2>&1 | Out-String
Write-Status "  Python: $PyVer" "Gray"

# -- Step 1: Install dependencies --

if (-not $SkipInstall) {
    Write-Status "`n[1/4] Checking dependencies..." "Cyan"

    $Pyproject = Join-Path $ProjectRoot "pyproject.toml"
    if (Test-Path $Pyproject) {
        Write-Status "  pip install -e . (silent)" "Gray"
        $pipOut = & $Pip install -q -e $ProjectRoot 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0) {
            Write-Status "  pip install skipped (file locked or error - package likely already installed)" "Yellow"
        }
    }

    # Verify veriflow_agent is importable
    $check = & $Python -c "import veriflow_agent; print('OK')" 2>&1 | Out-String
    if ($check -match "OK") {
        Write-Status "  veriflow_agent module OK" "Green"
    } else {
        Write-Status "  WARNING: veriflow_agent import failed" "Yellow"
        Write-Status "  $check" "Gray"
    }
} else {
    Write-Status "`n[1/4] Skipping dependency install" "Cyan"
}

# -- Step 2: Check port --

Write-Status "`n[2/4] Checking port $Port..." "Cyan"

if (Test-PortInUse -Port $Port) {
    $oldPort = $Port
    Write-Status "  Port $Port is in use, searching for a free port..." "Yellow"
    $found = $false
    for ($candidate = $Port + 1; $candidate -le 65535; $candidate++) {
        if (-not (Test-PortInUse -Port $candidate)) {
            $Port = $candidate
            $found = $true
            break
        }
    }
    if (-not $found) {
        Write-Status "  [ERROR] No free port found in range $oldPort..65535" "Red"
        exit 1
    }
    Write-Status "  Auto-switched to free port: $Port" "Green"
} else {
    Write-Status "  Port $Port is free" "Green"
}

# -- Step 3: Start Gateway --

Write-Status "`n[3/4] Starting Gateway..." "Cyan"

$gatewayUrl = "http://${HostAddr}:${Port}"
$logDir = Join-Path $ProjectRoot ".claude\scratch"
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}
$logFile = Join-Path $logDir "gateway_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"
$errFile = Join-Path $logDir "gateway_$(Get-Date -Format 'yyyyMMdd_HHmmss')_stderr.log"

# Use veriflow-agent CLI (installed via pip install -e .)
$veriflowExe = Join-Path $ProjectRoot ".venv\Scripts\veriflow-agent.exe"
if (-not (Test-Path $veriflowExe)) {
    # Fallback: use python -m
    $veriflowExe = $Python
    $veriflowArgs = @("-m", "veriflow_agent.cli", "gateway", "-v")
} else {
    $veriflowArgs = @("gateway", "-v")
}

Write-Status "  Command: $veriflowExe $($veriflowArgs -join ' ')" "Gray"
Write-Status "  Log: $logFile" "Gray"

$proc = Start-Process -FilePath $veriflowExe `
    -ArgumentList $veriflowArgs `
    -RedirectStandardOutput $logFile `
    -RedirectStandardError $errFile `
    -WindowStyle Hidden `
    -PassThru

if ($null -eq $proc) {
    Write-Status "  [ERROR] Failed to start process." "Red"
    if (Test-Path $logFile) {
        Get-Content $logFile -ErrorAction SilentlyContinue | ForEach-Object { Write-Status "    $_" "Gray" }
    }
    if (Test-Path $errFile) {
        Get-Content $errFile -ErrorAction SilentlyContinue | ForEach-Object { Write-Status "    $_" "Gray" }
    }
    exit 1
}
Write-Status "  Process PID: $($proc.Id)" "Green"

# Wait for ready
Write-Status "  Waiting for service..." "Gray"
$ready = $false
for ($i = 1; $i -le 30; $i++) {
    Start-Sleep -Seconds 1

    # Check if process is alive
    if (-not (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue)) {
        Write-Status "`n  [ERROR] Process exited unexpectedly. Log:" "Red"
        if (Test-Path $logFile) {
            Get-Content $logFile -Tail 20 -ErrorAction SilentlyContinue | ForEach-Object { Write-Status "    $_" "Gray" }
        }
        exit 1
    }

    # TCP connection test
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $tcp.Connect($HostAddr, $Port)
        $tcp.Close()
        $ready = $true
        break
    } catch {
        if ($i % 5 -eq 0) { Write-Status "    Waited ${i}s..." "Gray" }
    }
}

if (-not $ready) {
    Write-Status "`n  [ERROR] Startup timeout (30s). Log:" "Red"
    if (Test-Path $logFile) {
        Get-Content $logFile -Tail 20 -ErrorAction SilentlyContinue | ForEach-Object { Write-Status "    $_" "Gray" }
    }
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    exit 1
}

Write-Status "  Service ready (took ${i}s)" "Green"

# -- Step 4: Open browser --

Write-Status "`n[4/4] Opening browser..." "Cyan"

try {
    Start-Process $gatewayUrl
    Write-Status "  Browser opened: $gatewayUrl" "Green"
} catch {
    Write-Status "  Please visit manually: $gatewayUrl" "Yellow"
}

# -- Done --

Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  Gateway Running" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host "  URL:    $gatewayUrl" -ForegroundColor Green
Write-Host "  PID:    $($proc.Id)" -ForegroundColor Green
Write-Host "  Log:    $logFile" -ForegroundColor Green
Write-Host "" -ForegroundColor Green
Write-Host "  Stop:   Stop-Process -Id $($proc.Id)" -ForegroundColor Green
Write-Host "  Tail:   Get-Content $logFile -Tail 50 -Wait" -ForegroundColor Green
Write-Host "" -ForegroundColor Green
Write-Host "  Press Enter or Ctrl+C to exit AND STOP the service" -ForegroundColor Red
Write-Host "============================================" -ForegroundColor Green

try {
    # Intercept Ctrl+C
    [console]::TreatControlCAsInput = $true
    while ($true) {
        if ([console]::KeyAvailable) {
            $key = [system.console]::readkey($true)
            if ($key.Key -eq 'Enter') {
                break
            }
            if ($key.Key -eq 'C' -and $key.Modifiers -match 'Control') {
                break
            }
        }
        Start-Sleep -Milliseconds 200
        
        # Check if process died
        if ($proc.HasExited) {
            Write-Host "`nGateway process exited on its own." -ForegroundColor Yellow
            break
        }
    }
} finally {
    [console]::TreatControlCAsInput = $false
    Write-Host "`nStopping Gateway process tree (PID: $($proc.Id))..." -ForegroundColor Yellow
    
    # Use taskkill /T (Process Tree) /F (Force) to kill the wrapper and all python/uvicorn children
    $null = taskkill /PID $($proc.Id) /F /T 2>&1
    
    # Fallback in case taskkill fails
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    
    Write-Host "Gateway stopped." -ForegroundColor Green
}
