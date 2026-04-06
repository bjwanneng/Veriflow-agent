#!/bin/bash

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "=========================================="
echo "VeriFlow-Agent Claude Code Agent Installer"
echo "=========================================="
echo ""

# Check if running from project root
if [ ! -f ".claude/agents/veriflow-agent.md" ]; then
    echo -e "${RED}ERROR: Please run this script from the project root directory.${NC}"
    echo "Current directory: $(pwd)"
    exit 1
fi

# === Step 1: Install CLI globally ===
echo "[1/3] Installing veriflow-agent CLI globally..."
pip install . --quiet 2>/dev/null
if [ $? -ne 0 ]; then
    echo -e "${RED}ERROR: Failed to install veriflow-agent via pip.${NC}"
    exit 1
fi
echo -e "      ${GREEN}OK${NC}: veriflow-agent CLI installed"
echo ""

# === Step 2: Verify CLI is in PATH ===
echo "[2/3] Verifying CLI is accessible..."
if ! command -v veriflow-agent &>/dev/null; then
    PYTHON_SCRIPTS_DIR="$(python -m site --user-base)/bin"
    echo -e "      ${YELLOW}WARNING${NC}: veriflow-agent not in current PATH."
    echo "      Add this to your shell profile:"
    echo "        export PATH=\"\$PATH:$PYTHON_SCRIPTS_DIR\""
    echo ""
else
    echo -e "      ${GREEN}OK${NC}: $(veriflow-agent --version 2>/dev/null || echo 'CLI found')"
    echo ""
fi

# === Step 3: Install agent definition to Claude Code ===
echo "[3/3] Installing agent definition to Claude Code..."
CLAUDE_AGENTS_DIR="$HOME/.claude/agents"
mkdir -p "$CLAUDE_AGENTS_DIR"
cp -f ".claude/agents/veriflow-agent.md" "$CLAUDE_AGENTS_DIR/"
if [ $? -ne 0 ]; then
    echo -e "${RED}ERROR: Failed to copy agent definition file.${NC}"
    exit 1
fi
echo -e "      ${GREEN}OK${NC}: Agent definition installed to $CLAUDE_AGENTS_DIR/veriflow-agent.md"
echo ""

echo "=========================================="
echo -e "${GREEN}Installation Complete!${NC}"
echo "=========================================="
echo ""
echo "What was installed:"
echo "  1. veriflow-agent CLI (global, via pip)"
echo "  2. Claude Code agent definition (~/.claude/agents/)"
echo ""
echo "Next steps:"
echo "  1. Open a NEW terminal (to reload PATH)"
echo "  2. Verify:  veriflow-agent --version"
echo "  3. Restart Claude Code"
echo "  4. In ANY project directory, use:"
echo "     /veriflow-agent run --project-dir ./your_project --mode quick"
echo ""
