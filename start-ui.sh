#!/bin/bash

echo "Starting VeriFlow-Agent Web UI..."
echo ""

# Activate virtual environment if it exists
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# Start the UI
veriflow-agent ui "$@"
