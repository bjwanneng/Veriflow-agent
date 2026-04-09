"""Tests for the VeriFlow-Agent tool layer.

These tests verify:
- Import structure and exports
- Tool instantiation and prerequisite detection
- Output parsing logic (lint, sim, synth)
- EDA utility functions
"""

from pathlib import Path

from veriflow_agent.tools.base import ToolResult, ToolStatus
from veriflow_agent.tools.constraint_gen import (
    generate_constraints,
    read_constraint_file,
)
from veriflow_agent.tools.eda_utils import (
    _compare_versions,
    check_version_compatibility,
    find_eda_tool,
    get_all_tool_versions,
    get_eda_env,
    get_tool_version,
)
from veriflow_agent.tools.lint import IverilogTool, LintResult
from veriflow_agent.tools.simulate import SimResult, VvpTool
from veriflow_agent.tools.synth import SynthResult, YosysTool

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


# ── Version detection ─────────────────────────────────────────────────


class TestVersionDetection:
    def test_get_tool_version_returns_str_or_none(self):
        version = get_tool_version("iverilog")
        assert version is None or isinstance(version, str)

    def test_get_all_tool_versions(self):
        versions = get_all_tool_versions()
        assert isinstance(versions, dict)
        assert "iverilog" in versions
        assert "yosys" in versions
        assert "vvp" in versions

    def test_compare_versions_equal(self):
        assert _compare_versions("10.0", "10.0") == 0

    def test_compare_versions_greater(self):
        assert _compare_versions("11.0", "10.0") == 1
        assert _compare_versions("10.1", "10.0") == 1

    def test_compare_versions_less(self):
        assert _compare_versions("9.0", "10.0") == -1

    def test_compare_versions_three_part(self):
        assert _compare_versions("10.3.1", "10.3.0") == 1
        assert _compare_versions("10.3.1", "10.3") == 1

    def test_check_version_compatibility_unknown_tool(self):
        ok, msg = check_version_compatibility("nonexistent_tool")
        assert ok is True  # No minimum set → always OK

    def test_get_tool_version_nonexistent(self):
        version = get_tool_version("definitely_not_a_tool_xyz")
        assert version is None


# ── Constraint generation ─────────────────────────────────────────────


class TestConstraintGeneration:
    def test_generate_from_clocks_list(self, tmp_path):
        """Generate constraints from timing model with clock list."""
        import yaml
        timing = {
            "clocks": [
                {"name": "clk", "period_ns": 10.0},
                {"name": "clk2", "frequency_mhz": 200},
            ],
            "io_delays": [
                {"direction": "input", "port": "data_in", "delay_ns": 2.0, "clock": "clk"},
                {"direction": "output", "port": "data_out", "delay_ns": 3.0, "clock": "clk"},
            ],
            "false_paths": [
                {"from": "rst_async", "to": "clk"},
            ],
        }
        timing_path = tmp_path / "timing_model.yaml"
        timing_path.write_text(yaml.dump(timing), encoding="utf-8")
        output_path = tmp_path / "constraints.sdc"

        result = generate_constraints(timing_path, output_path)
        assert result.success is True
        assert result.clock_constraints == 2
        assert result.io_constraints == 2
        assert result.timing_constraints == 1

        # Verify file content
        content = output_path.read_text(encoding="utf-8")
        assert "create_clock" in content
        assert "set_input_delay" in content
        assert "set_output_delay" in content
        assert "set_false_path" in content

    def test_generate_from_target_kpis(self, tmp_path):
        """Generate constraints from target KPIs when no clocks defined."""
        import yaml
        timing = {}
        timing_path = tmp_path / "timing_model.yaml"
        timing_path.write_text(yaml.dump(timing), encoding="utf-8")
        output_path = tmp_path / "constraints.sdc"

        result = generate_constraints(
            timing_path, output_path,
            target_kpis={"frequency_mhz": 100},
        )
        assert result.success is True
        assert result.clock_constraints == 1

        content = output_path.read_text(encoding="utf-8")
        assert "period 10.000" in content

    def test_generate_missing_timing_model(self, tmp_path):
        """Should fail gracefully when timing model doesn't exist."""
        result = generate_constraints(
            tmp_path / "nonexistent.yaml",
            tmp_path / "out.sdc",
        )
        assert result.success is False

    def test_read_constraint_file(self, tmp_path):
        """Should read and filter constraint lines."""
        sdc = tmp_path / "test.sdc"
        sdc.write_text(
            "# Comment line\n"
            "create_clock -name clk -period 10 [get_ports clk]\n"
            "\n"
            "set_input_delay 2.0 -clock clk [get_ports data]\n"
            "# Another comment\n",
            encoding="utf-8",
        )
        lines = read_constraint_file(sdc)
        assert len(lines) == 2
        assert "create_clock" in lines[0]
        assert "set_input_delay" in lines[1]

    def test_read_nonexistent_constraint_file(self):
        lines = read_constraint_file("/nonexistent/path.sdc")
        assert lines == []

    def test_generate_with_multicycle(self, tmp_path):
        """Generate multicycle path constraints."""
        import yaml
        timing = {
            "clocks": [{"name": "clk", "period_ns": 5.0}],
            "multicycle_paths": [
                {"cycles": 3, "from": "data_in", "to": "data_out"},
            ],
        }
        timing_path = tmp_path / "timing_model.yaml"
        timing_path.write_text(yaml.dump(timing), encoding="utf-8")
        output_path = tmp_path / "constraints.sdc"

        result = generate_constraints(timing_path, output_path)
        assert result.success is True
        assert result.timing_constraints == 1

        content = output_path.read_text(encoding="utf-8")
        assert "set_multicycle_path 3" in content
