# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## ⚖️ Critical Instructions (Mindset)
1. **Sync & Plan**: Before any coding, read `readme_first.md` (if exists) and output a `/plan`. 
2. **Hybrid Context**: You are managing a Python backend that generates Verilog. When writing Python, follow PEP8/Type Hints. When writing Verilog (CoderAgent/DebuggerAgent), follow the "Asynchronous Reset, Active Low" rule.
3. **Tool Awareness**: This project relies on `iverilog` and `yosys`. Before fixing an Agent, check if the corresponding Tool wrapper in `src/veriflow_agent/tools/` needs updating.
4. **LangGraph Safety**: When modifying `graph.py`, ensure the `VeriFlowState` remains consistent. Do not break the checkpointing logic.

## 📁 Filesystem Hygiene
- **Scratchpad**: Put experimental prompts or debug logs in `.claude/scratch/`.
- **EDA Logs**: Never attempt to read raw bitstreams or massive wave files. Parse the summary JSONs in `workspace/docs/` instead.

## ⚖️ Observability Constraints (CRITICAL)
To kill the "Black Box" behavior, all development must follow these rules:

1. **State Transparency**: Every LangGraph Node must update the `metadata` field in `VeriFlowState` with:
   - `start_time`, `end_time`
   - `llm_raw_response_path` (link to the log file)
   - `eda_tool_return_code`
2. **UI Intermediates**: The Streamlit UI must not just show the "Final Result." It must have a "Live Trace" view that reads from the `workspace/` directory in real-time.
3. **No Silent Retries**: If `DebuggerAgent` triggers a retry, the reason for the retry must be explicitly logged and visible in the UI.
4. **Tool Wrappers**: Every call to `iverilog` or `yosys` must redirect `stdout/stderr` to a unique log file in `workspace/logs/` for the UI to display.

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

### Self-Healing Architecture

The pipeline uses a **fully LLM-driven** self-healing system. When any stage fails:

1. **SupervisorAgent** (LLM) analyzes the failure with full project context (error logs, RTL files, spec, environment)
2. Supervisor decides: `retry_stage`, `escalate_stage`, `degrade`, `continue`, or `abort`
3. If Supervisor routes to Debugger and Debugger also fails, Supervisor is called **again** with `debugger_failure_note` to choose a different strategy
4. All routing decisions are made by the LLM — no regex-based keyword matching or mechanical stage jumping

**Key principle: LLM does all routing. Code only executes LLM decisions, enforces retry limits, and notifies the UI. If the LLM is unavailable, the pipeline pauses and asks the user — never guesses mechanically.**

### Partial Pipeline Runs

When users request incremental execution (e.g., "从 lint 开始执行"), `_run_pipeline_partial` uses the **full** `STAGE_ORDER` list. Stages before the requested start point are skipped, but the list is not truncated — this allows rollback to earlier stages (e.g., lint failure → rollback to coder). The `start_idx` is dynamically updated when a rollback targets an earlier stage.

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
