@echo off
echo ==========================================
echo VeriFlow-Agent Claude Code Agent Installer
echo ==========================================
echo.

REM Check if running from project root
if not exist ".claude\agents\veriflow-agent.md" (
    echo ERROR: Please run this script from the project root directory.
    echo Current directory: %CD%
    pause
    exit /b 1
)

REM Check if veriflow-agent is installed
echo Checking veriflow-agent CLI installation...
veriflow-agent --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo WARNING: veriflow-agent CLI not found in PATH.
    echo Please install it first:
    echo.
    echo   pip install -e .
    echo.
    pause
    exit /b 1
)
echo OK: veriflow-agent is installed
echo.

REM Create Claude agents directory if not exists
set "CLAUDE_AGENTS_DIR=%APPDATA%\Claude\agents"
if not exist "%CLAUDE_AGENTS_DIR%" (
    echo Creating Claude agents directory...
    mkdir "%CLAUDE_AGENTS_DIR%"
)

REM Copy agent definition file
echo Installing VeriFlow-Agent definition...
copy /Y ".claude\agents\veriflow-agent.md" "%CLAUDE_AGENTS_DIR%\" >nul

if errorlevel 1 (
    echo ERROR: Failed to copy agent definition file.
    pause
    exit /b 1
)

echo.
echo ==========================================
echo Installation Successful!
echo ==========================================
echo.
echo The VeriFlow-Agent has been installed to:
echo   %CLAUDE_AGENTS_DIR%\veriflow-agent.md
echo.
echo Next steps:
echo 1. Restart Claude Code or press Ctrl+R to refresh
echo 2. Type: /veriflow-agent run --project-dir ./your_project
echo.
echo Example:
echo   /veriflow-agent run --project-dir ./examples/alu_project --mode quick
echo.
pause
