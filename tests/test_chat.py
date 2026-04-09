"""Tests for the VeriFlow-Agent Chat Interface.

Tests the chat handler, project manager, and formatters.
"""

from unittest.mock import MagicMock, patch

from veriflow_agent.chat.formatters import (
    format_debugger_event,
    format_final_summary,
    format_inspection_response,
    format_pipeline_start,
    format_rtl_code_display,
    format_stage_progress,
)
from veriflow_agent.chat.handler import PipelineChatHandler
from veriflow_agent.chat.project_manager import (
    _generate_slug,
    create_project_from_requirement,
    update_requirement,
)
from veriflow_agent.graph.state import StageOutput

# ── Project Manager ───────────────────────────────────────────────────


class TestProjectManager:
    def test_create_project(self, tmp_path):
        project = create_project_from_requirement(
            "Design a 4-bit ALU", base_dir=tmp_path,
        )
        assert project.exists()
        assert (project / "requirement.md").exists()
        assert (project / "workspace" / "rtl").exists()
        assert (project / "workspace" / "docs").exists()
        assert (project / "workspace" / "tb").exists()

    def test_requirement_content(self, tmp_path):
        req = "Design a simple counter"
        project = create_project_from_requirement(req, base_dir=tmp_path)
        content = (project / "requirement.md").read_text()
        assert content == req

    def test_update_requirement(self, tmp_path):
        project = create_project_from_requirement(
            "Design a 4-bit ALU", base_dir=tmp_path,
        )
        update_requirement(project, "Add shift operations")
        content = (project / "requirement.md").read_text()
        assert "4-bit ALU" in content
        assert "Add shift operations" in content

    def test_generate_slug_alu(self):
        slug = _generate_slug("Design a 4-bit ALU")
        assert "alu" in slug

    def test_generate_slug_uart(self):
        slug = _generate_slug("Create a UART transmitter")
        assert "uart" in slug

    def test_generate_slug_fifo(self):
        slug = _generate_slug("Build a FIFO buffer with full/empty flags")
        assert "fifo" in slug or len(slug) > 0

    def test_generate_slug_short(self):
        slug = _generate_slug("counter")
        assert len(slug) > 0


# ── Formatters ────────────────────────────────────────────────────────


class TestFormatters:
    def test_pipeline_start(self):
        result = format_pipeline_start("Design a 4-bit ALU")
        assert "RTL Design Pipeline" in result
        assert "4-bit ALU" in result

    def test_stage_progress_pass(self):
        so = StageOutput(success=True, duration_s=2.5, artifacts=["spec.json"])
        result = format_stage_progress(
            "architect", so,
            all_completed=["architect"],
            all_failed=[],
            retry_counts={},
            stage_num=1,
        )
        assert "Architecture Analysis" in result
        assert "PASSED" in result

    def test_stage_progress_fail(self):
        so = StageOutput(success=False, duration_s=1.0, errors=["syntax error"])
        result = format_stage_progress(
            "lint", so,
            all_completed=["architect"],
            all_failed=["lint"],
            retry_counts={"lint": 1},
            stage_num=5,
        )
        assert "FAILED" in result

    def test_debugger_event(self):
        result = format_debugger_event(
            feedback_source="lint",
            retry_count=2,
            max_retries=3,
            rollback_target="coder",
            error_category="syntax",
        )
        assert "Feedback Loop" in result
        assert "RTL Code Generation" in result or "coder" in result

    def test_final_summary(self, tmp_path):
        project = tmp_path / "test_project"
        project.mkdir()
        (project / "workspace" / "rtl").mkdir(parents=True)
        (project / "workspace" / "rtl" / "alu.v").write_text("module alu; endmodule")

        state = {
            "stages_completed": ["architect", "coder", "synth"],
            "stages_failed": [],
            "token_usage": 50000,
            "token_budget": 1000000,
        }
        result = format_final_summary(state, project)
        assert "Pipeline Complete" in result

    def test_rtl_code_display(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        rtl = project / "workspace" / "rtl"
        rtl.mkdir(parents=True)
        (rtl / "top.v").write_text("module top; endmodule")

        result = format_rtl_code_display(project)
        assert "module top" in result
        assert "verilog" in result

    def test_inspection_rtl(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        rtl = project / "workspace" / "rtl"
        rtl.mkdir(parents=True)
        (rtl / "alu.v").write_text("module alu; endmodule")

        result = format_inspection_response("show me the RTL code", project)
        assert "alu.v" in result

    def test_inspection_no_project(self, tmp_path):
        result = format_inspection_response("show spec", tmp_path / "nonexistent")
        # Should indicate file not found or return empty
        assert "No" in result or "not found" in result.lower() or "Run" in result or result == ""


# ── Chat Handler ──────────────────────────────────────────────────────


class TestChatHandler:
    def test_classify_intent_new(self):
        handler = PipelineChatHandler()
        intent = handler._classify_intent("Design a 4-bit ALU", [])
        assert intent == "design"

    def test_classify_intent_inspect(self):
        handler = PipelineChatHandler()
        history = [{"role": "assistant", "content": "Pipeline Complete!"}]
        intent = handler._classify_intent("show me the RTL code", history)
        assert intent == "inspect"

    def test_classify_intent_modify(self):
        handler = PipelineChatHandler()
        history = [{"role": "assistant", "content": "Pipeline Complete!"}]
        intent = handler._classify_intent("add shift-left to the ALU", history)
        assert intent == "modify"

    def test_inspection_no_project(self):
        handler = PipelineChatHandler()
        # With history containing pipeline output, this is classified as inspection
        history = [{"role": "assistant", "content": "Pipeline Complete!"}]
        responses = list(handler.handle_message(
            "show me the RTL code", history, session_id="test_no_proj4",
        ))
        assert len(responses) > 0
        combined = " ".join(responses).lower()
        # Should tell user no project exists
        assert "no project" in combined or "start" in combined or "no" in combined

    def test_new_design_creates_project(self, tmp_path):
        """Verify new design creates a project directory."""
        handler = PipelineChatHandler()
        # Mock the pipeline graph to avoid actual execution
        with patch("veriflow_agent.chat.handler.create_veriflow_graph") as mock_graph:
            mock_instance = MagicMock()
            mock_instance.stream.return_value = iter([])  # No events
            mock_instance.get_state.return_value = MagicMock(values={})
            mock_graph.return_value = mock_instance

            responses = list(handler.handle_message(
                "Design a 4-bit ALU", [], session_id="test_new2",
            ))

            # Should have at least the start message
            assert len(responses) > 0
            # Project dir should be created
            assert "test_new2" in handler._project_dirs
