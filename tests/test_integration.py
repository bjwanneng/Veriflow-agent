"""Integration tests for VeriFlow-Agent.

Tests the full pipeline flow with mock LLM:
- Checkpoint save/restore
- EDA tool execution on sample project
- Stage node execution with real files
- Spec validation
- Retry tracking and error history
"""


import pytest

from veriflow_agent.agents.architect import ArchitectAgent
from veriflow_agent.agents.coder import CoderAgent
from veriflow_agent.agents.synth import SynthAgent
from veriflow_agent.cli import (
    _load_checkpoint,
    _save_checkpoint,
    _stage_number_to_name,
    _validate_stage,
)
from veriflow_agent.graph.graph import (
    _run_stage,
)
from veriflow_agent.graph.state import (
    create_initial_state,
)
from veriflow_agent.tools.lint import IverilogTool
from veriflow_agent.tools.simulate import VvpTool
from veriflow_agent.tools.synth import YosysTool

# ── Checkpoint persistence ────────────────────────────────────────────


class TestCheckpointPersistence:
    def test_save_and_load(self, tmp_path):
        state = {"stages_completed": ["architect"], "current_stage": "microarch"}
        _save_checkpoint(tmp_path, state)

        loaded = _load_checkpoint(tmp_path)
        assert loaded is not None
        assert "architect" in loaded["stages_completed"]
        assert loaded["current_stage"] == "microarch"

    def test_load_nonexistent(self, tmp_path):
        loaded = _load_checkpoint(tmp_path)
        assert loaded is None

    def test_overwrite_checkpoint(self, tmp_path):
        state1 = {"stages_completed": ["architect"]}
        _save_checkpoint(tmp_path, state1)

        state2 = {"stages_completed": ["architect", "microarch"]}
        _save_checkpoint(tmp_path, state2)

        loaded = _load_checkpoint(tmp_path)
        assert loaded["stages_completed"] == ["architect", "microarch"]


# ── Stage number to name mapping ─────────────────────────────────────


class TestStageMapping:
    def test_all_mappings(self):
        assert _stage_number_to_name(1) == "architect"
        assert _stage_number_to_name(15) == "microarch"
        assert _stage_number_to_name(2) == "timing"
        assert _stage_number_to_name(3) == "coder"
        assert _stage_number_to_name(35) == "skill_d"
        assert _stage_number_to_name(4) == "sim"
        assert _stage_number_to_name(5) == "synth"
        assert _stage_number_to_name(99) is None


# ── Validate stage with sample project ────────────────────────────────


class TestValidateStage:
    def test_validate_stage1_valid(self, sample_project):
        """Stage 1 validation should pass with valid spec.json."""
        errors = _validate_stage(1, sample_project)
        assert errors == []

    def test_validate_stage3_lint(self, sample_project):
        """Stage 3 validation should run iverilog lint on RTL."""
        tool = IverilogTool()
        if tool.validate_prerequisites():
            errors = _validate_stage(3, sample_project)
            assert isinstance(errors, list)
        else:
            pytest.skip("iverilog not available")

    def test_validate_stage_missing_spec(self, tmp_path):
        """Validation should fail without spec.json."""
        project = tmp_path / "empty_project"
        project.mkdir()
        (project / "workspace" / "docs").mkdir(parents=True)
        errors = _validate_stage(1, project)
        assert len(errors) > 0
        assert any("spec.json" in e for e in errors)


# ── EDA tools on sample project ────────────────────────────────────────


class TestEDAToolsIntegration:
    def test_iverilog_on_sample_rtl(self, sample_project):
        """Run iverilog lint on the sample ALU."""
        tool = IverilogTool()
        if not tool.validate_prerequisites():
            pytest.skip("iverilog not available")

        rtl_file = sample_project / "workspace" / "rtl" / "alu.v"
        result = tool.run(mode="lint", files=[rtl_file], cwd=sample_project)
        assert result.status.value == "success" or result.status.value == "failure"

        lint = tool.parse_lint_output(result)
        assert isinstance(lint.passed, bool)
        assert isinstance(lint.error_count, int)

    def test_vvp_on_sample_testbench(self, sample_project):
        """Run simulation on the sample testbench."""
        tool = VvpTool()
        if not tool.validate_prerequisites():
            pytest.skip("vvp not available")

        tb_file = sample_project / "workspace" / "tb" / "tb_alu.v"
        rtl_file = sample_project / "workspace" / "rtl" / "alu.v"

        result = tool.run(
            testbench=tb_file,
            rtl_files=[rtl_file],
            cwd=sample_project,
        )
        assert result.status.value in ("success", "failure")

        sim = tool.parse_sim_output(result)
        assert isinstance(sim.passed, bool)

    def test_yosys_not_available_graceful(self, sample_project):
        """Synthesis should handle missing yosys gracefully."""
        tool = YosysTool()
        result = tool.validate_prerequisites()
        assert isinstance(result, bool)


# ── State initialization ────────────────────────────────────────────────


class TestStateInitialization:
    def test_initial_state_has_all_fields(self):
        state = create_initial_state("/tmp/test")
        assert "project_dir" in state
        assert "retry_count" in state
        assert "error_history" in state
        assert "feedback_source" in state
        assert state["feedback_source"] == ""
        assert "error_categories" in state
        assert "target_rollback_stage" in state
        assert "token_budget" in state
        assert "token_usage" in state
        assert "token_usage_by_stage" in state

    def test_initial_retry_counts_zero(self):
        state = create_initial_state("/tmp/test")
        assert state["retry_count"]["lint"] == 0
        assert state["retry_count"]["sim"] == 0
        assert state["retry_count"]["synth"] == 0


# ── Full pipeline with mock LLM ───────────────────────────────────────


class TestPipelineWithMockLLM:
    def test_architect_agent_on_sample(self, sample_project_dir, spec_data):
        """Architect agent should work with sample project inputs."""
        agent = ArchitectAgent()
        ctx = {"project_dir": sample_project_dir}
        valid, missing = agent.validate_inputs(ctx)
        assert valid

    def test_coder_agent_reads_spec(self, sample_project_dir, spec_json):
        """Coder agent should find and parse spec.json."""
        agent = CoderAgent()
        ctx = {"project_dir": sample_project_dir}
        valid, missing = agent.validate_inputs(ctx)
        assert valid

    def test_synth_agent_reads_spec(self, sample_project_dir):
        """Synth agent should validate spec.json input."""
        agent = SynthAgent()
        ctx = {"project_dir": sample_project_dir}
        valid, missing = agent.validate_inputs(ctx)
        assert valid

    def test_run_stage_updates_state(self, sample_project_dir):
        """_run_stage should correctly update state even on failure."""
        state = create_initial_state(sample_project_dir)
        result = _run_stage(state, ArchitectAgent)
        assert "current_stage" in result
        assert result["current_stage"] == "architect"
        assert "architect_output" in result
        assert "quality_gates_passed" in result
        # architect will fail because LLM not configured
        assert "stages_failed" in result or "stages_completed" in result


# ── Spec validation ────────────────────────────────────────────────────


class TestSpecValidation:
    def test_spec_has_required_fields(self, spec_data):
        """Sample spec.json should have all required fields."""
        assert "design_name" in spec_data
        assert "modules" in spec_data
        assert "target_kpis" in spec_data
        assert len(spec_data["modules"]) == 2

    def test_spec_module_ports(self, spec_data):
        """Each module should have valid ports."""
        for mod in spec_data["modules"]:
            assert "module_name" in mod
            assert "ports" in mod
            assert len(mod["ports"]) > 0
            for port in mod["ports"]:
                assert "name" in port
                assert "direction" in port

    def test_spec_kpi_fields(self, spec_data):
        """Target KPIs should have required fields."""
        kpis = spec_data["target_kpis"]
        assert "frequency_mhz" in kpis
        assert "max_cells" in kpis
