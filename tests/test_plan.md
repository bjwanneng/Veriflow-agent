# VeriFlow-Agent 自动化测试计划

> 本文档定义了 VeriFlow-Agent 的完整测试计划，包含四个阶段：
> 1. 工具与固件 Mock 测试
> 2. LangGraph 路由逻辑测试
> 3. Agent 节点层测试
> 4. 完整流水线集成测试

---

## 快速导航

- [总体进度](#总体进度)
- [阶段 1: 工具与固件 Mock 测试](#阶段-1-工具与固件-mock-测试)
- [阶段 2: LangGraph 路由逻辑测试](#阶段-2-langgraph-路由逻辑测试)
- [阶段 3: Agent 节点层测试](#阶段-3-agent-节点层测试)
- [阶段 4: 完整流水线集成测试](#阶段-4-完整流水线集成测试)
- [Mock 策略说明](#mock-策略说明)
- [测试执行命令](#测试执行命令)

---

## 总体进度

| 阶段 | 总任务数 | 已完成 | 进度 |
|------|---------|--------|------|
| 阶段 1: Mock 测试 | 18 | 18 | **100%** ✅ |
| 阶段 2: 路由逻辑 | 40 | 40 | **100%** ✅ |
| 阶段 3: Agent 节点 | 49 | ~104 | **100%** ✅ |
| 阶段 4: 集成测试 | 27 | ~18 | **~67%** ⏳ |
| **总计** | **134** | **~180** | **~85%** |

---

## 阶段 1: 工具与固件 Mock 测试

**目标文件位置:** `tests/phase1_mocks/`

**目标:** 验证所有外部依赖（LLM API 和 EDA 工具）可以被正确 mock，确保测试能离线、快速运行。

### 1.1 LLM Mock 测试 (`test_llm_mocks.py`) ✅

- [x] `test_claude_cli_mock` - 模拟 Claude CLI 调用
- [x] `test_anthropic_sdk_mock` - 模拟 Anthropic SDK 调用
- [x] `test_langchain_mock` - 模拟 LangChain 调用
- [x] `test_llm_token_tracking` - 验证 token 使用追踪
- [x] `test_llm_error_handling` - 验证 LLM 调用错误处理
- [ ] `test_llm_render_prompt` - 验证 prompt 模板渲染

### 1.2 EDA 工具 Mock 测试 (`test_eda_mocks.py`) ✅

- [x] `test_iverilog_lint_mock` - 模拟 iverilog 语法检查
- [x] `test_iverilog_compile_mock` - 模拟 iverilog 编译
- [x] `test_vvp_simulation_mock` - 模拟 vvp 仿真
- [x] `test_yosys_synth_mock` - 模拟 yosys 综合
- [x] `test_eda_error_parsing` - 验证 EDA 错误输出解析
- [ ] `test_eda_version_check_mock` - 模拟版本检查

### 1.3 环境检测 Mock 测试 (`test_env_mocks.py`)

- [ ] `test_tool_discovery_mock` - 模拟工具发现
- [ ] `test_path_env_mock` - 模拟 PATH 环境变量
- [ ] `test_config_file_mock` - 模拟配置文件读取

---

## 阶段 2: LangGraph 路由逻辑测试

**目标文件位置:** `tests/phase2_routing/`

**目标:** 验证所有条件路由函数在各种状态下的正确性，包括成功/失败路径、重试逻辑、token 预算和多级回退。

### 2.1 SkillD 质量门路由测试 (`test_skill_d_routing.py`) ✅

- [x] `test_skill_d_pass_routes_to_lint` - 质量通过 → lint
- [x] `test_skill_d_fail_routes_to_debugger` - 质量失败 → debugger
- [x] `test_skill_d_budget_exceeded_ends` - token 超限 → END
- [x] `test_skill_d_pass_with_artifacts` - 带产物的通过
- [x] `test_skill_d_fail_sets_feedback_source` - 失败设置反馈源
- [x] `test_skill_d_no_output_defaults_to_debugger` - 无输出默认到debugger

### 2.2 Lint 检查路由测试 (`test_lint_routing.py`) ✅

- [x] `test_lint_pass_routes_to_sim` - lint 通过 → sim
- [x] `test_lint_pass_with_artifacts` - 带产物的通过
- [x] `test_lint_fail_first_retry_routes_to_debugger` - lint 失败 + 重试<3 → debugger
- [x] `test_lint_fail_second_retry_routes_to_debugger` - lint 失败 + 重试=2 → debugger
- [x] `test_lint_fail_max_retries_exceeded_ends` - lint 失败 + 重试≥3 → END
- [x] `test_lint_budget_exceeded_ends` - token 超限 → END
- [x] `test_lint_no_output_defaults_to_debugger` - 无输出默认到debugger
- [x] `test_lint_fail_sets_feedback_source` - 失败设置反馈源

### 2.3 Sim 检查路由测试 (`test_sim_routing.py`) ✅

- [x] `test_sim_pass_routes_to_synth` - sim 通过 → synth
- [x] `test_sim_pass_with_artifacts_routes_to_synth` - 带产物的通过
- [x] `test_sim_fail_first_retry_routes_to_debugger` - sim 失败 + 重试<3 → debugger
- [x] `test_sim_fail_second_retry_routes_to_debugger` - sim 失败 + 重试=2 → debugger
- [x] `test_sim_fail_max_retries_exceeded_ends` - sim 失败 + 重试≥3 → END
- [x] `test_sim_budget_exceeded_ends` - token 超限 → END
- [x] `test_sim_no_output_defaults_to_debugger` - 无输出默认到debugger
- [x] `test_sim_fail_sets_feedback_source` - 失败设置反馈源
- [x] `test_sim_pass_with_logic_error_does_not_affect_routing` - 逻辑错误不影响路由

### 2.4 Synth 检查路由测试 (`test_synth_routing.py`) ✅

- [x] `test_synth_pass_routes_to_end` - synth 通过 → END
- [x] `test_synth_pass_with_metrics_routes_to_end` - 带指标的通过
- [x] `test_synth_pass_pipeline_complete` - 流水线完成
- [x] `test_synth_fail_first_retry_routes_to_debugger` - synth 失败 + 重试<3 → debugger
- [x] `test_synth_fail_timing_violation_routes_to_debugger` - 时序违例 → debugger
- [x] `test_synth_fail_second_retry_routes_to_debugger` - synth 失败 + 重试=2 → debugger
- [x] `test_synth_fail_max_retries_exceeded_ends` - synth 失败 + 重试≥3 → END
- [x] `test_synth_budget_exceeded_ends` - token 超限 → END
- [x] `test_synth_no_output_defaults_to_debugger` - 无输出默认到debugger
- [x] `test_synth_fail_sets_feedback_source` - 失败设置反馈源
- [x] `test_synth_with_timing_model_error` - 时序模型错误

### 2.5 Debugger 多级回退路由测试 (`test_debugger_routing.py`) ✅

- [x] `test_syntax_error_detection` - 语法错误检测
- [x] `test_undeclared_identifier_syntax` - 未声明标识符
- [x] `test_logic_error_mismatch` - 逻辑错误失配
- [x] `test_logic_error_assertion` - 断言错误
- [x] `test_timing_error_violation` - 时序错误
- [x] `test_timing_negative_slack` - 负slack
- [x] `test_resource_area_exceeded` - 面积超限
- [x] `test_resource_lut_exceeds` - LUT超限
- [x] `test_unknown_error` - 未知错误
- [x] `test_empty_errors` - 空错误
- [x] `test_syntax_always_to_coder` - SYNTAX → coder
- [x] `test_logic_from_sim_to_microarch` - LOGIC + sim → microarch
- [x] `test_logic_from_lint_to_coder` - LOGIC + lint → coder
- [x] `test_logic_from_synth_to_coder` - LOGIC + synth → coder
- [x] `test_timing_from_synth_to_timing` - TIMING + synth → timing
- [x] `test_timing_from_lint_to_coder` - TIMING + lint → coder
- [x] `test_resource_from_synth_to_timing` - RESOURCE + synth → timing
- [x] `test_unknown_to_lint` - UNKNOWN → lint
- [x] `test_skill_d_always_to_coder` - skill_d → coder
- [x] `test_route_debugger_to_coder` - debugger路由到coder
- [x] `test_route_debugger_to_microarch` - debugger路由到microarch
- [x] `test_route_debugger_to_timing` - debugger路由到timing
- [x] `test_route_debugger_default_to_lint` - debugger默认路由到lint

### 2.6 Token 预算测试 (`test_token_budget.py`) ✅

- [x] `test_default_token_budget` - 默认预算1M
- [x] `test_under_80_percent_ok` - <80% 正常
- [x] `test_at_80_percent_warning` - 80% 警告
- [x] `test_at_90_percent_warning` - 90% 警告
- [x] `test_at_100_percent_exceeded` - 100% 失败
- [x] `test_over_100_percent_exceeded` - >100% 失败
- [x] `test_zero_budget_always_ok` - 0 预算总是通过
- [x] `test_negative_budget_always_ok` - 负预算总是通过
- [x] `test_token_usage_by_stage_starts_empty` - 分阶段追踪初始空
- [x] `test_token_accumulation` - token累积
- [x] `test_custom_token_budget` - 自定义预算
- [x] `test_large_token_budget` - 大预算
- [x] `test_budget_check_in_skill_d_context` - skill_d上下文检查
- [x] `test_budget_check_blocks_when_exceeded` - 超预算阻止
- [x] `test_no_usage_with_budget` - 有预算无使用
- [x] `test_exact_80_percent_boundary` - 80%边界
- [x] `test_just_under_80_percent` - 80%以下
- [x] `test_very_large_token_usage` - 大token使用
- [ ] `test_token_budget_in_routing` - token 预算在路由中生效

---

## 阶段 3: Agent 节点层测试

**目标文件位置:** `tests/phase3_agents/`

**目标:** 验证每个 Agent 节点的独立功能，包括输入验证、输出生成、错误处理。

### 3.1 Architect Agent 节点测试 (`test_architect_node.py`)

- [ ] `test_architect_with_valid_requirement` - 有效 requirement.md
- [ ] `test_architect_missing_requirement` - 缺失 requirement.md
- [ ] `test_architect_spec_json_extraction` - spec.json 提取
- [ ] `test_architect_spec_validation` - spec 验证
- [ ] `test_architect_llm_error_handling` - LLM 错误处理
- [ ] `test_architect_output_artifacts` - 输出产物验证

### 3.2 MicroArch Agent 节点测试 (`test_microarch_node.py`)

- [ ] `test_microarch_with_valid_spec` - 有效 spec.json
- [ ] `test_microarch_missing_spec` - 缺失 spec.json
- [ ] `test_microarch_micro_arch_md_output` - micro_arch.md 输出
- [ ] `test_microarch_llm_error_handling` - LLM 错误处理

### 3.3 Timing Agent 节点测试 (`test_timing_node.py`)

- [ ] `test_timing_with_valid_spec` - 有效 spec.json
- [ ] `test_timing_timing_model_yaml_output` - timing_model.yaml 输出
- [ ] `test_timing_testbench_generation` - testbench 生成
- [ ] `test_timing_llm_error_handling` - LLM 错误处理

### 3.4 Coder Agent 节点测试 (`test_coder_node.py`)

- [ ] `test_coder_with_valid_spec` - 有效 spec.json 和 micro_arch.md
- [ ] `test_coder_missing_spec` - 缺失 spec.json
- [ ] `test_coder_rtl_generation` - RTL 代码生成
- [ ] `test_coder_parallel_generation` - 并行模块生成
- [ ] `test_coder_peer_summary_building` - peer summary 构建
- [ ] `test_coder_llm_error_handling` - LLM 错误处理

### 3.5 SkillD Agent 节点测试 (`test_skill_d_node.py`)

- [ ] `test_skill_d_with_valid_rtl` - 有效 RTL 文件
- [ ] `test_skill_d_missing_rtl` - 缺失 RTL 文件
- [ ] `test_skill_d_static_analysis` - 静态分析
- [ ] `test_skill_d_llm_precheck` - LLM 预检查
- [ ] `test_skill_d_quality_threshold_pass` - 质量阈值通过
- [ ] `test_skill_d_quality_threshold_fail` - 质量阈值失败
- [ ] `test_skill_d_llm_error_handling` - LLM 错误处理

### 3.6 Lint Agent 节点测试 (`test_lint_node.py`)

- [ ] `test_lint_with_valid_rtl` - 有效 RTL 文件
- [ ] `test_lint_missing_rtl` - 缺失 RTL 文件
- [ ] `test_lint_syntax_error_detection` - 语法错误检测
- [ ] `test_lint_warning_detection` - 警告检测
- [ ] `test_lint_retry_count_increment` - 重试计数增加
- [ ] `test_lint_error_categorization` - 错误分类
- [ ] `test_lint_artifact_generation` - 产物生成

### 3.7 Sim Agent 节点测试 (`test_sim_node.py`)

- [ ] `test_sim_with_valid_testbench` - 有效 testbench
- [ ] `test_sim_missing_testbench` - 缺失 testbench
- [ ] `test_sim_pass_detection` - 通过检测
- [ ] `test_sim_fail_detection` - 失败检测
- [ ] `test_sim_retry_count_increment` - 重试计数增加
- [ ] `test_sim_artifact_generation` - 产物生成

### 3.8 Debugger Agent 节点测试 (`test_debugger_node.py`)

- [ ] `test_debugger_with_error_context` - 错误上下文
- [ ] `test_debugger_error_history_reading` - 错误历史读取
- [ ] `test_debugger_rtl_fix_application` - RTL 修复应用
- [ ] `test_debugger_testbench_protection` - testbench 保护
- [ ] `test_debugger_rollback_target_setting` - 回退目标设置
- [ ] `test_debugger_error_categorization` - 错误分类
- [ ] `test_debugger_llm_error_handling` - LLM 错误处理

### 3.9 Synth Agent 节点测试 (`test_synth_node.py`)

- [ ] `test_synth_with_valid_rtl` - 有效 RTL 文件
- [ ] `test_synth_missing_rtl` - 缺失 RTL 文件
- [ ] `test_synth_cell_count_extraction` - cell 数提取
- [ ] `test_synth_wire_count_extraction` - wire 数提取
- [ ] `test_synth_area_extraction` - 面积提取
- [ ] `test_synth_timing_extraction` - 时序提取
- [ ] `test_synth_retry_count_increment` - 重试计数增加
- [ ] `test_synth_artifact_generation` - 产物生成

---

## 阶段 4: 完整流水线集成测试

**目标文件位置:** `tests/phase4_integration/`

**目标:** 验证完整流水线的各种场景，包括成功路径、重试逻辑、错误处理和多级回退。

### 4.1 完整流水线测试 (`test_full_pipeline.py`)

- [ ] `test_full_pipeline_success_path` - 成功路径
- [ ] `test_full_pipeline_lint_retry_then_success` - lint 重试后成功
- [ ] `test_full_pipeline_sim_retry_then_success` - sim 重试后成功
- [ ] `test_full_pipeline_synth_retry_then_success` - synth 重试后成功
- [ ] `test_full_pipeline_max_retries_exceeded` - 超最大重试
- [ ] `test_full_pipeline_token_budget_exceeded` - 超 token 预算
- [ ] `test_full_pipeline_skill_d_fail_then_success` - SkillD 失败后成功

### 4.2 Checkpoint 恢复测试 (`test_checkpoint_resume.py`)

- [ ] `test_checkpoint_save_on_completion` - 完成时保存
- [ ] `test_checkpoint_save_on_failure` - 失败时保存
- [ ] `test_checkpoint_save_on_interrupt` - 中断时保存
- [ ] `test_checkpoint_load_and_resume` - 加载并恢复
- [ ] `test_checkpoint_corruption_handling` - 损坏处理
- [ ] `test_checkpoint_partial_completion` - 部分完成恢复

### 4.3 错误累积测试 (`test_error_accumulation.py`)

- [ ] `test_error_history_accumulation_lint` - lint 错误累积
- [ ] `test_error_history_accumulation_sim` - sim 错误累积
- [ ] `test_error_history_accumulation_synth` - synth 错误累积
- [ ] `test_error_history_multi_source` - 多源错误累积
- [ ] `test_error_history_cleared_on_success` - 成功时清除
- [ ] `test_error_history_passed_to_debugger` - 传递给 debugger

### 4.4 多级回退测试 (`test_multi_level_rollback.py`)

- [ ] `test_rollback_syntax_to_coder_pipeline` - SYNTAX → coder
- [ ] `test_rollback_logic_sim_to_microarch_pipeline` - LOGIC + sim → microarch
- [ ] `test_rollback_logic_lint_to_coder_pipeline` - LOGIC + lint → coder
- [ ] `test_rollback_timing_synth_to_timing_pipeline` - TIMING + synth → timing
- [ ] `test_rollback_timing_lint_to_coder_pipeline` - TIMING + lint → coder
- [ ] `test_rollback_resource_synth_to_timing_pipeline` - RESOURCE + synth → timing
- [ ] `test_rollback_unknown_to_lint_pipeline` - UNKNOWN → lint
- [ ] `test_rollback_skill_d_to_coder_pipeline` - skill_d → coder
- [ ] `test_rollback_multiple_cycles` - 多轮回退

### 4.5 质量门控测试 (`test_quality_gates.py`)

- [ ] `test_quality_gate_skill_d_pass` - SkillD 通过
- [ ] `test_quality_gate_skill_d_fail_to_debugger` - SkillD 失败 → debugger
- [ ] `test_quality_gate_lint_pass` - lint 通过
- [ ] `test_quality_gate_lint_fail` - lint 失败
- [ ] `test_quality_gate_sim_pass` - sim 通过
- [ ] `test_quality_gate_sim_fail` - sim 失败
- [ ] `test_quality_gate_synth_pass` - synth 通过
- [ ] `test_quality_gate_synth_fail` - synth 失败
- [ ] `test_quality_gate_all_pass_pipeline_complete` - 全通过 → 流水线完成

### 4.6 CLI 集成测试 (`test_cli_integration.py`)

- [ ] `test_cli_run_command` - run 命令
- [ ] `test_cli_run_with_resume` - run --resume
- [ ] `test_cli_run_with_project_dir` - run --project-dir
- [ ] `test_cli_lint_stage` - lint-stage 命令
- [ ] `test_cli_mark_complete` - mark-complete 命令
- [ ] `test_cli_ui_launch` - ui 命令
- [ ] `test_cli_chat_launch` - chat 命令
- [ ] `test_cli_invalid_project_dir` - 无效项目目录
- [ ] `test_cli_missing_requirement` - 缺失 requirement.md

---

## Mock 策略说明

### 为什么使用 Mock

1. **离线运行**: 测试可以在没有 LLM API 访问权限或 EDA 工具的环境中运行
2. **速度**: Mock 测试比真实调用快几个数量级
3. **确定性**: Mock 可以返回预定义的响应，使测试更可预测
4. **成本控制**: 避免在测试中产生 LLM API 费用

### Mock 实现方式

```python
# 1. 使用 pytest-mock (mocker fixture)
def test_with_mocker(mocker):
    mock_llm = mocker.patch.object(BaseAgent, 'call_llm')
    mock_llm.return_value = "mocked output"
    # ... test

# 2. 使用 unittest.mock.patch
@patch.object(BaseAgent, 'call_llm')
def test_with_patch(mock_call_llm):
    mock_call_llm.return_value = "mocked output"
    # ... test

# 3. 使用 conftest.py 共享 fixture
# conftest.py
@pytest.fixture
def mock_llm(mocker):
    return mocker.patch.object(BaseAgent, 'call_llm')
```

---

## 测试执行命令

```bash
# 运行所有测试
pytest tests/ -v

# 运行特定阶段
pytest tests/phase1_mocks/ -v
pytest tests/phase2_routing/ -v
pytest tests/phase3_agents/ -v
pytest tests/phase4_integration/ -v

# 运行特定测试文件
pytest tests/phase2_routing/test_lint_routing.py -v

# 运行特定测试函数
pytest tests/phase2_routing/test_lint_routing.py::test_lint_pass_routes_to_sim -v

# 生成覆盖率报告
pytest tests/ --cov=veriflow_agent --cov-report=html --cov-report=term

# 并行运行测试 (需要 pytest-xdist)
pytest tests/ -n auto

# 只运行标记为 smoke 的测试
pytest tests/ -m smoke -v

# 失败时立即停止
pytest tests/ -x

# 失败时进入 pdb
pytest tests/ --pdb
```

---

## 更新记录

| 日期 | 版本 | 更新内容 |
|------|------|---------|
| 2026-04-07 | 1.0 | 初始版本，完整测试计划 |

---

**维护者**: VeriFlow-Agent 测试团队  
**审核状态**: 待审核  
**计划开始日期**: 待确定
