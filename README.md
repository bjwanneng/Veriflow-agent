# VeriFlow-Agent

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-77%20passing-brightgreen.svg)]()

> **Agent-based RTL Design Pipeline using LangGraph**

Transform hardware design from manual coding to agent-based workflows. VeriFlow-Agent automates the complete RTL design flow—from architecture specification to synthesis—using a 7-stage pipeline powered by LangGraph and LLM agents.

![Pipeline Overview](docs/assets/pipeline-overview.png)

## ✨ Features

- **🔄 9-Stage Pipeline**: Architect → MicroArch → Timing → Coder → Skill D → Lint → Sim → Synthesis
- **🔄 Declarative Feedback Loops**: Lint/Sim/Synth failures route through Debugger with full rollback
- **🤖 Multi-Backend LLM Support**: Claude CLI, Anthropic SDK, LangChain (pluggable backends)
- **📊 Three Interfaces**:
  - **Claude Code Agent**: Conversational interface within Claude Code
  - **Web UI**: Streamlit-based browser interface for visualization
  - **CLI**: Command-line tool for automation and CI/CD
- **💾 Checkpoint & Resume**: Pause and resume pipeline execution at any stage
- **🧪 82 Comprehensive Tests**: Full test coverage for tools, agents, graph, and integration

## 🚀 Quick Start

### Installation

#### 1. Clone & Install

```bash
git clone https://github.com/bjwanneng/Veriflow-agent.git
cd Veriflow-agent
```

<details>
<summary><b>Windows</b></summary>

```powershell
# Create and activate virtual environment
python -m venv .venv
.venv\Scripts\activate

# Install in editable mode (with dev tools)
pip install -e ".[dev]"

# Verify CLI
veriflow-agent --help
```

If `veriflow-agent` is not found, add Python Scripts to PATH:
```powershell
# Check where it was installed
pip show veriflow-agent

# Add to user PATH (persistent, takes effect in new terminals)
$scriptsDir = "$(python -m site --user-base)\Scripts"
[Environment]::SetEnvironmentVariable("Path", $env:Path + ";$scriptsDir", "User")
```

</details>

<details>
<summary><b>Linux / macOS</b></summary>

```bash
# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install in editable mode (with dev tools)
pip install -e ".[dev]"

# Verify CLI
veriflow-agent --help
```

If `veriflow-agent` is not found, add to PATH:
```bash
# Add to your shell profile (~/.bashrc or ~/.zshrc)
echo 'export PATH="$PATH:$(python3 -m site --user-base)/bin"' >> ~/.bashrc
source ~/.bashrc
```

</details>

#### 2. EDA Tool Dependencies (Optional, for synthesis)

VeriFlow-Agent uses Icarus Verilog and Yosys for simulation and synthesis stages.

<details>
<summary><b>Windows</b></summary>

```powershell
# Install via Chocolatey (run as Administrator)
choco install icarusverilog yosys -y

# Or download manually:
# Icarus Verilog: https://bleyer.org/icarus/
# Yosys:         https://github.com/YosysHQ/oss-cad-suite-build/releases
```

Verify:
```powershell
iverilog -V
yosys --version
```

</details>

<details>
<summary><b>Linux (Ubuntu/Debian)</b></summary>

```bash
sudo apt-get update
sudo apt-get install -y iverilog yosys

# Verify
iverilog -V
yosys --version
```

</details>

<details>
<summary><b>macOS</b></summary>

```bash
brew install icarus-verilog yosys

# Verify
iverilog -V
yosys --version
```

</details>

#### 3. Configure Claude Code Agent (Optional)

To use `/veriflow-agent` inside Claude Code:

```bash
# Windows
install-claude-agent.bat

# Linux/macOS
chmod +x install-claude-agent.sh
./install-claude-agent.sh
```

Then restart Claude Code and run:
```
/veriflow-agent run --project-dir ./my_alu --mode standard
```

Prompt files are already included in the repository under `prompts/` directory.


### Usage Options

#### Option 1: Claude Code Agent (Recommended for Development)

See [Step 3 above](#3-configure-claude-code-agent-optional) to set up the agent.

#### Option 2: Web UI (Recommended for Visualization)

```bash
# Launch Streamlit Web UI
veriflow-agent ui

# Or with custom port
veriflow-agent ui --port 8080 --host 0.0.0.0
```

Then open `http://localhost:8501` in your browser.

![Web UI Screenshot](docs/assets/web-ui-screenshot.png)

#### Option 3: CLI (Recommended for CI/CD)

```bash
# Run full pipeline
veriflow-agent run --project-dir ./my_alu

# Resume from checkpoint
veriflow-agent run --project-dir ./my_alu --resume

# Validate stage output (no LLM required)
veriflow-agent lint-stage --stage 3 --project-dir ./my_alu
```

## 📁 Project Structure

```
my-project/
├── requirement.md              # Design requirements (required)
├── workspace/
│   ├── docs/
│   │   ├── spec.json           # Stage 1: Architecture spec
│   │   ├── micro_arch.md       # Stage 1.5: Micro-architecture
│   │   ├── timing_model.yaml   # Stage 2: Timing model
│   │   └── synth_report.json   # Stage 5: Synthesis report
│   ├── rtl/
│   │   └── *.v                 # Stage 3: Generated RTL
│   └── tb/
│       └── tb_*.v               # Stage 2/4: Testbenches
└── .veriflow/
    └── checkpoint.json         # Resume point (auto-created)
```

## 🔧 7-Stage Pipeline

| Stage | Name | LLM | Input | Output |
|-------|------|-----|-------|--------|
| 1 | **Architect** | ✅ | `requirement.md` | `spec.json` |
| 1.5 | **MicroArch** | ✅ | `spec.json` | `micro_arch.md` |
| 2 | **Timing** | ✅ | `spec.json` | `timing_model.yaml`, `tb_*.v` |
| 3 | **Coder** | ✅ | `spec.json` | `*.v` RTL files |
| 3.5 | **Skill D** | ✅ | RTL files | Static analysis report |
| 4 | **Lint** | ❌ (EDA only) | RTL files | Pass/fail + error log |
| 5 | **Sim** | ❌ (EDA only) | RTL + TB | Pass/fail + sim log |
| 6 | **Synth** | ❌ (EDA only) | RTL | `synth_report.json` |

### Feedback Loops

The pipeline includes **declarative feedback loops** via LangGraph conditional edges:

```
Lint/Sim/Synth Check
       ↓
    (pass) → Next Stage
       ↓
    (fail & retry < 3) → Debugger → Lint (full rollback)
       ↓
    (fail & retry ≥ 3) → Pipeline Failed
```

- **MAX_RETRIES = 3** for each checkpoint (lint/sim/synth)
- **Error history** is accumulated and passed to Debugger for context
- **Debugger** is a proper graph node, visible to UI and checkpointing

## 🧪 Testing

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=veriflow_agent --cov-report=html

# Run specific test file
pytest tests/test_tools.py -v
```

**Current Test Coverage: 82 tests passing**
- 19 tool tests
- 23 agent tests (including new LintAgent, SimAgent)
- 22 graph tests (including new feedback loop tests)
- 18 integration tests

## 🛠️ LLM Backend Configuration

VeriFlow-Agent supports three LLM backends, configurable per-agent:

### 1. Claude CLI (Default, Recommended)
```bash
# Install Claude CLI
npm install -g @anthropic-ai/claude-code

# Login
claude login

# No API key needed - uses Claude CLI authentication
```

### 2. Anthropic SDK
```bash
export ANTHROPIC_API_KEY=sk-ant-xxxxx
```

Then modify agent:
```python
super().__init__(
    ...
    llm_backend="anthropic",
)
```

### 3. LangChain
```bash
pip install langchain-anthropic
export ANTHROPIC_API_KEY=sk-ant-xxxxx
```

```python
llm_backend="langchain"
```

## 🤝 Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## 📄 License

This project is licensed under the MIT License - see [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- Original VeriFlow project for the inspiration
- LangGraph team for the graph-based state machine framework
- Anthropic for Claude and the Claude Code platform

---

**Built with ❤️ by the VeriFlow Team**

## 📚 Documentation

| Document | Description |
|----------|-------------|
| `README.md` | This file - project overview and quick start |
| [`design_spec.md`](design_spec.md) | **Design Specification** - detailed architecture, requirements, and design decisions |
| `readme_first.md` | Project status and migration roadmap |
| `MIGRATION_PLAN.md` | Legacy migration strategy |
| `IMPLEMENTATION_GUIDE.md` | Developer implementation reference |

---

**Built with ❤️ by the VeriFlow Team**

For support, please open an issue on [GitHub Issues](https://github.com/bjwanneng/Veriflow-agent/issues).
