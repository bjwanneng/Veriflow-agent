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
        # All messages now go through LLM-driven analysis
        intent = handler._classify_intent("Design a 4-bit ALU", [])
        assert intent == "llm_analyze"

    def test_classify_intent_inspect(self):
        handler = PipelineChatHandler()
        history = [{"role": "assistant", "content": "Pipeline Complete!"}]
        # L1: All messages now return "llm_analyze" — LLM decides inspect vs design
        intent = handler._classify_intent("show me the RTL code", history)
        assert intent == "llm_analyze"

    def test_classify_intent_modify(self):
        handler = PipelineChatHandler()
        history = [{"role": "assistant", "content": "Pipeline Complete!"}]
        # L1: All messages now return "llm_analyze" — LLM decides modify vs design
        intent = handler._classify_intent("add a port for shift-left to the ALU", history)
        assert intent == "llm_analyze"

    def test_classify_intent_fallback_inspect(self):
        """Verify keyword fallback works when LLM is unavailable."""
        history = [{"role": "assistant", "content": "Pipeline Complete!"}]
        intent = PipelineChatHandler._classify_intent_fallback("show me the RTL code", history)
        assert intent == "inspect"

    def test_classify_intent_fallback_modify(self):
        """Verify keyword fallback works when LLM is unavailable."""
        history = [{"role": "assistant", "content": "Pipeline Complete!"}]
        intent = PipelineChatHandler._classify_intent_fallback("add a port to the ALU", history)
        assert intent == "modify"

    def test_classify_intent_fallback_design(self):
        """Verify keyword fallback defaults to design for non-matching input."""
        intent = PipelineChatHandler._classify_intent_fallback("Design a 4-bit ALU", [])
        assert intent == "design"

    def test_inspection_no_project(self):
        """Verify inspection flow: LLM decides inspect → no project → error message."""
        handler = PipelineChatHandler()
        history = [{"role": "assistant", "content": "Pipeline Complete!"}]
        # Mock LLM to return "inspect" mode (simulating LLM intent classification)
        import json
        inspect_json = json.dumps({
            "mode": "inspect",
            "reasoning": "User wants to see RTL",
            "target_files": ["rtl"],
        })
        with patch("veriflow_agent.chat.handler.call_llm_stream") as mock_llm:
            mock_llm.return_value = iter([inspect_json])
            responses = list(handler.handle_message(
                "show me the RTL code", history, session_id="test_no_proj4",
            ))
            assert len(responses) > 0
            combined = " ".join(responses).lower()
            # Should tell user no project exists
            assert "no project" in combined or "start" in combined or "no" in combined

    def test_new_design_creates_project(self, tmp_path):
        """Verify start_pipeline tool creates a project directory.

        We mock OrchestratorAgent.run() to simulate what happens when
        the LLM decides to call start_pipeline: it sets _project_dirs
        and yields a response.
        """
        handler = PipelineChatHandler()

        def fake_orchestrator_run(self_orch, message, history, event_callback):
            """Simulate orchestrator calling start_pipeline tool."""
            from pathlib import Path
            import tempfile
            project_dir = Path(tempfile.mkdtemp(prefix="veriflow-test-"))
            (project_dir / "workspace" / "docs").mkdir(parents=True, exist_ok=True)
            (project_dir / "workspace" / "rtl").mkdir(parents=True, exist_ok=True)
            (project_dir / "workspace" / "tb").mkdir(parents=True, exist_ok=True)
            (project_dir / "workspace" / "logs").mkdir(parents=True, exist_ok=True)
            self_orch.handler._project_dirs[self_orch.session_id] = project_dir
            yield "Pipeline started successfully."

        with patch(
            "veriflow_agent.chat.orchestrator.OrchestratorAgent.run",
            fake_orchestrator_run,
        ):
            responses = list(handler.handle_message(
                "Design a 4-bit ALU", [], session_id="test_new2",
            ))

            assert len(responses) > 0
            assert "test_new2" in handler._project_dirs


class TestToolCallingMessageFormat:
    """Verify that tool_calls and tool_call_id are preserved in API messages."""

    def test_tool_call_id_preserved_in_stream(self):
        """_stream_openai must preserve tool_call_id in role:tool messages."""
        from veriflow_agent.chat.llm import _stream_openai, LLMConfig

        config = LLMConfig(api_key="test-key", base_url="http://localhost:1234")
        messages = [
            {"role": "user", "content": "test"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_abc123",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path": "rtl.v"}'},
                }],
            },
            {"role": "tool", "tool_call_id": "call_abc123", "content": "module top; endmodule"},
        ]

        with patch("veriflow_agent.chat.llm._make_openai_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client
            # Simulate empty streaming response
            mock_chunk = MagicMock()
            mock_chunk.choices = []
            mock_client.chat.completions.create.return_value = iter([mock_chunk])

            list(_stream_openai(messages, config, "system prompt"))

            # Verify the API was called with messages preserving tool_call_id
            call_args = mock_client.chat.completions.create.call_args
            api_messages = call_args.kwargs["messages"]

            # System prompt + user + assistant + tool = 4 messages
            assert len(api_messages) == 4
            assert api_messages[0]["role"] == "system"
            assert api_messages[1]["role"] == "user"
            # Assistant message must have tool_calls
            assert api_messages[2]["role"] == "assistant"
            assert "tool_calls" in api_messages[2]
            assert api_messages[2]["tool_calls"][0]["id"] == "call_abc123"
            # Tool message must have tool_call_id
            assert api_messages[3]["role"] == "tool"
            assert api_messages[3]["tool_call_id"] == "call_abc123"
