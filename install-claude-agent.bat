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

REM === Step 1: Install CLI globally ===
echo [1/3] Installing veriflow-agent CLI globally...
pip install . --quiet 2>nul
if errorlevel 1 (
    echo ERROR: Failed to install veriflow-agent via pip.
    echo Make sure Python and pip are available.
    pause
    exit /b 1
)
echo       OK: veriflow-agent CLI installed
echo.

REM === Step 2: Add Python Scripts to PATH (user-level, persistent) ===
echo [2/3] Ensuring CLI is in PATH...
for /f "delims=" %%i in ('python -m site --user-base') do set "PYTHON_USER_BASE=%%i"
set "PYTHON_SCRIPTS_DIR=%PYTHON_USER_BASE%\Scripts"

REM Check if already in user PATH
echo %PATH% | findstr /i /c:"%PYTHON_SCRIPTS_DIR%" >nul
if errorlevel 1 (
    echo       Adding %PYTHON_SCRIPTS_DIR% to user PATH...
    powershell -Command "[Environment]::SetEnvironmentVariable('Path', [Environment]::GetEnvironmentVariable('Path', 'User') + ';%PYTHON_SCRIPTS_DIR%', 'User')" 2>nul
    set "PATH=%PATH%;%PYTHON_SCRIPTS_DIR%"
    echo       OK: Added to user PATH (takes effect in new terminals)
) else (
    echo       OK: Already in PATH
)
echo.

REM Verify CLI works
veriflow-agent --version >nul 2>&1
if errorlevel 1 (
    echo WARNING: veriflow-agent not found in current session PATH.
    echo It will work after opening a new terminal.
    echo.
) else (
    for /f "delims=" %%v in ('veriflow-agent --version') do echo       CLI version: %%v
    echo.
)

REM === Step 3: Install agent definition to Claude Code ===
echo [3/3] Installing agent definition to Claude Code...
set "CLAUDE_AGENTS_DIR=%USERPROFILE%\.claude\agents"
if not exist "%CLAUDE_AGENTS_DIR%" (
    mkdir "%CLAUDE_AGENTS_DIR%"
)
copy /Y ".claude\agents\veriflow-agent.md" "%CLAUDE_AGENTS_DIR%\" >nul
if errorlevel 1 (
    echo ERROR: Failed to copy agent definition file.
    pause
    exit /b 1
)
echo       OK: Agent definition installed to %CLAUDE_AGENTS_DIR%\veriflow-agent.md
echo.

echo ==========================================
echo Installation Complete!
echo ==========================================
echo.
echo What was installed:
echo   1. veriflow-agent CLI (global, via pip)
echo   2. Claude Code agent definition (~/.claude/agents/)
echo.
echo Next steps:
echo   1. Open a NEW terminal (to reload PATH)
echo   2. Verify:  veriflow-agent --version
echo   3. Restart Claude Code
echo   4. In ANY project directory, use:
echo      /veriflow-agent run --project-dir ./your_project --mode quick
echo.
pause
