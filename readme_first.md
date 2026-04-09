# VeriFlow-Agent

**Agent-based RTL Design Pipeline using LangGraph**

> 基于 LangGraph 的 RTL 设计流水线，从传统状态机架构迁移到 Agent 架构

---

## 1. 项目概述

本项目是 VeriFlow 的重构版本，将原有的 2500 行状态机代码 (`veriflow_ctl.py`) 迁移到现代化的 LangGraph Agent 架构。

### 核心改进

- **显式状态机**: 使用 LangGraph 的图结构替代隐式的 while-loop
- **模块化 Agent**: 每个流水线阶段封装为独立 Agent
- **可观测性**: 内置追踪、检查点和可视化
- **人机协作**: 原生支持 human-in-the-loop 质量门控
- **可恢复性**: 检查点机制支持从断点恢复

---

## 2. 项目结构

```
Veriflow-agent/
├── pyproject.toml             # 项目配置、依赖管理
├── MIGRATION_PLAN.md          # 完整迁移计划
├── IMPLEMENTATION_GUIDE.md    # 实施指南
├── readme_first.md            # 本文件
│
├── src/
│   └── veriflow_agent/        # 主包
│       ├── __init__.py          # 包入口, 导出核心类型
│       ├── cli.py               # Click CLI (run / lint-stage / mark-complete)
│       ├── agents/              # Agent 层
│       │   ├── __init__.py
│       │   ├── base.py          # BaseAgent, AgentResult
│       │   ├── architect.py     # Stage 1: 架构分析
│       │   ├── microarch.py     # Stage 1.5: 微架构设计
│       │   ├── timing.py        # Stage 2: 时序模型
│       │   ├── coder.py         # Stage 3: RTL 代码生成
│       │   ├── skill_d.py       # Stage 3.5: 静态质量分析
│       │   ├── debugger.py      # Stage 4: 错误修正
│       │   └── synth.py         # Stage 5: 综合
│       ├── graph/               # LangGraph 核心
│       │   ├── __init__.py
│       │   ├── state.py         # VeriFlowState, StageOutput
│       │   └── graph.py         # StateGraph 组装, 条件路由
│       └── tools/               # 工具层
│           ├── __init__.py
│           ├── base.py          # BaseTool, ToolResult
│           ├── eda_utils.py     # 工具发现、环境配置、版本检测
│           ├── lint.py          # IverilogTool, LintResult
│           ├── simulate.py      # VvpTool, SimResult
│           ├── synth.py         # YosysTool, SynthResult
│           └── constraint_gen.py # SDC约束生成器
│
├── tests/                     # 测试 (77 tests)
│   ├── conftest.py              # 共享 fixture
│   ├── fixtures/                # 示例项目 (ALU)
│   ├── test_tools.py            # 工具层测试 (19 tests)
│   ├── test_agents.py           # Agent 层测试 (20 tests)
│   ├── test_graph.py            # Graph 测试 (19 tests)
│   └── test_integration.py      # 集成测试 (19 tests)
│
├── prompts/                   # 提示词文件 (从原项目复制)
│
└── .venv/                     # 虚拟环境 (已安装依赖)
```

---

## 3. 快速开始

### 3.1 环境要求

- Python >= 3.10
- Claude CLI 或 Anthropic API Key
- (可选) Icarus Verilog, Yosys

### 3.2 安装步骤

```bash
# 1. 进入项目目录
cd C:\Users\wanneng.zhang\Desktop\work\ai_app_zone\Veriflow-agent

# 2. 创建虚拟环境
python -m venv .venv

# 3. 激活虚拟环境 (Windows)
.venv\Scripts\activate

# 4. 安装依赖
pip install langgraph langchain-core langchain-anthropic anthropic pydantic click pyyaml rich

# 5. 复制提示词文件
xcopy /E /I C:\Users\wanneng.zhang\Desktop\work\ai_app_zone\Veriflow\prompts prompts
```

### 3.3 验证安装

```bash
# 检查是否可以导入
python -c "from src.veriflow_agent import __version__; print(f'OK: VeriFlow-Agent {__version__}')"

# 预期输出:
# OK: VeriFlow-Agent 0.1.0
```

---

## 4. 迁移路线图

### 当前状态: Phase 5 完成 (所有阶段完成)

| 阶段 | 任务 | 状态 | 预计工期 |
|------|------|------|---------|
| **Phase 0** | 基础设施 | ✅ 完成 | 1 周 |
| **Phase 1** | 工具层迁移 | ✅ 完成 | 1 周 |
| **Phase 2** | Agent 层实现 | ✅ 完成 | 2 周 |
| **Phase 3** | LangGraph 组装 | ✅ 完成 | 1 周 |
| **Phase 4** | CLI 与兼容层 | ✅ 完成 | 1 周 |
| **Phase 5** | 测试与验证 | ✅ 完成 | 1-2 周 |

### 已完成 ✅

- [x] 项目目录结构搭建
- [x] `MIGRATION_PLAN.md` / `IMPLEMENTATION_GUIDE.md` / `pyproject.toml`
- [x] `BaseAgent` 抽象类 + `render_prompt()` 模板渲染
- [x] `BaseTool` 抽象类
- [x] `VeriFlowState` 状态定义
- [x] 虚拟环境 + 依赖安装 + prompts 复制
- [x] `eda_utils.py` - EDA 工具发现与环境配置
- [x] `IverilogTool` - lint/syntax 检查 (iverilog -Wall -tnull)
- [x] `VvpTool` - 编译+仿真 (iverilog + vvp)
- [x] `YosysTool` - 综合 (yosys synth + stat)
- [x] `ArchitectAgent` - Stage 1: 架构分析 → spec.json
- [x] `MicroArchAgent` - Stage 1.5: 微架构设计 → micro_arch.md
- [x] `TimingAgent` - Stage 2: 时序模型 → timing_model.yaml + testbench
- [x] `CoderAgent` - Stage 3: RTL 代码生成 (并行模块生成)
- [x] `SkillDAgent` - Stage 3.5: 静态质量分析 (informational only)
- [x] `LintAgent` - Iverilog lint check (pure EDA, no LLM)
- [x] `SimAgent` - Simulation check (pure EDA, no LLM)
- [x] `DebuggerAgent` - 错误修正 (LLM-based, supports error history)
- [x] `SynthAgent` - Stage 5: 综合与 KPI 对比 (纯 EDA, 无 LLM)
- [x] LangGraph StateGraph 组装 (条件路由, 检查点, 质量门控)
- [x] **声明式反馈回路**: lint/sim/synth 失败 → debugger → lint (全流程回退)
- [x] **单一流程模式**: 移除 quick/standard/enterprise 模式分支
- [x] Click CLI (run / lint-stage / mark-complete) + Rich 格式化
- [x] 全套单元测试 + 集成测试
- [x] **115/115 tests passing**

---

## 5. 关键文档索引

| 文档 | 用途 | 阅读建议 |
|------|------|---------|
| `readme_first.md` | 项目入门、当前状态 | **必读** |
| `MIGRATION_PLAN.md` | 详细迁移计划 | 规划参考 |
| `IMPLEMENTATION_GUIDE.md` | 代码示例、 开发参考 |
| `pyproject.toml` | 项目配置、 依赖管理 | 环境搭建 |
| `tests/test_plan.md` | 测试计划 | **测试必读** |
| `src/veriflow_agent/graph/graph.py` | LangGraph 图组装 | 核心参考 |
| `src/veriflow_agent/cli.py` | CLI 入口 | 使用参考 |

---

## 6. 常见问题 (FAQ)

### Q1: 为什么要迁移到 LangGraph?

**A:** 原有架构使用 2500 行的 while-loop 管理状态，随着功能增加越来越难以维护。LangGraph 提供：
- 显式状态机（图结构可视化）
- 内置检查点（断点续跑）
- 原生人机协作支持
- 更好的可观测性

### Q2: 旧项目还能用吗?

**A:** 可以。迁移是渐进的：
- Phase 1-2: 新旧版本并行开发
- Phase 3: 默认使用新版本，旧版本作为 fallback
- Phase 6: 完全替换

### Q3: 如何贡献代码?

**A:** 
1. 遵循 PEP 8 编码规范
2. 所有代码必须通过 mypy 类型检查
3. 新功能需包含单元测试
4. 提交前运行 `black` 和 `ruff` 格式化

### Q4: 遇到安装问题怎么办?

**A:** 
1. 确认 Python >= 3.10: `python --version`
2. 确认虚拟环境已激活: `which python`
3. 检查依赖: `pip list | grep langgraph`
4. 查看详细错误: `pip install -v langgraph`

---

## 7. 联系方式

- **项目主页**: https://github.com/veriflow/veriflow-agent
- **文档**: https://veriflow-agent.readthedocs.io
- **Issue 追踪**: https://github.com/veriflow/veriflow-agent/issues

---

---

## 🚀 四种使用方式

### 1. TUI Client（推荐终端开发）

终端 WebSocket 客户端，连接到 Gateway 进行交互式聊天：

```bash
# Terminal 1: Start Gateway
veriflow-agent gateway

# Terminal 2: Connect TUI client
veriflow-agent tui
```

TUI 特点：
- 基于 Rich 的终端界面，支持 Markdown 渲染
- 流式输出，实时显示 Pipeline 进度
- 支持 `/quit`、`/new`、`/status` 命令
- 自动重连和会话管理

### 2. Claude Code Agent（推荐日常开发）

配置后直接在 Claude Code 中使用 `/veriflow-agent run` 命令：

```bash
# 配置 Agent
# Windows:
copy .claude\agents\veriflow-agent.md %APPDATA%\Claude\agents\

# Linux/macOS:
cp .claude/agents/veriflow-agent.md ~/.config/Claude/agents/
```

然后在 Claude Code 中：
```
/veriflow-agent run --project-dir ./my_alu
```

### 3. Web UI（推荐演示和可视化）

```bash
# 启动 Web UI
veriflow-agent ui

# 或指定端口
veriflow-agent ui --port 8080
```

浏览器打开 `http://localhost:8501`，提供：
- 📁 项目设置页面 - 配置目录和模式
- ▶️ 流水线执行页面 - 实时进度跟踪
- 📊 结果查看页面 - 浏览 spec/RTL/报告

### 3. CLI（推荐自动化和 CI/CD）

```bash
# 运行流水线
veriflow-agent run --project-dir ./my_alu

# 断点续跑
veriflow-agent run --project-dir ./my_alu --resume

# 验证阶段输出
veriflow-agent lint-stage --stage 3 --project-dir ./my_alu
```

---

## 📚 详细文档

- **[USAGE.md](USAGE.md)** - 完整使用手册（CLI、Chat UI、配置）
- **[CLAUDE.md](CLAUDE.md)** - 开发规范

---

**最后更新**: 2026-04-08

**项目状态**: ✅ Phase 1+2+3 全部完成 + **154测试通过** + Chat UI + TUI Client + Code Review 修复完成

---

## Session Handoff

### 2026-04-08: 全量 Code Review + 修复

**Review 范围**: 49 文件, +1617/-2672 行

**发现**: 3 Critical + 8 High + 13 Medium + 10 Low

#### Critical 修复 (安全 + 正确性)

| # | 问题 | 修复 | 文件 |
|---|------|------|------|
| C1 | `--dangerously-skip-permissions` 硬编码 | 环境变量 `VERIFLOW_SKIP_PERMISSIONS` 控制 | `agents/base.py`, `chat/llm.py` |
| C2 | LLM 输出文件名路径遍历 | `BaseAgent.sanitize_module_name()` 静态方法 | `agents/debugger.py`, `agents/coder.py`, `agents/timing.py` |
| C3 | 路由函数直接修改 state | `feedback_source` 改为 node wrapper 返回 dict | `graph/graph.py` |
| C4 | `handler.py` retry_counts 类型 `[]` → `{}` | 一字修复 | `chat/handler.py` |

#### High 修复 (功能正确性)

| # | 问题 | 修复 | 文件 |
|---|------|------|------|
| H1 | 6处流式消费重复代码 | `BaseAgent._consume_streaming()` 统一方法 | `agents/base.py` + 6个agent文件 |
| H2 | IverilogTool/SynthTool validate_prerequisites 总返回True | 检查路径是否存在 | `tools/lint.py`, `tools/synth.py` |
| H3 | YosysTool 用文件名而非完整路径 | `Path(f).resolve()` | `tools/synth.py` |
| H4 | 侧边栏解析运算符优先级 | 添加括号 | `chat/app.py` |
| H5 | SynthAgent Yosys未安装返回True | 改为返回False | `agents/synth.py` |
| H6 | SynthAgent重复if/else分支 | 移除无意义分支 | `agents/synth.py` |

#### Medium 修复 (质量 + 健壮性)

| # | 问题 | 修复 | 文件 |
|---|------|------|------|
| M1 | sim parse误匹配"bypass" | 改用`\b`单词边界 | `tools/simulate.py` |
| M2 | handler私有属性访问 | 添加`get_project_dir()`公共方法 | `chat/handler.py`, `chat/app.py` |
| M3 | LLM score未clamp到[0,1] | `max(0.0, min(1.0, ...))` | `agents/skill_d.py` |
| M4 | peer summary width=0崩溃 | `max(1, int(...))` | `agents/coder.py` |
| M5 | Debugger无修改时返回True | `_write_fixed_rtl`返回计数 | `agents/debugger.py` |
| M6 | ArchitectAgent prompt_file非线程安全 | 局部变量替代实例修改 | `agents/architect.py` |
| M8 | snapshot静默错误 | `logger.warning()` | `agents/debugger.py` |

### 2026-04-07: Agent 修复

**修复的问题:**
1. **MicroArchAgent** (`microarch.py:92`): 内容长度检查阈值从 100 字符降低到 50 字符
2. **SkillDAgent** (`skill_d.py:92`): 评分权重从 `static*0.4 + llm*0.6` 调整为 `static*0.3 + llm*0.7`，让LLM判断更有影响力
3. **TimingAgent** (`timing.py`): 添加 `_write_timing_artifacts()` 方法，解析LLM输出并写入 timing_model.yaml 和 testbench 文件
4. **DebuggerAgent** (`debugger.py:115`): 添加 `_write_fixed_rtl()` 方法，解析LLM输出中的 Verilog 代码并写入 RTL 文件

### 2026-04-07: TUI Client Implementation

**Completed:**
- Created `src/veriflow_agent/tui_client.py` - WebSocket TUI client
- Updated `cli.py` `tui` command to launch TUI client instead of Gateway
- TUI connects to Gateway at `ws://host:port/ws` using protocol frames
- Supports commands: `/quit`, `/new`, `/status`
- Rich terminal UI with Markdown rendering and streaming output

**Architecture:**
```
Terminal 1: veriflow-agent gateway  # Starts Gateway daemon
Terminal 2: veriflow-agent tui     # TUI client connects via WebSocket
```

**Files Modified:**
- `src/veriflow_agent/tui_client.py` (new)
- `src/veriflow_agent/cli.py` (updated tui command)
- `readme_first.md` (updated documentation)

---

## 4. 最新进展 (2026-04-07)

### 4.3 Phase 1 工业级改进完成 ✅

实现了三项核心架构改进：

#### 1. Debugger 分级回溯（Multi-level Rollback）

| 错误类型 | 来源 | 回退目标 | 理由 |
|---------|------|---------|------|
| SYNTAX | 任何 | coder | 代码生成语法问题 |
| LOGIC | sim | microarch | 设计/架构问题 |
| LOGIC | lint/synth | coder | 代码生成逻辑问题 |
| TIMING | synth | timing | 时序模型需修订 |
| TIMING | lint/sim | coder | 代码未遵循时序模型 |
| RESOURCE | synth | timing | 约束需调整 |
| UNKNOWN | 任何 | lint | 保守全量回退 |
| (any) | skill_d | coder | 质量预检失败 |

核心代码:
- `ErrorCategory` 枚举 + `categorize_error()` 基于关键词分类
- `get_rollback_target()` 根据错误类型+来源选择回退目标
- `node_lint/node_sim/node_synth` 自动分类错误并设置 `target_rollback_stage`
- Debugger 路由从固定 `→ lint` 改为条件边 `→ target_rollback_stage`

#### 2. Token 成本监控（Token Budgeting）

- 默认预算: 1,000,000 tokens
- 80% 警告, 100% 终止流水线
- 三个LLM后端(claude_cli/anthropic/langchain)均追踪token使用
- `_run_stage` 自动累加 `token_usage` 和 `token_usage_by_stage`
- 路由函数在重试前检查 `check_token_budget()`

#### 3. 测试覆盖

- 从 82 tests → **134 tests**（新增52个测试）
- 新增测试类: `TestErrorCategorization`, `TestGetRollbackTarget`, `TestTokenBudget`
- 所有 134/134 tests passing

### 4.4 Phase 2 质量守门员完成 ✅

#### SkillD 升级为质量守门员

- **两阶段分析**:
  1. 静态分析（免费）: 模块结构、命名规范、文件大小
  2. LLM预检（低成本）: 锁存器推断、组合环路、未初始化寄存器、非可综合构造
- **质量评分**: `score = static*0.4 + llm*0.6`，低于阈值(默认0.5)触发debugger
- **条件边**: `skill_d → (pass) → lint` 或 `skill_d → (fail) → debugger → coder`
- **ROI**: 在昂贵iverilog/Yosys之前拦截低质量代码

### 4.5 Phase 3 工程化增强完成 ✅

#### 3a. 约束管理生成

- 新增 `constraint_gen.py`: 从 `timing_model.yaml` 生成 `.sdc` 约束文件
- 支持约束类型: create_clock, set_input/output_delay, set_max_delay, set_false_path, set_multicycle_path
- SynthAgent 自动检测 timing_model 并生成约束
- 约束文件路径记录在综合报告中

#### 3b. EDA工具版本检测

- 新增 `get_tool_version()`, `get_all_tool_versions()`, `check_version_compatibility()`
- 支持 iverilog, vvp, yosys 版本检测
- 版本比较工具: `_compare_versions()` 支持语义化版本(x.y.z)
- 最低版本要求: iverilog >= 10.0, yosys >= 0.9

### 4.1 UI组件完成

新增3个核心可视化组件，采用 **Raw Terminal Aesthetic** 设计：

| 组件 | 功能 | 文件 |
|------|------|------|
| **FeedbackLoop Visualizer** | 9阶段流水线可视化，显示Debugger回滚路径 | `feedback_loop_viz.py` |
| **ErrorHistory Timeline** | 垂直时间线显示最近3次错误，带展开/折叠 | `error_history_timeline.py` |
| **Debugger Status Panel** | 终端风格面板，显示分析文件、上下文、实时日志 | `debugger_status_panel.py` |

### 4.2 收到专业Review意见

针对 `design_spec.md` 收到详细Review，主要建议如下：

#### 🔴 极其合理（建议立即采纳）

1. **Debugger 分级回溯（Multi-level Rollback）**
   - **现状**: 统一回退到 Lint
   - **问题**: Sim Fail（功能性）需回退到 MicroArch/Coder，Synth Fail（时序/面积）需回退到 Timing
   - **改进**: 基于错误类型智能选择回退点

2. **Token 成本监控（Token Budgeting）**
   - **重要性**: Debugger循环3次易耗尽预算
   - **实现**: 单次Pipeline Token预算，超限时自动暂停

3. **SkillD 升级为质量守门员**
   - **现状**: SkillD "始终成功"，浪费预检查机会
   - **改进**: LLM预检查代码风格、硬件禁忌，低质量直接触发Debugger
   - **收益**: 节省昂贵Iverilog/Yosys开销，ROI极高

#### 🟡 合理但可延后（未来版本）

4. **约束管理生成** - Timing→Synthesis桥梁，当前可手工约束
5. **并行Coder执行** - LangGraph原生支持，当前串行已够用
6. **版本检测** - EDA工具版本检测，工程化增强

### 4.3 下一步计划

**Phase 1: 核心架构改进（2周）**
- [ ] 实现Debugger分级回溯逻辑
- [ ] 扩展VeriFlowState（context_window_files, error_category）
- [ ] 错误日志去重/摘要算法

**Phase 2: 质量与成本（1周）**
- [ ] SkillD升级为质量守门员
- [ ] Token预算与成本监控

**Phase 3: 工程化（3-5天）**
- [ ] 约束管理生成
- [ ] EDA工具版本检测

**目标**: 具备工业级部署能力的 RTL 设计 Agent 平台

### 4.6 Chat UI 完成 ✅

新增独立 Gradio Chat 界面，提供类似 Claude/ChatGPT 的交互体验：

```bash
# 启动 Chat UI
veriflow-agent chat --port 7860
# 浏览器打开 http://localhost:7860
```

新增文件：

```
src/veriflow_agent/chat/
  __init__.py         # 导出 launch_chat()
  app.py              # Gradio ChatInterface 定义
  handler.py          # Gradio ↔ LangGraph 桥接（graph.stream() 流式输出）
  project_manager.py  # 自然语言 → 项目目录转换
  formatters.py       # State → Markdown 进度格式化
```

功能：
- **流式进度**: 每个阶段实时显示进度条和结果
- **多轮对话**: 新设计 / 查看文件 / 修改重跑 / 恢复
- **调试可视化**: Debugger 反馈回路实时展示（重试次数、回退目标、错误类型）
- **RTL 代码展示**: 自动显示生成的 Verilog 代码
- **公网分享**: `--share` 参数生成公网 URL