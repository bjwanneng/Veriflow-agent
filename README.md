# VeriFlow-Agent

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-77%20passing-brightgreen.svg)]()

> **Agent-based RTL Design Pipeline using LangGraph**

Transform hardware design from manual coding to agent-based workflows. VeriFlow-Agent automates the complete RTL design flow—from architecture specification to synthesis—using a 7-stage pipeline powered by LangGraph and LLM agents.

![Pipeline Overview](docs/assets/pipeline-overview.png)

## ✨ Features

- **🔄 7-Stage Pipeline**: Architect → MicroArch → Timing → Coder → Skill D → Sim Loop → Synthesis
- **🤖 Multi-Backend LLM Support**: Claude CLI, Anthropic SDK, LangChain (pluggable backends)
- **📊 Three Interfaces**:
  - **Claude Code Agent**: Conversational interface within Claude Code
  - **Web UI**: Streamlit-based browser interface for visualization
  - **CLI**: Command-line tool for automation and CI/CD
- **💾 Checkpoint & Resume**: Pause and resume pipeline execution at any stage
- **🧪 77 Comprehensive Tests**: Full test coverage for tools, agents, graph, and integration

## 🚀 Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/bjwanneng/Veriflow-agent.git
cd Veriflow-agent

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -e .
```

Prompt files are already included in the repository under `prompts/` directory.


### Usage Options

#### Option 1: Claude Code Agent (Recommended for Development)

Configure the custom agent in Claude Code:

```bash
# Windows
install-claude-agent.bat

# Linux/macOS
./install-claude-agent.sh
```

Then in Claude Code:
```
/veriflow-agent run --project-dir ./my_alu --mode standard
```

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
veriflow-agent run --project-dir ./my_alu --mode standard

# Quick mode (skip timing and sim_loop)
veriflow-agent run --project-dir ./my_alu --mode quick

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
| 3.5 | **Skill D** | ✅ | RTL files | Lint reports |
| 4 | **Sim Loop** | ✅ | RTL + TB | Simulation results |
| 5 | **Synthesis** | ❌ (EDA only) | RTL | `synth_report.json` |

### Pipeline Modes

- **standard** (default): Run all 7 stages
- **quick**: Skip timing (Stage 2) and sim_loop (Stage 4) for rapid prototyping
- **enterprise**: All stages with stricter quality gates

## 🧪 Testing

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=veriflow_agent --cov-report=html

# Run specific test file
pytest tests/test_tools.py -v
```

**Current Test Coverage: 77 tests passing**
- 19 tool tests
- 20 agent tests
- 19 graph tests
- 19 integration tests

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

For support, please open an issue on [GitHub Issues](https://github.com/bjwanneng/Veriflow-agent/issues).
