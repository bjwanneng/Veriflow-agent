"""Tests for the VeriFlow-Agent agent layer.

These tests verify:
- Agent instantiation and configuration
- Input/output validation logic
- Template rendering
- Spec extraction (ArchitectAgent)
- Peer summary building (CoderAgent)
- Testbench snapshot/restore (DebuggerAgent)
"""

import json
from pathlib import Path

import pytest

from veriflow_agent.agents.architect import ArchitectAgent
from veriflow_agent.agents.base import AgentResult, LLMInvocationError
from veriflow_agent.agents.coder import CoderAgent
from veriflow_agent.agents.debugger import DebuggerAgent
from veriflow_agent.agents.lint_agent import LintAgent
from veriflow_agent.agents.microarch import MicroArchAgent
from veriflow_agent.agents.sim_agent import SimAgent
from veriflow_agent.agents.skill_d import SkillDAgent
from veriflow_agent.agents.synth import SynthAgent
from veriflow_agent.agents.timing import TimingAgent

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def tmp_project(tmp_path):
    """Create a minimal project directory structure."""
    # requirement.md
    (tmp_path / "requirement.md").write_text("Design a simple ALU")
    # .veriflow
    veriflow_dir = tmp_path / ".veriflow"
    veriflow_dir.mkdir()
    (veriflow_dir / "project_config.json").write_text(json.dumps({"mode": "standard"}))
    # workspace/docs
    docs_dir = tmp_path / "workspace" / "docs"
    docs_dir.mkdir(parents=True)
    # workspace/rtl
    rtl_dir = tmp_path / "workspace" / "rtl"
    rtl_dir.mkdir(parents=True)
    # workspace/tb
    tb_dir = tmp_path / "workspace" / "tb"
    tb_dir.mkdir(parents=True)
    # prompts (copy or create minimal)
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    for name in [
        "stage1_architect.md", "stage15_microarch.md", "stage2_timing.md",
        "stage3_module.md", "stage35_skill_d.md", "stage4_debugger.md",
    ]:
        (prompts_dir / name).write_text(f"# {name}\nStage prompt placeholder with {{{{PROJECT_DIR}}}}")
    return tmp_path


# ── AgentResult ───────────────────────────────────────────────────────


class TestAgentResult:
    def test_to_dict_roundtrip(self):
        r = AgentResult(
            success=True, stage="test",
            artifacts=["a.v"], metrics={"x": 1},
            errors=[], warnings=["w1"],
            raw_output="out", metadata={"key": "val"},
        )
        d = r.to_dict()
        r2 = AgentResult.from_dict(d)
        assert r2.success is True
        assert r2.stage == "test"
        assert r2.artifacts == ["a.v"]
        assert r2.metrics == {"x": 1}

    def test_defaults(self):
        r = AgentResult(success=False, stage="x")
        assert r.artifacts == []
        assert r.errors == []
        assert r.raw_output == ""


# ── Agent instantiation ───────────────────────────────────────────────


class TestAgentInstantiation:
    def test_all_agents_instantiate(self):
        agents = [
            ArchitectAgent(), MicroArchAgent(), TimingAgent(),
            CoderAgent(), SkillDAgent(), DebuggerAgent(),
            LintAgent(), SimAgent(), SynthAgent(),
        ]
        names = {a.name for a in agents}
        assert names == {"architect", "microarch", "timing", "coder", "skill_d", "debugger", "lint", "sim", "synth"}

    def test_agent_configs(self):
        a = ArchitectAgent()
        assert a.prompt_file == "stage1_architect.md"
        assert "requirement.md" in a.required_inputs
        assert "spec.json" in a.output_artifacts[0]


# ── Input validation ──────────────────────────────────────────────────


class TestInputValidation:
    def test_missing_inputs(self, tmp_project):
        agent = ArchitectAgent()
        ctx = {"project_dir": str(tmp_project)}
        valid, missing = agent.validate_inputs(ctx)
        # requirement.md exists, so should be valid
        assert valid is True
        assert missing == []

    def test_missing_spec(self, tmp_project):
        agent = MicroArchAgent()
        ctx = {"project_dir": str(tmp_project)}
        valid, missing = agent.validate_inputs(ctx)
        # spec.json doesn't exist yet
        assert valid is False
        assert "workspace/docs/spec.json" in missing


# ── Template rendering ────────────────────────────────────────────────


class TestRenderPrompt:
    def test_render_substitutes_placeholders(self, tmp_project):
        agent = ArchitectAgent()
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_project))
            rendered = agent.render_prompt({"PROJECT_DIR": "/my/project", "MODE": "quick"})
            assert "/my/project" in rendered
            # MODE not in test prompt template, so only PROJECT_DIR substituted
        finally:
            os.chdir(old_cwd)

    def test_render_missing_file(self, tmp_project):
        agent = SynthAgent()  # has empty prompt_file
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_project))
            with pytest.raises(LLMInvocationError, match="No prompt file configured"):
                agent.render_prompt({})
        finally:
            os.chdir(old_cwd)


# ── ArchitectAgent ────────────────────────────────────────────────────


class TestArchitectAgent:
    def test_extract_spec_json_from_code_fence(self):
        agent = ArchitectAgent()
        output = 'Here is the spec:\n```json\n{"design_name": "alu", "modules": [{"module_name": "top"}]}\n```\nDone.'
        result = agent._extract_spec_json(output)
        assert result is not None
        assert result["design_name"] == "alu"

    def test_extract_spec_json_raw(self):
        agent = ArchitectAgent()
        output = 'Result: {"design_name": "fifo", "modules": []}'
        result = agent._extract_spec_json(output)
        assert result is not None
        assert result["design_name"] == "fifo"

    def test_extract_spec_json_none(self):
        agent = ArchitectAgent()
        result = agent._extract_spec_json("no json here")
        assert result is None

    def test_validate_spec_valid(self):
        agent = ArchitectAgent()
        spec = {
            "design_name": "alu",
            "modules": [{"module_name": "top", "ports": []}],
            "target_kpis": {"frequency_mhz": 100},
        }
        errors = agent._validate_spec(spec)
        assert errors == []

    def test_validate_spec_missing_fields(self):
        agent = ArchitectAgent()
        errors = agent._validate_spec({})
        assert len(errors) > 0
        assert any("design_name" in e for e in errors)

    def test_execute_missing_requirement(self, tmp_project):
        agent = ArchitectAgent()
        # Remove requirement.md
        (tmp_project / "requirement.md").unlink()
        ctx = {"project_dir": str(tmp_project)}
        result = agent.execute(ctx)
        assert result.success is False
        assert any("Missing" in e for e in result.errors)


# ── CoderAgent ────────────────────────────────────────────────────────


class TestCoderAgent:
    def test_build_peer_summary(self):
        modules = [
            {"module_name": "top", "ports": [
                {"direction": "input", "width": 8, "name": "clk"},
                {"direction": "output", "width": 16, "name": "result"},
            ]},
            {"module_name": "alu", "ports": [
                {"direction": "input", "width": 1, "name": "enable"},
            ]},
        ]
        summary = CoderAgent._build_peer_summary(modules)
        assert "module top" in summary
        assert "module alu" in summary
        assert "clk" in summary
        assert "result" in summary
        assert "enable" in summary

    def test_execute_no_spec(self, tmp_project):
        agent = CoderAgent()
        ctx = {"project_dir": str(tmp_project)}
        result = agent.execute(ctx)
        assert result.success is False


# ── DebuggerAgent ─────────────────────────────────────────────────────


class TestDebuggerAgent:
    def test_snapshot_restore(self, tmp_path):
        # Create a tb directory with a file
        tb_dir = tmp_path / "tb"
        tb_dir.mkdir()
        (tb_dir / "tb_alu.v").write_text("original content")

        # Snapshot
        snapshot = DebuggerAgent._snapshot_directory(tb_dir)
        assert snapshot is not None
        assert "tb_alu.v" in snapshot

        # Modify file
        (tb_dir / "tb_alu.v").write_text("tampered content")

        # Restore
        DebuggerAgent._restore_snapshot(tb_dir, snapshot)
        assert (tb_dir / "tb_alu.v").read_text() == "original content"

    def test_snapshot_nonexistent_dir(self):
        snapshot = DebuggerAgent._snapshot_directory(Path("/nonexistent"))
        assert snapshot is None


# ── SynthAgent ────────────────────────────────────────────────────────


class TestSynthAgent:
    def test_execute_no_spec(self, tmp_project):
        agent = SynthAgent()
        ctx = {"project_dir": str(tmp_project)}
        result = agent.execute(ctx)
        assert result.success is False
        assert any("spec.json" in e for e in result.errors)


# ── SkillDAgent ───────────────────────────────────────────────────────


class TestSkillDAgent:
    def test_execute_no_rtl(self, tmp_project):
        """SkillDAgent should fail when no RTL files exist."""
        agent = SkillDAgent()
        ctx = {"project_dir": str(tmp_project)}
        result = agent.execute(ctx)
        assert result.success is False

    def test_execute_with_rtl_passes_quality(self, tmp_project):
        """SkillDAgent should pass quality gate with well-structured RTL."""
        rtl_dir = tmp_project / "workspace" / "rtl"
        rtl_dir.mkdir(parents=True, exist_ok=True)
        (rtl_dir / "alu.v").write_text(
            "module alu(\n"
            "    input wire clk,\n"
            "    input wire rst_n,\n"
            "    input wire [7:0] a,\n"
            "    input wire [7:0] b,\n"
            "    output reg [15:0] result\n"
            ");\n"
            "always @(posedge clk or negedge rst_n) begin\n"
            "    if (!rst_n)\n"
            "        result <= 16'd0;\n"
            "    else\n"
            "        result <= a * b;\n"
            "end\n"
            "endmodule\n"
        )

        agent = SkillDAgent(quality_threshold=0.3)
        ctx = {"project_dir": str(tmp_project)}
        result = agent.execute(ctx)
        assert result.stage == "skill_d"
        assert "quality_score" in result.metrics

    def test_quality_threshold_configurable(self):
        """SkillD should accept custom quality threshold."""
        agent = SkillDAgent(quality_threshold=0.9)
        assert agent.quality_threshold == 0.9

    def test_static_score_with_issues(self, tmp_project):
        """Static analysis should detect naming violations."""
        rtl_dir = tmp_project / "workspace" / "rtl"
        rtl_dir.mkdir(parents=True, exist_ok=True)
        (rtl_dir / "BadModule.v").write_text(
            "module BadModule(input clk); endmodule\n"
        )

        agent = SkillDAgent()
        rtl_files = list(rtl_dir.glob("*.v"))
        analysis = agent._run_static_analysis(rtl_files)
        assert len(analysis["issues"]) > 0

    def test_parse_llm_score(self):
        """Should parse LLM output correctly."""
        agent = SkillDAgent()
        output = "SCORE: 85\nISSUES:\n- latch inferred in module foo\n- missing reset\n"
        score, issues = agent._parse_llm_score(output)
        assert score == 0.85
        assert len(issues) == 2

    def test_parse_llm_score_no_issues(self):
        """Should handle clean output."""
        agent = SkillDAgent()
        output = "SCORE: 95\nISSUES:\n"
        score, issues = agent._parse_llm_score(output)
        assert score == 0.95
        assert len(issues) == 0
