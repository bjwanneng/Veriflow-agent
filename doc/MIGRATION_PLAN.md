# VeriFlow-Agent 迁移计划

> 从传统状态机架构迁移到 LangGraph Agent 架构

## 项目信息

- **源项目**: `C:\Users\wanneng.zhang\Desktop\work\ai_app_zone\Veriflow`
- **目标项目**: `C:\Users\wanneng.zhang\Desktop\work\ai_app_zone\Veriflow-agent`
- **目标架构**: LangGraph + 垂直领域 Agent
- **预计工期**: 4-6 周

---

## 迁移原则

### 1. 渐进式迁移 (Incremental Migration)

```
Phase 1: 并行运行 (2周)
  - 保留原有 veriflow_ctl.py 完整功能
  - 开发新的 LangGraph 版本
  - 对比测试确保行为一致

Phase 2: 功能切换 (1周)
  - 默认使用新架构
  - 保留旧架构作为 fallback

Phase 3: 完全替换 (1周)
  - 移除旧架构代码
  - 清理废弃依赖
```

### 2. 向后兼容 (Backward Compatibility)

- 保留原有的 `project_config.json` 格式
- 保留 `workspace/` 目录结构
- 保留 `requirement.md` 输入格式
- 保留 `prompts/` 提示词文件（复用）

### 3. 领域知识保留

```
可复用资产:
├── prompts/*.md           → 完全复用
├── verilog_flow/          → 复用配置和模板
│   ├── coding_style/
│   ├── templates/
│   └── defaults/
└── tools/                 → 复用脚本逻辑
    ├── run_lint.sh
    ├── run_sim.sh
```

---

## 迁移阶段详解

### Phase 0: 基础设施 (Week 1)

**目标**: 搭建项目骨架，确保开发环境就绪

**任务清单**:

- [ ] 创建项目目录结构
- [ ] 初始化 Git 仓库
- [ ] 创建 Python 虚拟环境
- [ ] 编写 `pyproject.toml` / `requirements.txt`
- [ ] 搭建基础 CI/CD (GitHub Actions)
- [ ] 创建开发文档

**关键依赖**:

```toml
[dependencies]
python = "^3.10"
langgraph = "^0.2.0"
langchain-core = "^0.3.0"
anthropic = "^0.30.0"
pydantic = "^2.0"
click = "^8.0"
pyyaml = "^6.0"
```

---

### Phase 1: 工具层迁移 (Week 1-2)

**目标**: 将 shell 脚本工具转换为 Python 类

**任务清单**:

- [ ] 创建 `veriflow_tools/` 目录
- [ ] 实现 `BaseTool` 抽象类
- [ ] 实现 `IverilogTool` (lint)
- [ ] 实现 `VvpTool` (simulation)
- [ ] 实现 `YosysTool` (synthesis)
- [ ] 编写工具层单元测试
- [ ] 与旧 shell 脚本对比测试

**代码示例**:

```python
# veriflow_tools/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

@dataclass
class ToolResult:
    success: bool
    stdout: str
    stderr: str
    artifacts: dict[str, Any]
    duration_ms: int

class BaseTool(ABC):
    def __init__(self, config: dict = None):
        self.config = config or {}
        self.timeout = self.config.get("timeout", 60)
        
    @abstractmethod
    def run(self, **kwargs) -> ToolResult:
        """执行工具"""
        pass
        
    @abstractmethod
    def validate_prerequisites(self) -> bool:
        """检查工具是否可用"""
        pass
```

---

### Phase 2: Agent 基类与第一个 Agent (Week 2-3)

**目标**: 实现 Agent 框架和第一个 Stage (Skill D - 最简单)

**任务清单**:

- [ ] 创建 `veriflow_agents/` 目录
- [ ] 设计 `BaseAgent` 类
- [ ] 实现 `AgentResult` 数据类
- [ ] 实现 `SkillDAgent` (Stage 3.5)
- [ ] 编写 Agent 单元测试
- [ ] 与旧实现对比测试

**关键设计**:

```python
# veriflow_agents/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional
from pathlib import Path
import json

@dataclass
class AgentResult:
    """Agent 执行结果"""
    success: bool
    stage: str
    artifacts: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "stage": self.stage,
            "artifacts": self.artifacts,
            "metrics": self.metrics,
            "errors": self.errors,
            "warnings": self.warnings
        }


class BaseAgent(ABC):
    """VeriFlow Agent 基类"""
    
    def __init__(
        self,
        name: str,
        prompt_file: str,
        required_inputs: list[str] = None,
        output_artifacts: list[str] = None,
        max_retries: int = 3
    ):
        self.name = name
        self.prompt_file = prompt_file
        self.required_inputs = required_inputs or []
        self.output_artifacts = output_artifacts or []
        self.max_retries = max_retries
        
        # 可配置的 LLM 客户端
        self.llm_client = None
        
    @abstractmethod
    def execute(self, context: dict[str, Any]) -> AgentResult:
        """
        执行 Agent 任务
        
        Args:
            context: 包含 project_dir, inputs, configs 的字典
            
        Returns:
            AgentResult: 标准化的执行结果
        """
        pass
        
    def validate_inputs(self, context: dict[str, Any]) -> tuple[bool, list[str]]:
        """验证输入文件是否存在"""
        project_dir = Path(context.get("project_dir", "."))
        missing = []
        
        for input_file in self.required_inputs:
            full_path = project_dir / input_file
            if not full_path.exists():
                missing.append(str(input_file))
                
        return len(missing) == 0, missing
        
    def validate_outputs(self, context: dict[str, Any]) -> tuple[bool, list[str]]:
        """验证输出文件是否生成"""
        project_dir = Path(context.get("project_dir", "."))
        missing = []
        
        for pattern in self.output_artifacts:
            # 支持 glob 模式
            matches = list((project_dir / "workspace").glob(pattern.replace("workspace/", "")))
            if not matches:
                missing.append(pattern)
                
        return len(missing) == 0, missing
        
    def call_llm(self, context: dict[str, Any], prompt_override: str = None) -> str:
        """
        调用 LLM
        
        支持多种后端:
        - Claude CLI (保留现有方式)
        - Anthropic SDK
        - LangChain/LangGraph 集成
        """
        # 实现复用现有 prompts/stage*.md 的逻辑
        # ...
        pass
```

---

### Phase 3: LangGraph 组装 (Week 3-4)

**任务清单**:

- [ ] 定义 `VeriFlowState` TypedDict
- [ ] 实现所有 Stage Node 函数
- [ ] 实现条件边路由函数
- [ ] 组装完整图结构
- [ ] 添加 checkpoint 持久化
- [ ] 编写集成测试

---

### Phase 4: CLI 与兼容层 (Week 4-5)

**任务清单**:

- [ ] 实现新的 `veriflow` CLI（兼容旧命令）
- [ ] 创建配置迁移脚本
- [ ] 编写向后兼容的 adapter 层
- [ ] 添加详细的错误处理和日志
- [ ] 编写用户文档

---

### Phase 5: 测试与发布 (Week 5-6)

**任务清单**:

- [ ] 编写完整的测试套件（单元/集成/E2E）
- [ ] 在示例项目上进行回归测试
- [ ] 性能基准测试
- [ ] 发布第一个 beta 版本
- [ ] 编写迁移指南

---

## 附录 A: 目录结构对比

### 旧结构 (Veriflow)

```
Veriflow/
├── veriflow_ctl.py          # 2500行，核心控制逻辑
├── veriflow_gui.py          # GUI包装
├── prompts/
│   ├── stage1_architect.md
│   ├── stage2_timing.md
│   └── ...
├── tools/
│   ├── run_lint.sh
│   ├── run_sim.sh
│   └── run_yosys.sh
└── verilog_flow/
    ├── coding_style/
    ├── templates/
    └── defaults/
```

### 新结构 (VeriFlow-Agent)

```
Veriflow-agent/
├── pyproject.toml             # 现代 Python 包配置
├── README.md
├── docs/                      # 文档
│   ├── migration_guide.md
│   ├── architecture.md
│   └── api_reference/
│
├── src/
│   └── veriflow_agent/        # 主包
│       ├── __init__.py
│       ├── __main__.py        # python -m veriflow_agent
│       │
│       ├── cli/               # 命令行接口
│       │   ├── __init__.py
│       │   ├── main.py        # 主 CLI
│       │   ├── run.py         # run 子命令
│       │   └── config.py      # config 子命令
│       │
│       ├── graph/             # LangGraph 核心
│       │   ├── __init__.py
│       │   ├── state.py       # VeriFlowState
│       │   ├── graph.py       # 图定义
│       │   ├── nodes.py       # 节点函数
│       │   ├── edges.py       # 条件边
│       │   └── checkpoint.py  # 持久化
│       │
│       ├── agents/            # Agent 层
│       │   ├── __init__.py
│       │   ├── base.py        # BaseAgent
│       │   ├── architect.py   # Stage 1
│       │   ├── microarch.py   # Stage 1.5
│       │   ├── timing.py      # Stage 2
│       │   ├── coder.py       # Stage 3
│       │   ├── skill_d.py     # Stage 3.5
│       │   ├── debugger.py    # Stage 4
│       │   └── synth.py       # Stage 5
│       │
│       ├── tools/             # 工具层 (ACI)
│       │   ├── __init__.py
│       │   ├── base.py        # BaseTool
│       │   ├── lint.py        # iverilog
│       │   ├── simulate.py    # vvp
│       │   ├── synth.py       # yosys
│       │   └── common.py      # 通用工具
│       │
│       └── legacy/            # 兼容层
│           ├── __init__.py
│           ├── adapter.py     # 旧配置适配
│           └── bridge.py      # 新旧 API 桥接
│
├── prompts/                   # 复用原有提示词
│   ├── stage1_architect.md
│   ├── stage15_microarch.md
│   ├── stage2_timing.md
│   ├── stage3_coder.md
│   ├── stage35_skill_d.md
│   ├── stage4_debugger.md
│   └── supervisor.md
│
├── tests/                     # 测试套件
│   ├── __init__.py
│   ├── conftest.py
│   ├── unit/
│   │   ├── test_agents.py
│   │   ├── test_tools.py
│   │   └── test_graph.py
│   ├── integration/
│   │   ├── test_pipeline.py
│   │   └── test_checkpoint.py
│   └── fixtures/
│       ├── sample_project/
│       └── sample_outputs/
│
├── examples/                  # 示例项目
│   ├── simple_counter/
│   ├── uart_controller/
│   └── aes_core/
│
└── docs/                      # 文档
    ├── architecture.md
    ├── migration_guide.md
    ├── api_reference/
    └── tutorials/
```

---

## 附录 B: 关键文件内容

### B.1. pyproject.toml

```toml
[project]
name = "veriflow-agent"
version = "0.1.0"
description = "Agent-based RTL design pipeline using LangGraph"
readme = "README.md"
license = { text = "MIT" }
authors = [
    { name = "Your Name", email = "your.email@example.com" }
]
requires-python = ">=3.10"
dependencies = [
    "langgraph>=0.2.0",
    "langchain-core>=0.3.0",
    "anthropic>=0.30.0",
    "pydantic>=2.0",
    "click>=8.0",
    "pyyaml>=6.0",
    "rich>=13.0",
    "watchdog>=3.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0",
    "pytest-cov>=4.0",
    "pytest-asyncio>=0.21.0",
    "black>=23.0",
    "ruff>=0.1.0",
    "mypy>=1.0",
    "pre-commit>=3.0",
]

[project.scripts]
veriflow-agent = "veriflow_agent.cli:main"
vfa = "veriflow_agent.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/veriflow_agent"]

[tool.black]
line-length = 100
target-version = ["py310"]

[tool.ruff]
line-length = 100
select = ["E", "F", "I", "N", "W", "UP", "B", "C4", "SIM"]

[tool.mypy]
python_version = "3.10"
strict = true
warn_return_any = true
warn_unused_configs = true

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_functions = ["test_*"]
addopts = "-v --tb=short --strict-markers"
markers = [
    "unit: Unit tests",
    "integration: Integration tests",
    "slow: Slow tests",
]
```

---

## 下一步行动

1. **Review 此文档**：确认迁移计划符合预期
2. **初始化项目**：运行 `mkdir` 和 `git init`
3. **Phase 0 开始**：搭建基础设施
4. **每日同步**：建议每天 Review 进度

有任何问题随时讨论！