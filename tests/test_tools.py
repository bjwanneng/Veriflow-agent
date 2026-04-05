"""Tests for the VeriFlow-Agent tool layer.

These tests verify:
- Import structure and exports
- Tool instantiation and prerequisite detection
- Output parsing logic (lint, sim, synth)
- EDA utility functions
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from veriflow_agent.tools.base import BaseTool, ToolResult, ToolStatus
from veriflow_agent.tools.eda_utils import find_eda_tool, get_eda_env
from veriflow_agent.tools.lint import IverilogTool, LintResult
from veriflow_agent.tools.simulate import VvpTool, SimResult
from veriflow_agent.tools.synth import YosysTool, SynthResult


# ── Base classes ──────────────────────────────────────────────────────


class TestToolResult:
    def test_success_property(self):
        r = ToolResult(status=ToolStatus.SUCCESS, return_code=0)
        assert r.success is True

    def test_success_with_errors(self):
        r = ToolResult(status=ToolStatus.SUCCESS, errors=["err"])
        assert r.success is False

    def test_to_dict(self):
        r = ToolResult(status=ToolStatus.SUCCESS)
        d = r.to_dict()
        assert d["status"] == "success"
        assert "return_code" in d


class TestLintResult:
    def test_passed_no_issues(self):
        r = LintResult(passed=True)
        assert r.passed is True
        assert r.error_count == 0

    def test_to_dict(self):
        r = LintResult(passed=False, error_count=2, errors=["e1", "e2"])
        d = r.to_dict()
        assert d["passed"] is False
        assert d["error_count"] == 2


class TestSimResult:
    def test_passed(self):
        r = SimResult(passed=True, all_passed=True)
        assert r.passed is True
        assert r.all_passed is True

    def test_to_dict(self):
        r = SimResult(passed=False, fail_count=1)
        d = r.to_dict()
        assert d["fail_count"] == 1


class TestSynthResult:
    def test_success(self):
        r = SynthResult(success=True, num_cells=42, num_wires=100)
        assert r.success is True
        assert r.num_cells == 42

    def test_to_dict(self):
        r = SynthResult(success=True, num_cells=10, top_module="top")
        d = r.to_dict()
        assert d["top_module"] == "top"


# ── IverilogTool ──────────────────────────────────────────────────────


class TestIverilogTool:
    def test_parse_clean_output(self):
        tool = IverilogTool.__new__(IverilogTool)
        result = ToolResult(status=ToolStatus.SUCCESS, return_code=0, stdout="", stderr="")
        lint = tool.parse_lint_output(result)
        assert lint.passed is True
        assert lint.error_count == 0

    def test_parse_with_errors(self):
        tool = IverilogTool.__new__(IverilogTool)
        stderr = "rtl/top.v:10: error: syntax error\nrtl/top.v:20: warning: unused wire"
        result = ToolResult(
            status=ToolStatus.FAILURE,
            return_code=1,
            stdout="",
            stderr=stderr,
        )
        lint = tool.parse_lint_output(result)
        assert lint.passed is False
        assert lint.error_count == 1
        assert lint.warning_count == 1
        assert "syntax error" in lint.errors[0]

    def test_filter_testbench(self):
        files = [
            Path("rtl/alu.v"),
            Path("rtl/tb_alu.v"),
            Path("rtl/top.v"),
            Path("rtl/tb_top.v"),
        ]
        filtered = IverilogTool.filter_testbench_files(files)
        assert len(filtered) == 2
        assert all(not f.name.startswith("tb_") for f in filtered)


# ── VvpTool ───────────────────────────────────────────────────────────


class TestVvpTool:
    def test_parse_pass(self):
        tool = VvpTool.__new__(VvpTool)
        stdout = "Test 1: PASS\nTest 2: PASS\nALL TESTS PASSED\n"
        result = ToolResult(
            status=ToolStatus.SUCCESS,
            return_code=0,
            stdout=stdout,
            stderr="",
        )
        sim = tool.parse_sim_output(result)
        assert sim.passed is True
        assert sim.all_passed is True
        assert sim.pass_count >= 2

    def test_parse_fail(self):
        tool = VvpTool.__new__(VvpTool)
        stdout = "Test 1: PASS\nTest 2: FAIL - mismatch at 5ns\n"
        result = ToolResult(
            status=ToolStatus.FAILURE,
            return_code=1,
            stdout=stdout,
            stderr="",
        )
        sim = tool.parse_sim_output(result)
        assert sim.passed is False
        assert sim.fail_count >= 1


# ── YosysTool ─────────────────────────────────────────────────────────


class TestYosysTool:
    def test_parse_regex_output(self):
        tool = YosysTool.__new__(YosysTool)
        stdout = (
            "Synthesizing top module...\n"
            "Number of cells: 42\n"
            "Number of wires: 100\n"
        )
        result = ToolResult(
            status=ToolStatus.SUCCESS,
            return_code=0,
            stdout=stdout,
            stderr="",
        )
        synth = tool.parse_synth_output(result, top_module="top")
        assert synth.success is True
        assert synth.num_cells == 42
        assert synth.num_wires == 100

    def test_parse_json_output(self):
        tool = YosysTool.__new__(YosysTool)
        import json
        stats = {"modules": {"top": {"num_cells": 55, "num_wires": 200}}}
        stdout = f"some output\n{json.dumps(stats)}\nmore output"
        result = ToolResult(
            status=ToolStatus.SUCCESS,
            return_code=0,
            stdout=stdout,
            stderr="",
        )
        synth = tool.parse_synth_output(result, top_module="top")
        assert synth.success is True
        assert synth.num_cells == 55
        assert synth.num_wires == 200
        assert synth.stats_json is not None

    def test_parse_empty_output(self):
        tool = YosysTool.__new__(YosysTool)
        result = ToolResult(
            status=ToolStatus.FAILURE,
            return_code=1,
            stdout="",
            stderr="error: blah",
        )
        synth = tool.parse_synth_output(result, top_module="top")
        assert synth.success is False
        assert synth.num_cells == 0


# ── EDA Utils ─────────────────────────────────────────────────────────


class TestEdaUtils:
    def test_find_tool_returns_path_or_none(self):
        # On this system, iverilog should be found via oss-cad-suite
        path = find_eda_tool("iverilog")
        assert path is None or isinstance(path, str)

    def test_get_eda_env_returns_dict(self):
        env = get_eda_env()
        assert isinstance(env, dict)
        assert "PATH" in env
