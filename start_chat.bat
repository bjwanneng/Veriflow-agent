@echo off
chcp 65001 >nul 2>&1
title VeriFlow-Agent Chat

python "%~dp0start_chat.py"
pause
