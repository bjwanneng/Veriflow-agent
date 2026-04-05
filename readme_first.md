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
│           ├── eda_utils.py     # 工具发现与环境配置
│           ├── lint.py          # IverilogTool, LintResult
│           ├── simulate.py      # VvpTool, SimResult
│           └── synth.py         # YosysTool, SynthResult
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
- [x] `SkillDAgent` - Stage 3.5: 静态质量分析
- [x] `DebuggerAgent` - Stage 4: 错误修正 (testbench 防篡改保护)
- [x] `SynthAgent` - Stage 5: 综合与 KPI 对比 (纯 EDA, 无 LLM)
- [x] LangGraph StateGraph 组装 (条件路由, 检查点, 质量门控)
- [x] Click CLI (run / lint-stage / mark-complete) + Rich 格式化
- [x] 全套单元测试 (58 tests: tools + agents + graph)
- [x] 全套集成测试 (19 tests: checkpoint, validation, EDA tools, routing, pipeline, spec)
- [x] **77/77 tests passing**

---

## 5. 关键文档索引

| 文档 | 用途 | 阅读建议 |
|------|------|---------|
| `readme_first.md` | 项目入门、当前状态 | **必读** |
| `MIGRATION_PLAN.md` | 详细迁移计划 | 规划参考 |
| `IMPLEMENTATION_GUIDE.md` | 代码示例、 开发参考 |
| `pyproject.toml` | 项目配置、 依赖管理 | 环境搭建 |
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

## 🚀 三种使用方式

### 1. Claude Code Agent（推荐日常开发）

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
/veriflow-agent run --project-dir ./my_alu --mode standard
```

### 2. Web UI（推荐演示和可视化）

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
veriflow-agent run --project-dir ./my_alu --mode standard

# 快速模式
veriflow-agent run --project-dir ./my_alu --mode quick

# 断点续跑
veriflow-agent run --project-dir ./my_alu --resume

# 验证阶段输出
veriflow-agent lint-stage --stage 3 --project-dir ./my_alu
```

---

## 📚 详细文档

- **[QUICKSTART.md](QUICKSTART.md)** - 快速开始指南（三种方式详细说明）
- **[USAGE.md](USAGE.md)** - 完整使用手册（CLI、API、配置）
- **[CLAUDE.md](CLAUDE.md)** - 开发规范

---

**最后更新**: 2026-04-05

**项目状态**: ✅ Phase 5 完成 - 全部 77 测试通过 + Claude Code Agent + Web UI