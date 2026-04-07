#!/usr/bin/env bash
# VeriFlow-Agent Chat Launcher (Linux/macOS)
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
python3 "$DIR/start_chat.py"
