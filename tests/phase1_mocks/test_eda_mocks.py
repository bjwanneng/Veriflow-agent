"""Mock tests for EDA tools (Iverilog, VVP, Yosys).

These tests verify that all EDA tool invocation paths can be mocked
for fast, deterministic testing without actual tool installations.
"""

import subprocess
from unittest.mock import MagicMock

from veriflow_agent.tools.base import ToolResult, ToolStatus
from veriflow_agent.tools.lint import IverilogTool
from veriflow_agent.tools.simulate import VvpTool
from veriflow_agent.tools.synth import YosysTool


class TestIverilogMock:
    """Tests for mocking Iverilog tool."""

    def test_iverilog_lint_success_mock(self, mocker, tmp_path):
        """Test mocking iverilog lint with success."""
        tool = IverilogTool()

        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr="",
        )

        result = tool.run(
            mode="lint",
            files=["rtl/top.v"],
            cwd=tmp_path,
        )

        assert result.status == ToolStatus.SUCCESS
        mock_run.assert_called_once()

    def test_iverilog_lint_syntax_error_mock(self, mocker, tmp_path):
        """Test mocking iverilog with syntax error."""
        tool = IverilogTool()

        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="rtl/top.v:10: syntax error\nrtl/top.v:15: error: undeclared identifier 'foo'",
        )

        result = tool.run(
            mode="lint",
            files=["rtl/top.v"],
            cwd=tmp_path,
        )

        assert result.status == ToolStatus.FAILURE

        # Parse the error output
        lint = tool.parse_lint_output(result)
        assert lint.passed is False
        assert lint.error_count >= 1
        assert "undeclared" in lint.errors[0] or "error" in lint.errors[0].lower()

    def test_iverilog_lint_warning_mock(self, mocker, tmp_path):
        """Test mocking iverilog with warnings."""
        tool = IverilogTool()

        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr="rtl/top.v:5: warning: implicit wire declaration\nrtl/top.v:20: warning: unused wire 'temp'",
        )

        result = tool.run(
            mode="lint",
            files=["rtl/top.v"],
            cwd=tmp_path,
        )

        # Warnings don't cause failure
        assert result.status == ToolStatus.SUCCESS

        lint = tool.parse_lint_output(result)
        assert lint.passed is True
        assert lint.warning_count == 2

    def test_iverilog_compile_mock(self, mocker, tmp_path):
        """Test mocking iverilog compile mode."""
        tool = IverilogTool()

        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr="",
        )

        result = tool.run(
            mode="compile",
            files=["rtl/top.v", "rtl/alu.v"],
            output_file="output.vvp",
            cwd=tmp_path,
        )

        assert result.status == ToolStatus.SUCCESS
        mock_run.assert_called_once()
        # Verify compile-specific arguments
        call_args = mock_run.call_args
        assert "-o" in str(call_args)
        assert "output.vvp" in str(call_args)

    def test_iverilog_not_found_mock(self, mocker):
        """Test handling when iverilog is not found."""
        mocker.patch(
            "veriflow_agent.tools.lint.find_eda_tool",
            return_value=None,
        )

        tool = IverilogTool()
        assert tool.validate_prerequisites() is False

    def test_iverilog_timeout_mock(self, mocker, tmp_path):
        """Test mocking iverilog timeout."""
        tool = IverilogTool()

        mock_run = mocker.patch("subprocess.run")
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd=["iverilog"],
            timeout=60,
        )

        result = tool.run(
            mode="lint",
            files=["rtl/top.v"],
            cwd=tmp_path,
        )

        assert result.status == ToolStatus.TIMEOUT


class TestVvpMock:
    """Tests for mocking VVP simulation tool."""

    def test_vvp_simulation_success_mock(self, mocker, tmp_path):
        """Test mocking vvp simulation with success."""
        tool = VvpTool()

        # Mock both iverilog (compile) and vvp (sim)
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Test 1: PASS\nTest 2: PASS\nALL TESTS PASSED",
            stderr="",
        )

        result = tool.run(
            testbench="rtl/tb_top.v",
            rtl_files=["rtl/top.v"],
            cwd=tmp_path,
        )

        assert result.status == ToolStatus.SUCCESS

        sim = tool.parse_sim_output(result)
        assert sim.passed is True
        assert sim.all_passed is True

    def test_vvp_simulation_failure_mock(self, mocker, tmp_path):
        """Test mocking vvp simulation with failures."""
        tool = VvpTool()

        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="Test 1: PASS\nTest 2: FAIL - mismatch at 100ns\nTest 3: FAIL",
            stderr="",
        )

        result = tool.run(
            testbench="rtl/tb_top.v",
            rtl_files=["rtl/top.v"],
            cwd=tmp_path,
        )

        sim = tool.parse_sim_output(result)
        assert sim.passed is False
        assert sim.fail_count == 2

    def test_vvp_compile_failure_mock(self, mocker, tmp_path):
        """Test mocking vvp when compilation fails."""
        tool = VvpTool()

        mock_run = mocker.patch("subprocess.run")
        # First call (compile) fails
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="rtl/top.v:5: error: syntax error",
        )

        result = tool.run(
            testbench="rtl/tb_top.v",
            rtl_files=["rtl/top.v"],
            cwd=tmp_path,
        )

        assert result.status == ToolStatus.FAILURE
        assert "syntax error" in result.stderr


class TestYosysMock:
    """Tests for mocking Yosys synthesis tool."""

    def test_yosys_synthesis_success_mock(self, mocker, tmp_path):
        """Test mocking yosys synthesis with success."""
        tool = YosysTool()

        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='Number of cells: 42\nNumber of wires: 100\n{"modules": {"top": {"num_cells": 42, "num_wires": 100}}}',
            stderr="",
        )

        result = tool.run(
            rtl_files=["rtl/top.v", "rtl/alu.v"],
            top_module="top",
            cwd=tmp_path,
        )

        assert result.status == ToolStatus.SUCCESS

        synth = tool.parse_synth_output(result, top_module="top")
        assert synth.success is True
        assert synth.num_cells == 42
        assert synth.num_wires == 100

    def test_yosys_synthesis_failure_mock(self, mocker, tmp_path):
        """Test mocking yosys synthesis with failure."""
        tool = YosysTool()

        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="ERROR: Module `top' not found\nERROR: No top module found",
        )

        result = tool.run(
            rtl_files=["rtl/bad_file.v"],
            top_module="top",
            cwd=tmp_path,
        )

        assert result.status == ToolStatus.FAILURE

        synth = tool.parse_synth_output(result, top_module="top")
        assert synth.success is False

    def test_yosys_not_found_mock(self, mocker):
        """Test handling when yosys is not found."""
        mocker.patch(
            "veriflow_agent.tools.synth.find_eda_tool",
            return_value=None,
        )

        tool = YosysTool()
        assert tool.validate_prerequisites() is False


class TestEDAErrorParsing:
    """Tests for EDA error output parsing with mocks."""

    def test_iverilog_error_parsing_mock(self, mocker, tmp_path):
        """Test parsing iverilog error output."""
        tool = IverilogTool()

        # Create a mock ToolResult with error output
        error_result = ToolResult(
            status=ToolStatus.FAILURE,
            return_code=1,
            stdout="",
            stderr="""rtl/top.v:5: error: syntax error
rtl/top.v:10: error: undeclared identifier 'clk'
rtl/top.v:15: warning: implicit wire declaration
""",
        )

        lint = tool.parse_lint_output(error_result)

        assert lint.passed is False
        assert lint.error_count == 2
        assert lint.warning_count == 1
        assert "syntax error" in lint.errors[0]

    def test_vvp_error_parsing_mock(self, mocker, tmp_path):
        """Test parsing VVP error output."""
        tool = VvpTool()

        error_result = ToolResult(
            status=ToolStatus.FAILURE,
            return_code=1,
            stdout="""Test 1: PASS
Test 2: FAIL - expected 0x42, got 0x00
Test 3: FAIL - timeout at 1000ns""",
            stderr="",
        )

        sim = tool.parse_sim_output(error_result)

        assert sim.passed is False
        assert sim.fail_count == 2
        assert sim.pass_count == 1

    def test_yosys_error_parsing_mock(self, mocker, tmp_path):
        """Test parsing Yosys error output."""
        tool = YosysTool()

        error_result = ToolResult(
            status=ToolStatus.FAILURE,
            return_code=1,
            stdout="",
            stderr="""ERROR: Module `missing_module' not found
ERROR: No top module found in design
""",
        )

        synth = tool.parse_synth_output(error_result, top_module="top")

        assert synth.success is False
        assert synth.num_cells == 0
