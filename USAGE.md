# VeriFlow-Agent 使用指南

本文档介绍如何使用 VeriFlow-Agent 的三种方式：
1. **Claude Code 自定义 Agent** - 在 Claude Code 中直接调用
2. **Web UI** - 浏览器界面操作
3. **CLI** - 命令行工具

---

## 方式一：Claude Code 自定义 Agent（推荐）

通过 Claude Code 的 Custom Agent 功能，直接在 Claude Code 中使用 `/veriflow-run` 命令。

### 配置步骤

1. **确保 Claude Code 版本 >= 0.2.0**
   ```bash
   claude --version
   ```

2. **将 agent 配置复制到 Claude Code 配置目录**

   **Windows:**
   ```powershell
   $source = "C:\Users\$env:USERNAME\Desktop\work\ai_app_zone\Veriflow-agent\.claude\agents\veriflow-agent.md"
   $dest = "$env:APPDATA\Claude\agents\veriflow-agent.md"

   New-Item -ItemType Directory -Force (Split-Path $dest)
   Copy-Item $source $dest -Force
   ```

   **Linux/macOS:**
   ```bash
   mkdir -p ~/.config/Claude/agents
   cp ~/work/ai_app_zone/Veriflow-agent/.claude/agents/veriflow-agent.md ~/.config/Claude/agents/
   ```

3. **安装 VeriFlow-Agent CLI 到 PATH**

   Agent 定义文件告诉 Claude Code 如何解析命令，但实际执行需要 `veriflow-agent` CLI 在系统 PATH 中：

   ```bash
   # 在项目根目录执行
   cd Veriflow-agent
   pip install -e .
   ```

   验证安装：
   ```bash
   which veriflow-agent  # Linux/macOS
   where veriflow-agent  # Windows
   
   veriflow-agent --help  # 应该显示帮助信息
   ```

4. **重启 Claude Code** 或按 `Ctrl+R` 刷新配置

### 使用方法

在 Claude Code 中直接输入：

```
/veriflow-agent run --project-dir ./my_alu --mode standard
```

或交互式使用：

```
/veriflow-agent run
Claude: 请提供项目目录路径
You: ./my_alu
Claude: 选择模式: [1] standard [2] quick
You: 1
... (开始执行并实时显示进度)
```

### 可用命令

| 命令 | 说明 |
|------|------|
| `/veriflow-agent run` | 运行完整流水线 |
| `/veriflow-agent run --resume` | 从检查点恢复 |
| `/veriflow-agent lint-stage --stage 3` | 验证第3阶段输出 |
| `/veriflow-agent status` | 查看项目状态 |

---

## 方式二：Web UI（浏览器界面）

通过 Streamlit 构建的浏览器界面，提供可视化操作体验。

### 启动 Web UI

```bash
# 方式1: 使用 CLI 命令
veriflow-agent ui

# 方式2: 指定端口
veriflow-agent ui --port 8501 --host localhost

# 方式3: 直接使用 streamlit
streamlit run src/veriflow_agent/ui/app.py
```

### 界面功能

#### 1. 项目设置页面 (`/pages/01_🏠_project_setup`)

- 📁 **项目目录选择** - 输入或选择项目路径
- ✅ **自动验证** - 检查 requirement.md 和目录结构
- ⚙️ **模式选择** - quick / standard / enterprise
- 📝 **需求预览** - 查看 requirement.md 内容

#### 2. 流水线执行页面 (`/pages/02_▶️_pipeline_execution`)

- 📊 **实时进度** - 显示各阶段执行状态
- ⏳ **进度条** - 每个阶段的完成百分比
- 📝 **执行日志** - 实时输出执行日志
- 🔄 **断点续跑** - 支持从检查点恢复
- 🛑 **停止/重试** - 控制执行流程

#### 3. 结果查看页面 (`/pages/03_📊_results_viewer`)

- 📄 **Spec 查看** - 可视化 spec.json
- 📐 **架构文档** - 查看 micro_arch.md
- ⏱️ **时序模型** - timing_model.yaml + testbench
- 💻 **RTL 浏览器** - 查看所有生成的 Verilog 文件
- 📊 **综合报告** - 查看面积、时序、功耗指标

### 使用流程

```
1. 启动 Web UI
   $ veriflow-agent ui

2. 浏览器自动打开 http://localhost:8501

3. 在"项目设置"页面：
   - 输入项目目录路径
   - 选择运行模式 (quick/standard/enterprise)
   - 确认 requirement.md 存在

4. 切换到"流水线执行"页面：
   - 点击"开始执行"
   - 观察实时进度和各阶段状态
   - 查看执行日志

5. 执行完成后，切换到"结果查看"页面：
   - 查看 spec.json 结构
   - 浏览生成的 RTL 代码
   - 查看综合报告指标
```

---

## 方式三：CLI（命令行）

传统的命令行使用方式，适合自动化脚本和 CI/CD 集成。

### 安装

```bash
# 从源码安装
pip install -e .

# 安装后可用命令
veriflow-agent --help
```

### 核心命令

#### 1. 运行流水线

```bash
# 标准模式（全部 7 阶段）
veriflow-agent run --project-dir ./my_alu --mode standard

# 快速模式（跳过 timing 和 sim_loop）
veriflow-agent run --project-dir ./my_alu --mode quick

# 从检查点恢复
veriflow-agent run --project-dir ./my_alu --resume

# 指定工作线程数
veriflow-agent run --project-dir ./my_alu --workers 8
```

#### 2. 验证阶段输出

```bash
# 验证第 3 阶段（Coder）输出
veriflow-agent lint-stage --stage 3 --project-dir ./my_alu

# 支持的阶段号：
# 1 = architect, 15 = microarch, 2 = timing
# 3 = coder, 35 = skill_d, 4 = sim_loop, 5 = synth
```

#### 3. 标记阶段完成

```bash
# 手动标记阶段 1 为完成（用于调试/测试）
veriflow-agent mark-complete --stage 1 --project-dir ./my_alu
```

#### 4. 启动 Web UI

```bash
# 启动 Streamlit Web UI（默认端口 8501）
veriflow-agent ui

# 指定端口和主机
veriflow-agent ui --port 8080 --host 0.0.0.0
```

### 项目目录结构

```
my-project/
├── requirement.md              # 设计需求（必须）
├── .veriflow/
│   └── checkpoint.json         # 检查点（自动创建）
└── workspace/
    ├── docs/
    │   ├── spec.json           # architect 输出
    │   ├── micro_arch.md       # microarch 输出
    │   ├── timing_model.yaml   # timing 输出
    │   └── synth_report.json   # synth 输出
    ├── rtl/
    │   └── *.v                 # coder 输出 RTL
    └── tb/
        └── tb_*.v               # timing 输出 testbench
```

### 环境变量

| 变量 | 说明 | 示例 |
|------|------|------|
| `ANTHROPIC_API_KEY` | Anthropic API Key（使用 anthropic backend 时需要） | `sk-ant-xxxxx` |
| `VERIFLOW_LOG_LEVEL` | 日志级别 | `DEBUG`, `INFO`, `WARNING` |

---

## 三种方式对比

| 特性 | Claude Code Agent | Web UI | CLI |
|------|-------------------|--------|-----|
| **使用场景** | 日常开发，交互式对话 | 可视化操作，演示 | 自动化，CI/CD |
| **配置复杂度** | 低（复用 Claude Code 认证） | 中 | 低 |
| **实时反馈** | 优秀（流式输出） | 良好（进度条） | 良好（日志） |
| **可视化** | 文本表格 | 优秀（图形界面） | 文本 |
| **自动化** | 困难 | 困难 | 容易 |

## 推荐组合

- **开发调试**: Claude Code Agent（交互式，快速迭代）
- **演示分享**: Web UI（直观，可视化）
- **生产部署**: CLI（稳定，可脚本化）
