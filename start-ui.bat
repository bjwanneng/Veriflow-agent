@echo off
chcp 65001
echo Starting VeriFlow-Agent Web UI...
echo URL: http://localhost:8501
echo.

REM Use the CLI command which handles path resolution
veriflow-agent ui --port 8501 %*

if errorlevel 1 (
    echo.
    echo [ERROR] Failed to start UI. Try:
    echo   1. pip install streamlit
    echo   2. veriflow-agent ui
    pause
    exit /b 1
)

