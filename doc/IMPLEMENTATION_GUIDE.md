# VeriFlow-Agent 实施指南

> 详细的代码实现指南和逐步实施步骤

---

## 目录

1. [环境搭建](#1-环境搭建)
2. [项目初始化](#2-项目初始化)
3. [核心模块实现](#3-核心模块实现)
4. [Agent 开发](#4-agent-开发)
5. [图结构构建](#5-图结构构建)
6. [测试与验证](#6-测试与验证)

---

## 1. 环境搭建

### 1.1 Python 版本要求

```bash
# 检查 Python 版本 (需要 >=3.10)
python --version  # 或 python3 --version

# 如果不符合，使用 pyenv 安装
pyenv install 3.11.8
pyenv local 3.11.8
```

### 1.2 虚拟环境创建

```bash
# 进入项目目录
cd C:\Users\wanneng.zhang\Desktop\work\ai_app_zone\Veriflow-agent

# 创建虚拟环境
python -m venv .venv

# 激活虚拟环境
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 验证激活成功
which python  # 应该显示 .venv 路径
```

### 1.3 安装核心依赖

```bash
# 安装 langgraph 和相关包
pip install langgraph langchain-core langchain-anthropic

# 安装其他核心依赖
pip install pydantic click pyyaml rich watchdog

# 安装开发依赖
pip install pytest pytest-cov pytest-asyncio black ruff mypy pre-commit

# 生成 requirements.txt
pip freeze > requirements.txt
```

---

## 2. 项目初始化

### 2.1 目录结构创建

```bash
# 创建完整目录结构
mkdir -p src/veriflow_agent/{cli,graph,agents,tools,legacy}
mkdir -p prompts tests/{unit,integration,e2e,fixtures}
mkdir -p examples docs/{api,tutorials,architecture}
mkdir -p scripts

# 创建空 __init__.py 文件
touch src/veriflow_agent/__init__.py
touch src/veriflow_agent/cli/__init__.py
touch src/veriflow_agent/graph/__init__.py
touch src/veriflow_agent/agents/__init__.py
touch src/veriflow_agent/tools/__init__.py
touch src/veriflow_agent/legacy/__init__.py
```

### 2.2 配置文件创建

#### pyproject.toml

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "veriflow-agent"
dynamic = ["version"]
description = "Agent-based RTL design pipeline using LangGraph"
readme = "README.md"
license = { text = "MIT" }
requires-python = ">=3.10"
authors = [
    { name = "Your Name", email = "your.email@example.com" }
]
keywords = ["rtl", "verilog", "eda", "agent", "langgraph", "ai"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Topic :: Scientific/Engineering :: Electronic Design Automation (EDA)",
]

dependencies = [
    "langgraph>=0.2.0",
    "langchain-core>=0.3.0",
    "langchain-anthropic>=0.1.0",
    "anthropic>=0.30.0",
    "pydantic>=2.0",
    "click>=8.0",
    "pyyaml>=6.0",
    "rich>=13.0",
    "watchdog>=3.0",
    "typing-extensions>=4.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0",
    "pytest-cov>=4.0",
    "pytest-asyncio>=0.21.0",
    "pytest-mock>=3.0",
    "black>=23.0",
    "ruff>=0.1.0",
    "mypy>=1.0",
    "pre-commit>=3.0",
    "types-pyyaml",
]
docs = [
    "mkdocs>=1.5",
    "mkdocs-material>=9.0",
    "mkdocstrings[python]>=0.20",
]

[project.scripts]
veriflow-agent = "veriflow_agent.cli.main:main"
vfa = "veriflow_agent.cli.main:main"

[project.urls]
Homepage = "https://github.com/yourusername/veriflow-agent"
Documentation = "https://veriflow-agent.readthedocs.io"
Repository = "https://github.com/yourusername/veriflow-agent"
Issues = "https://github.com/yourusername/veriflow-agent/issues"

[tool.hatch.version]
path = "src/veriflow_agent/__init__.py"

[tool.hatch.build.targets.wheel]
packages = ["src/veriflow_agent"]

[tool.black]
line-length = 100
target-version = ["py310", "py311", "py312"]
include = '\.pyi?$'
extend-exclude = '''
/(
  # directories
  \.eggs
  | \.git
  | \.hg
  | \.mypy_cache
  | \.tox
  | \.venv
  | build
  | dist
)/
'''

[tool.ruff]
line-length = 100
target-version = "py310"
select = [
    "E",   # pycodestyle errors
    "F",   # Pyflakes
    "I",   # isort
    "N",   # pep8-naming
    "W",   # pycodestyle warnings
    "UP",  # pyupgrade
    "B",   # flake8-bugbear
    "C4",  # flake8-comprehensions
    "SIM", # flake8-simplify
    "ARG", # flake8-unused-arguments
]
ignore = [
    "E501",  # line too long, handled by black
]

[tool.ruff.pydocstyle]
convention = "google"

[tool.ruff.isort]
known-first-party = ["veriflow_agent"]

[tool.mypy]
python_version = "3.10"
strict = true
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
disallow_incomplete_defs = true
check_untyped_defs = true
disallow_untyped_decorators = true
warn_redundant_casts = true
warn_unused_ignores = true
warn_no_return = true
warn_unreachable = true
ignore_missing_imports = true

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_functions = ["test_*"]
addopts = "-v --tb=short --strict-markers"
markers = [
    "unit: Unit tests",
    "integration: Integration tests",
    "e2e: End-to-end tests",
    "slow: Slow tests that should be run separately",
]

[tool.coverage.run]
source = ["src/veriflow_agent"]
branch = true

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "raise AssertionError",
    "raise NotImplementedError",
    "if __name__ == .__main__.:",
    "if TYPE_CHECKING:",
]
show_missing = true
skip_covered = false
```

---

## 下一步行动

1. **Review 计划**: 确认迁移计划和实施指南符合预期
2. **初始化仓库**: 运行初始化命令
3. **开始 Phase 0**: 搭建基础设施
4. **每日同步**: 建议每天晚上 Review 进度

有任何问题随时讨论！