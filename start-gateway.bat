@echo off
chcp 65001 >nul
title Veriflow Gateway Starter
color 0B

echo.
echo  ================================================================
echo  ^|            Veriflow Gateway Starter                         ^|
echo  ================================================================
echo.

:: Check PowerShell
where powershell >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] PowerShell not found. Please install PowerShell first.
    pause
    exit /b 1
)

:: Get script directory
set SCRIPT_DIR=%~dp0
set PS_SCRIPT=%SCRIPT_DIR%start-gateway.ps1

:: Check PowerShell script exists
if not exist "%PS_SCRIPT%" (
    echo [ERROR] PowerShell script not found: %PS_SCRIPT%
    pause
    exit /b 1
)

:: Check execution policy
echo [INFO] Checking PowerShell execution policy...
powershell -Command "Get-ExecutionPolicy" | findstr /I "Restricted AllSigned" >nul
if %errorlevel% equ 0 (
    echo [WARN] PowerShell execution policy is restricted.
    echo [INFO] Trying Bypass mode...
    set BYPASS_FLAG=-ExecutionPolicy Bypass
) else (
    set BYPASS_FLAG=
)

echo [INFO] Starting PowerShell script...
echo.

:: Run PowerShell script
powershell -NoProfile %BYPASS_FLAG% -File "%PS_SCRIPT%" -SkipInstall

:: Capture exit code
set EXIT_CODE=%errorlevel%

echo.
if %EXIT_CODE% equ 0 (
    echo [OK] Script completed successfully.
) else (
    echo [ERROR] Script failed with exit code: %EXIT_CODE%
)

pause
exit /b %EXIT_CODE%
