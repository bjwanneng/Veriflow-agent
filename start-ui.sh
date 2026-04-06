#!/bin/bash

echo "Starting VeriFlow-Agent Web UI..."
echo ""

# Activate virtual environment if it exists
if [ -d ".venv" ]; then
    source .venv/bin/activate 2>/dev/null || true
fi

# Start the UI via CLI (works with both venv and global install)
echo "URL: http://localhost:8501"
echo ""

veriflow-agent ui "$@"

if [ $? -ne 0 ]; then
    echo ""
    echo "[ERROR] Failed to start UI. Possible fixes:"
    echo "  1. pip install streamlit"
    echo "  2. Run: veriflow-agent ui"
    exit 1
fi
