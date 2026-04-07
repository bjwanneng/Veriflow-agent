# VeriFlow-Agent Design Specification

## Document Information

| Item | Value |
|------|-------|
| **Version** | 2.0 |
| **Date** | 2026-04-06 |
| **Status** | Final |
| **Author** | VeriFlow Team |

---

## 1. Executive Summary

VeriFlow-Agent is an **agent-based RTL design pipeline** that transforms hardware design from manual coding to automated agent-based workflows. Built on **LangGraph**, it replaces a legacy 2500-line while-loop state machine with a declarative, checkpointed, and observable graph architecture.

### Key Innovation: Declarative Feedback Loops

Unlike traditional pipelines that fail on errors, VeriFlow-Agent implements **declarative feedback loops** where lint, simulation, and synthesis failures automatically route through a Debugger agent with full rollback capability.

---

## 2. Requirements Specification

### 2.1 Functional Requirements

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-001 | Pipeline shall support 9 stages: Architect, MicroArch, Timing, Coder, SkillD, Lint, Sim, Debugger, Synth | Must | ✅ |
| FR-002 | Pipeline shall implement declarative feedback loops for lint, sim, and synth checks | Must | ✅ |
| FR-003 | Pipeline shall support checkpoint and resume at any stage | Must | ✅ |
| FR-004 | Pipeline shall accumulate error history across retry attempts | Must | ✅ |
| FR-005 | Pipeline shall support multiple LLM backends: Claude CLI, Anthropic SDK, LangChain | Should | ✅ |
| FR-006 | Pipeline shall provide three interfaces: CLI, Web UI, Claude Code Agent | Should | ✅ |
| FR-007 | Pipeline shall implement single-flow mode (removed quick/standard/enterprise modes) | Must | ✅ |

### 2.2 Non-Functional Requirements

| ID | Requirement | Target | Status |
|----|-------------|--------|--------|
| NFR-001 | Maximum retry attempts per checkpoint | 3 | ✅ |
| NFR-002 | Test coverage | > 90% | ✅ (82 tests) |
| NFR-003 | Pipeline observability | Full tracing via LangGraph | ✅ |
| NFR-004 | Checkpoint durability | JSON file + MemorySaver | ✅ |

---

## 3. Architecture Design

### 3.1 System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              USER INTERFACES                                │
├──────────────────┬──────────────────┬───────────────────────────────────────┤
│   CLI (Click)    │   Web UI (St)    │   Claude Code Agent                  │
│   veriflow-agent │   Streamlit      │   /veriflow-agent                   │
│   run/ui/lint    │   localhost:8501 │   conversational                     │
└──────────────────┴──────────────────┴───────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         LANGGRAPH STATEGRAPH                                │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │ Architect│─▶│ MicroArch│─▶│  Timing  │─▶│   Coder  │─▶│  SkillD  │       │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └────┬─────┘       │
│                                                                  │           │
│  ╔═══════════╗  ┌──────────┐  ┌──────────┐  ┌──────────┐       │           │
│  ║ END (Pass)║  │   Synth  │─▶│   Lint   │─▶│    Sim   │◀──────┘           │
│  ╚═════╤═════╝  └────┬─────┘  └────┬─────┘  └────┬─────┘                   │
│        │             │              │              │                        │
│        │             │              │         (pass)                       │
│        │             │              │              │                        │
│        │          (pass)        (fail, retry<3)   │                        │
│        │             │              │              │                        │
│        │             └──────────────┴──────────────┘                        │
│        │                            │                                       │
│  ╔═════╧════════╗                  ▼                                       │
│  ║  END (Fail)  ║              ┌──────────┐                                 │
│  ╚══════════════╝              │ Debugger │                                 │
│                                └────┬─────┘                                 │
│                                     │                                       │
│                                     └────────────────────────────────▶ Lint│
│                                                              (full rollback)│
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           AGENT LAYER                                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │ Architect│  │ MicroArch│  │  Timing  │  │   Coder  │  │  SkillD  │  (LLM)│
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘       │
│                                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐                      │
│  │   Lint   │  │    Sim   │  │  Synth   │  │ Debugger │                      │
│  │ (no LLM) │  │ (no LLM) │  │ (no LLM) │  │  (LLM)   │                      │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘                      │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           TOOL LAYER                                        │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐               │
│  │  IverilogTool   │  │    VvpTool      │  │   YosysTool     │               │
│  │  (iverilog)     │  │  (iverilog+vvp)│  │    (yosys)      │               │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘               │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Component Details

#### 3.2.1 LangGraph StateGraph

The core pipeline is implemented as a LangGraph `StateGraph` with:

- **10 Nodes**: architect, microarch, timing, coder, skill_d, lint, sim, synth, debugger, END
- **Conditional Edges**: Routing functions determine next node based on check results and retry counts
- **State**: `VeriFlowState` TypedDict with all stage outputs, retry counts, error history, feedback source
- **Checkpointing**: `MemorySaver` for resume capability

#### 3.2.2 Feedback Loop Mechanism

```python
# Pseudo-code for conditional routing
def route_after_lint(state):
    lint_output = state["lint_output"]
    if lint_output.success:
        return "sim"  # Pass: continue to next stage
    
    retry_count = state["retry_count"]["lint"]
    if retry_count < MAX_RETRIES:
        state["feedback_source"] = "lint"
        return "debugger"  # Fail: route to debugger
    
    return END  # Max retries exceeded: pipeline failed
```

**Key Properties**:
1. **Declarative**: Feedback loops are graph edges, not inline loops
2. **Observable**: All routing decisions are visible in LangGraph traces
3. **Checkpointed**: Retry state is persisted and can be resumed
4. **Full Rollback**: Debugger always routes back to Lint (not just the failed check)

#### 3.2.3 Agent Layer

| Agent | Type | LLM | Description |
|-------|------|-----|-------------|
| ArchitectAgent | LLM | ✅ | Parses requirement.md, produces spec.json |
| MicroArchAgent | LLM | ✅ | Reads spec.json, produces micro_arch.md |
| TimingAgent | LLM | ✅ | Reads spec.json, produces timing_model.yaml and testbenches |
| CoderAgent | LLM | ✅ | Reads spec.json and micro_arch.md, generates RTL files in parallel |
| SkillDAgent | LLM | ✅ | Static analysis on RTL (informational only, always succeeds) |
| LintAgent | EDA | ❌ | Runs iverilog lint, returns pass/fail with error log |
| SimAgent | EDA | ❌ | Runs iverilog + vvp simulation, returns pass/fail with log |
| DebuggerAgent | LLM | ✅ | Reads error history and RTL, applies fixes, preserves testbenches |
| SynthAgent | EDA | ❌ | Runs Yosys synthesis, produces synth_report.json |

#### 3.2.4 Tool Layer

| Tool | Executable | Purpose |
|------|------------|---------|
| IverilogTool | `iverilog` | Syntax check (`-Wall -tnull`), compile to `.vvp` |
| VvpTool | `vvp` | Run simulation from `.vvp` file |
| YosysTool | `yosys` | Synthesis (`synth -top <module>; stat -json`) |

---

## 4. State Management

### 4.1 VeriFlowState TypedDict

```python
class VeriFlowState(TypedDict):
    # Project Configuration
    project_dir: str
    
    # Execution State
    current_stage: str
    stages_completed: list[str]
    stages_failed: list[str]
    retry_count: dict[str, int]  # {"lint": 0, "sim": 0, "synth": 0}
    error_history: dict[str, list[str]]  # Accumulated errors per checkpoint
    feedback_source: str  # "lint" | "sim" | "synth" | ""
    
    # Stage Outputs
    architect_output: Optional[StageOutput]
    microarch_output: Optional[StageOutput]
    timing_output: Optional[StageOutput]
    coder_output: Optional[StageOutput]
    skill_d_output: Optional[StageOutput]
    lint_output: Optional[StageOutput]
    sim_output: Optional[StageOutput]
    synth_output: Optional[StageOutput]
    debugger_output: Optional[StageOutput]
    
    # Quality Gates
    quality_gates_passed: dict[str, bool]
    
    # Debug/Logging
    messages: Annotated[Sequence, add_messages]
```

### 4.2 Retry and Error History

The error history mechanism ensures that the Debugger agent has full context of previous fix attempts:

```python
# In node_lint (or node_sim, node_synth)
if lint_output and not lint_output.success:
    # Increment retry counter
    retry_count = dict(state.get("retry_count", {}))
    retry_count["lint"] = retry_count.get("lint", 0) + 1
    updates["retry_count"] = retry_count
    
    # Record error history
    error_history = dict(state.get("error_history", {}))
    lint_errors = list(error_history.get("lint", []))
    lint_errors.append("\n".join(lint_output.errors))
    error_history["lint"] = lint_errors
    updates["error_history"] = error_history
```

---

## 5. Interfaces

### 5.1 CLI

```bash
# Run full pipeline
veriflow-agent run --project-dir ./my_alu

# Resume from checkpoint
veriflow-agent run --project-dir ./my_alu --resume

# Validate stage output (no LLM required)
veriflow-agent lint-stage --stage 3 --project-dir ./my_alu

# Launch Web UI
veriflow-agent ui --port 8080
```

### 5.2 Web UI (Streamlit)

- **Project Setup Page**: Configure project directory, validate inputs
- **Pipeline Execution Page**: Real-time progress tracking with stage visualization
- **Results Viewer Page**: Browse generated artifacts (spec.json, RTL, reports)

### 5.3 Claude Code Agent

```
/veriflow-agent run --project-dir ./my_alu
```

Provides conversational interface within Claude Code with natural language feedback.

---

## 6. Testing

### 6.1 Test Suite

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=veriflow_agent --cov-report=html

# Run specific test file
pytest tests/test_graph.py -v
```

### 6.2 Test Coverage

**82 tests passing:**

- **19 tool tests**: IverilogTool, VvpTool, YosysTool, EDA utils
- **23 agent tests**: All 10 agents including new LintAgent, SimAgent
- **22 graph tests**: State creation, routing, graph compilation, feedback loops
- **18 integration tests**: Checkpoint persistence, EDA tool execution, spec validation

---

## 7. Configuration

### 7.1 LLM Backend Selection

Configure per-agent in agent class:

```python
# Default (Claude CLI)
super().__init__(..., llm_backend="claude_cli")

# Anthropic SDK
super().__init__(..., llm_backend="anthropic")

# LangChain
super().__init__(..., llm_backend="langchain")
```

### 7.2 Environment Variables

```bash
# For Anthropic SDK / LangChain
export ANTHROPIC_API_KEY=sk-ant-xxxxx

# Optional: Custom model selection
export VERIFLOW_MODEL=claude-sonnet-4-6
```

---

## 8. Migration from Legacy

### 8.1 Key Changes from veriflow_ctl.py

| Aspect | Legacy | New Architecture |
|--------|--------|------------------|
| State Machine | 2500-line while-loop | LangGraph StateGraph |
| Feedback | Inline for loops | Declarative conditional edges |
| Retry State | Hidden variables | Explicit in `VeriFlowState` |
| Observability | Print debugging | Full LangGraph tracing |
| Modes | quick/standard/enterprise | Single flow with full feedback |

### 8.2 Backward Compatibility

The CLI maintains compatibility:

```bash
# Legacy command
python veriflow_ctl.py --project-dir ./my_alu

# New command
veriflow-agent run --project-dir ./my_alu
```

---

## 9. Future Roadmap

### 9.1 Planned Features

- [ ] **Human-in-the-loop**: Interactive approval for critical stages
- [ ] **Parallel execution**: Run independent stages concurrently
- [ ] **Custom agents**: Plugin system for user-defined agents
- [ ] **Metrics dashboard**: Real-time pipeline analytics
- [ ] **Multi-project**: Orchestrate multiple related designs

### 9.2 Performance Optimizations

- [ ] Caching for repeated lint checks
- [ ] Incremental synthesis for large designs
- [ ] Lazy loading of LLM clients
- [ ] Parallel file I/O

---

## 10. References

### 10.1 Related Documentation

- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)
- [Claude Code Documentation](https://docs.anthropic.com/en/docs/claude-code)
- [Icarus Verilog](https://steveicarus.github.io/iverilog/)
- [Yosys Open Synthesis Suite](https://yosyshq.net/yosys/)

### 10.2 Internal Documents

- `readme_first.md` - Project overview and quick start
- `MIGRATION_PLAN.md` - Detailed migration strategy
- `IMPLEMENTATION_GUIDE.md` - Developer implementation reference
- `CLAUDE.md` - Development standards and conventions

---

**Document End**
