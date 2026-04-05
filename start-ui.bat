@echo off
echo Starting VeriFlow-Agent Web UI...
echo.

REM Activate virtual environment if it exists
if exist .venv\Scripts\activate.bat (
    call .venv\Scripts\activate.bat
)

REM Start the UI
veriflow-agent ui %*

pause
