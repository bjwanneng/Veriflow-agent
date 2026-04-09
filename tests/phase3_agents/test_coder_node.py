"""Tests for CoderAgent node.

Verifies the CoderAgent's independent functionality including:
- Input validation (spec.json and micro_arch.md)
- RTL code generation
- Parallel module generation
- Peer summary building
- LLM error handling
"""

import json
from pathlib import Path

import pytest

from veriflow_agent.agents.coder import CoderAgent


class TestCoderAgent:
    """Tests for CoderAgent node functionality."""

    @pytest.fixture
    def agent(self):
        """Create a CoderAgent instance."""
        return CoderAgent()

    @pytest.fixture
    def valid_project(self, tmp_path):
        """Create a valid project structure with spec.json and micro_arch.md."""
        # Create spec.json
        spec_file = tmp_path / "workspace" / "docs" / "spec.json"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec = {
            "design_name": "alu",
            "target_kpis": {"frequency_mhz": 100},
            "modules": [
                {
                    "module_name": "alu",
                    "module_type": "top",
                    "ports": [
                        {"name": "clk", "direction": "input", "width": 1},
                        {"name": "rst_n", "direction": "input", "width": 1},
                        {"name": "a", "direction": "input", "width": 32},
                        {"name": "b", "direction": "input", "width": 32},
                        {"name": "op", "direction": "input", "width": 4},
                        {"name": "result", "direction": "output", "width": 32},
                    ]
                },
                {
                    "module_name": "adder",
                    "module_type": "leaf",
                    "ports": [
                        {"name": "a", "direction": "input", "width": 32},
                        {"name": "b", "direction": "input", "width": 32},
                        {"name": "sum", "direction": "output", "width": 32},
                    ]
                }
            ]
        }
        spec_file.write_text(json.dumps(spec, indent=2), encoding="utf-8")

        # Create micro_arch.md
        microarch_file = tmp_path / "workspace" / "docs" / "micro_arch.md"
        microarch_file.parent.mkdir(parents=True, exist_ok=True)
        microarch = """
# Micro-Architecture: ALU

## Overview
Simple ALU with adder sub-module.

## Module: alu (top)
- Pipelined: No
- FSM: No

## Module: adder (leaf)
- Simple combinational adder
- No clock/reset needed
"""
        microarch_file.write_text(microarch, encoding="utf-8")

        # Create RTL directory
        rtl_dir = tmp_path / "workspace" / "rtl"
        rtl_dir.mkdir(parents=True, exist_ok=True)

        return str(tmp_path)

    def test_coder_with_valid_spec(self, agent, valid_project, mocker):
        """Test CoderAgent with valid spec.json and micro_arch.md."""
        # Mock LLM call to return Verilog
        mock_verilog = """
module adder (
    input  [31:0] a,
    input  [31:0] b,
    output [31:0] sum
);
    assign sum = a + b;
endmodule
"""
        mocker.patch.object(agent, 'call_llm', return_value=mock_verilog)

        # Execute
        context = {"project_dir": valid_project, "mode": "standard"}
        result = agent.execute(context)

        # Verify
        assert result.success is True
        assert result.stage == "coder"
        assert len(result.artifacts) == 2  # adder.v and alu.v
        assert result.metrics["modules_generated"] == 2
        assert result.metrics["modules_total"] == 2

    def test_coder_missing_spec(self, agent, tmp_path):
        """Test CoderAgent with missing spec.json."""
        # Create project without spec.json
        project_dir = str(tmp_path)

        # Execute
        context = {"project_dir": project_dir, "mode": "standard"}
        result = agent.execute(context)

        # Verify failure
        assert result.success is False
        assert result.stage == "coder"
        assert "Missing required inputs" in result.errors[0]

    def test_coder_rtl_generation(self, agent, valid_project, mocker):
        """Test RTL code generation."""
        mock_verilog = """
module alu (
    input         clk,
    input         rst_n,
    input  [31:0] a,
    input  [31:0] b,
    input  [3:0]  op,
    output [31:0] result
);
    reg [31:0] result_reg;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            result_reg <= 32'b0;
        else begin
            case (op)
                4'b0000: result_reg <= a + b;
                4'b0001: result_reg <= a - b;
                4'b0010: result_reg <= a & b;
                4'b0011: result_reg <= a | b;
                default: result_reg <= 32'b0;
            endcase
        end
    end

    assign result = result_reg;
endmodule
"""
        mocker.patch.object(agent, 'call_llm', return_value=mock_verilog)

        context = {"project_dir": valid_project, "mode": "standard"}
        result = agent.execute(context)

        # Verify RTL file was created
        assert result.success is True
        rtl_path = Path(valid_project) / "workspace" / "rtl" / "alu.v"
        assert rtl_path.exists()

        content = rtl_path.read_text(encoding="utf-8")
        assert "module alu" in content
        assert "endmodule" in content

    def test_coder_parallel_generation(self, agent, valid_project, mocker):
        """Test parallel module generation."""
        # Mock LLM to return different modules
        verilog_responses = {
            "adder": """
module adder (
    input  [31:0] a,
    input  [31:0] b,
    output [31:0] sum
);
    assign sum = a + b;
endmodule
""",
            "alu": """
module alu (
    input         clk,
    input  [31:0] a,
    input  [31:0] b,
    output [31:0] result
);
    assign result = a + b;
endmodule
"""
        }

        call_count = [0]

        def mock_call_llm(context, prompt_override=None):
            call_count[0] += 1
            # Extract module name from prompt
            if "adder" in prompt_override or "adder" in str(context):
                return verilog_responses["adder"]
            return verilog_responses["alu"]

        mocker.patch.object(agent, 'call_llm', side_effect=mock_call_llm)

        context = {"project_dir": valid_project, "mode": "standard"}
        result = agent.execute(context)

        # Verify both modules were generated
        assert result.success is True
        assert result.metrics["modules_generated"] == 2
        assert result.metrics["leaf_count"] == 1
        assert result.metrics["top_count"] == 1

    def test_coder_peer_summary_building(self, agent):
        """Test peer interface summary building."""
        modules = [
            {
                "module_name": "alu",
                "ports": [
                    {"name": "clk", "direction": "input", "width": 1},
                    {"name": "a", "direction": "input", "width": 32},
                ]
            },
            {
                "module_name": "adder",
                "ports": [
                    {"name": "x", "direction": "input", "width": 16},
                    {"name": "y", "direction": "output", "width": 16},
                ]
            }
        ]

        summary = agent._build_peer_summary(modules)

        # Verify summary format
        assert "module alu" in summary
        assert "module adder" in summary
        assert "clk" in summary
        assert "a" in summary
        assert "x" in summary
        assert "y" in summary

    def test_coder_llm_error_handling(self, agent, valid_project, mocker):
        """Test LLM error handling."""
        # Mock LLM to raise exception
        mocker.patch.object(
            agent,
            'call_llm',
            side_effect=Exception("Claude CLI not found")
        )

        context = {"project_dir": valid_project, "mode": "standard"}
        result = agent.execute(context)

        # Verify error handling - may fail for one module but should return result
        assert result.stage == "coder"
        # Should have errors from the failed LLM calls
        assert len(result.errors) > 0 or not result.success
