#!/bin/bash

echo "=========================================="
echo "VeriFlow-Agent Claude Code Agent Installer"
echo "=========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if running from project root
if [ ! -f ".claude/agents/veriflow-agent.md" ]; then
    echo -e "${RED}ERROR: Please run this script from the project root directory.${NC}"
    echo "Current directory: $(pwd)"
    exit 1
fi

# Check if veriflow-agent is installed
echo "Checking veriflow-agent CLI installation..."
if ! command -v veriflow-agent &> /dev/null; then
    echo ""
    echo -e "${YELLOW}WARNING: veriflow-agent CLI not found in PATH.${NC}"
    echo "Please install it first:"
    echo ""
    echo "  pip install -e ."
    echo ""
    exit 1
fi

echo -e "${GREEN}OK: veriflow-agent is installed${NC}"
echo ""

# Determine Claude config directory
if [[ "$OSTYPE" == "darwin"* ]]; then
    # macOS
    CLAUDE_AGENTS_DIR="$HOME/Library/Application Support/Claude/agents"
else
    # Linux
    CLAUDE_AGENTS_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/Claude/agents"
fi

# Create directory if not exists
echo "Creating Claude agents directory..."
mkdir -p "$CLAUDE_AGENTS_DIR"

# Copy agent definition file
echo "Installing VeriFlow-Agent definition..."
cp -f ".claude/agents/veriflow-agent.md" "$CLAUDE_AGENTS_DIR/"

if [ $? -ne 0 ]; then
    echo -e "${RED}ERROR: Failed to copy agent definition file.${NC}"
    exit 1
fi

echo ""
echo "=========================================="
echo -e "${GREEN}Installation Successful!${NC}"
echo "=========================================="
echo ""
echo "The VeriFlow-Agent has been installed to:"
echo "  $CLAUDE_AGENTS_DIR/veriflow-agent.md"
echo ""
echo "Next steps:"
echo "1. Restart Claude Code or press Ctrl+R to refresh"
echo "2. Type: /veriflow-agent run --project-dir ./your_project"
echo ""
echo "Example:"
echo "  /veriflow-agent run --project-dir ./examples/alu_project --mode quick"
echo ""
