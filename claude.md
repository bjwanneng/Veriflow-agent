# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (editable mode with dev extras)
pip install -e ".[dev]"

# Run all tests
pytest tests/

# Run a single test file
pytest tests/test_agents.py

# Run a single test by name
pytest tests/test_tools.py::TestIverilogTool::test_lint_success

# Type checking
mypy src/

# Linting
ruff check src/ tests/

# Format
black src/ tests/

# Launch Web UI
veriflow-agent ui

# Run pipeline via CLI
veriflow-agent run --project-dir ./examples/alu_project --mode quick
```

## Architecture

VeriFlow-Agent is a Python 3.10+ project that implements a 7-stage RTL design pipeline using **LangGraph**. The pipeline replaces a legacy 2500-line while-loop state machine.

### Three-Layer Stack

**1. Tools layer** (`src/veriflow_agent/tools/`): Wrappers around EDA CLI tools (Icarus Verilog, Yosys). Each tool inherits `BaseTool` and produces a typed `ToolResult`. `eda_utils.py` discovers tools in PATH at runtime.

**2. Agents layer** (`src/veriflow_agent/agents/`): Each pipeline stage is an `Agent` subclass. Agents render Jinja-style prompts from `prompts/`, call the LLM via a pluggable backend (Claude CLI / Anthropic SDK / LangChain), and return `AgentResult`. The base class handles input validation, prompt rendering, and LLM invocation.

**3. Graph layer** (`src/veriflow_agent/graph/`): `graph.py` assembles a LangGraph `StateGraph` from the 7 agent nodes with conditional edges for mode-based routing and retry loops. `state.py` defines `VeriFlowState` (TypedDict) and `StageOutput` (dataclass). Checkpointing uses `MemorySaver` for resume capability.

### Pipeline Stages

| Stage | Agent | Output artifact |
|-------|-------|----------------|
| 1 | `ArchitectAgent` | `workspace/docs/spec.json` |
| 1.5 | `MicroArchAgent` | `workspace/docs/micro_arch.md` |
| 2 | `TimingAgent` | `workspace/docs/timing_model.yaml` + TB |
| 3 | `CoderAgent` | `workspace/rtl/*.v` |
| 3.5 | `SkillDAgent` | Quality analysis (lint feedback) |
| 4 | `DebuggerAgent` | Corrected RTL (retry loop) |
| 5 | `SynthAgent` | `workspace/docs/synth_report.json` (EDA only, no LLM) |

Modes: `quick` (stages 1, 3, 5), `standard` (all 7), `enterprise` (all 7 with strict quality gates).

### Interfaces

- **CLI** (`cli.py`): `veriflow-agent run / lint-stage / mark-complete / ui` via Click + Rich formatting
- **Web UI** (`ui/`): Streamlit multi-page app at `http://localhost:8501`
- **Claude Code Agent** (`.claude/agents/veriflow-agent.md`): Custom agent definition

### Project Directory Convention

User projects follow this layout (auto-created by the pipeline):

```
my_project/
├── requirement.md          # Input: design spec
├── workspace/
│   ├── docs/               # Stage 1, 1.5, 2, 5 outputs
│   ├── rtl/                # Stage 3 RTL files
│   └── tb/                 # Testbenches
└── .veriflow/
    └── checkpoint.json     # Auto-created for --resume
```
