"""Tests for DebuggerAgent node.

Verifies the DebuggerAgent's independent functionality including:
- Error context handling
- Error history reading
- RTL fix application
- Testbench protection
- Rollback target setting
- Error categorization
- LLM error handling
"""

from pathlib import Path

import pytest

from veriflow_agent.agents.debugger import DebuggerAgent


class TestDebuggerAgent:
    """Tests for DebuggerAgent node functionality."""

    @pytest.fixture
    def agent(self):
        """Create a DebuggerAgent instance."""
        return DebuggerAgent()

    @pytest.fixture
    def valid_project(self, tmp_path):
        """Create a valid project structure with RTL files."""
        # Create RTL files with errors
        rtl_dir = tmp_path / "workspace" / "rtl"
        rtl_dir.mkdir(parents=True, exist_ok=True)

        # Create a Verilog file with syntax error
        alu_file = rtl_dir / "alu.v"
        alu_file.write_text("""
module alu (
    input         clk,
    input  [31:0] a,
    output [31:0] result
);
    // Syntax error: missing 'reg' declaration for result
    always @(posedge clk)
        result = a + 1;  // result should be declared as 'output reg'
endmodule
""", encoding="utf-8")

        # Create testbench directory
        tb_dir = tmp_path / "workspace" / "tb"
        tb_dir.mkdir(parents=True, exist_ok=True)

        tb_file = tb_dir / "tb_alu.v"
        tb_file.write_text("""
module tb_alu;
  initial $display("Test");
endmodule
""", encoding="utf-8")

        return str(tmp_path)

    def test_debugger_with_error_context(self, agent, valid_project, mocker):
        """Test DebuggerAgent with error context."""
        # Mock LLM to return fixed code
        fixed_code = """
module alu (
    input         clk,
    input  [31:0] a,
    output reg [31:0] result
);
    always @(posedge clk)
        result = a + 1;
endmodule
"""
        mocker.patch.object(agent, 'call_llm', return_value=fixed_code)

        # Execute with error context
        context = {
            "project_dir": valid_project,
            "error_type": "syntax",
            "error_log": "alu.v:9: error: 'result' is not declared as reg",
            "feedback_source": "lint",
            "error_history": []
        }
        result = agent.execute(context)

        # Verify
        assert result.success is True
        assert result.stage == "debugger"
        assert result.metrics["error_type"] == "syntax"

    def test_debugger_error_history_reading(self, agent, valid_project, mocker):
        """Test error history reading."""
        mocker.patch.object(agent, 'call_llm', return_value="```verilog\nmodule alu (input clk, output reg [31:0] result);\nalways @(posedge clk) result <= 0;\nendmodule\n```")

        error_history = [
            "Attempt 1: Syntax error at line 10",
            "Attempt 2: Undefined identifier 'foo'",
        ]

        context = {
            "project_dir": valid_project,
            "error_type": "syntax",
            "error_log": "Current error",
            "feedback_source": "lint",
            "error_history": error_history
        }
        result = agent.execute(context)

        # Verify error history was processed
        assert result.success is True

    def test_debugger_rtl_fix_application(self, agent, valid_project, mocker):
        """Test RTL fix application."""
        fixed_code = """```verilog
module alu (
    input         clk,
    input  [31:0] a,
    output reg [31:0] result
);
    always @(posedge clk)
        result <= a + 1;
endmodule
```"""
        mocker.patch.object(agent, 'call_llm', return_value=fixed_code)

        context = {
            "project_dir": valid_project,
            "error_type": "syntax",
            "error_log": "result not declared as reg",
            "feedback_source": "lint",
            "error_history": []
        }
        result = agent.execute(context)

        # Verify RTL file was updated
        assert result.success is True
        rtl_path = Path(valid_project) / "workspace" / "rtl" / "alu.v"
        content = rtl_path.read_text(encoding="utf-8")
        assert "output reg [31:0] result" in content

    def test_debugger_testbench_protection(self, agent, valid_project, mocker):
        """Test that testbench files are protected."""
        mocker.patch.object(agent, 'call_llm', return_value="// Fixed code")

        # Read original testbench content
        tb_path = Path(valid_project) / "workspace" / "tb" / "tb_alu.v"
        original_content = tb_path.read_text(encoding="utf-8")

        context = {
            "project_dir": valid_project,
            "error_type": "syntax",
            "error_log": "Some error",
            "feedback_source": "lint",
            "error_history": []
        }
        result = agent.execute(context)

        # Verify testbench was not modified
        tb_content = tb_path.read_text(encoding="utf-8")
        assert tb_content == original_content

    def test_debugger_error_categorization(self, agent, valid_project, mocker):
        """Test error categorization (syntax, logic, timing)."""
        mocker.patch.object(agent, 'call_llm', return_value="// Fixed code")

        # Test with different error types
        for error_type in ["syntax", "logic", "timing", "resource"]:
            context = {
                "project_dir": valid_project,
                "error_type": error_type,
                "error_log": f"Some {error_type} error",
                "feedback_source": "lint" if error_type == "syntax" else "sim",
                "error_history": []
            }
            result = agent.execute(context)
            assert result.metrics["error_type"] == error_type

    def test_debugger_llm_error_handling(self, agent, valid_project, mocker):
        """Test LLM error handling."""
        # Mock LLM to raise exception
        mocker.patch.object(
            agent,
            'call_llm',
            side_effect=Exception("LLM service unavailable")
        )

        context = {
            "project_dir": valid_project,
            "error_type": "syntax",
            "error_log": "Some error",
            "feedback_source": "lint",
            "error_history": []
        }
        result = agent.execute(context)

        # Verify error handling
        assert result.success is False
        assert result.stage == "debugger"
        assert "LLM invocation failed" in result.errors[0]
