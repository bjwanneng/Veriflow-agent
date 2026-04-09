"""Tests for TimingAgent node.

Verifies the TimingAgent's independent functionality including:
- Input validation (spec.json existence)
- timing_model.yaml output generation
- Testbench generation
- LLM error handling
"""

import json
from pathlib import Path

import pytest

from veriflow_agent.agents.timing import TimingAgent


class TestTimingAgent:
    """Tests for TimingAgent node functionality."""

    @pytest.fixture
    def agent(self):
        """Create a TimingAgent instance."""
        return TimingAgent()

    @pytest.fixture
    def valid_project(self, tmp_path):
        """Create a valid project structure with spec.json."""
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
                }
            ]
        }
        spec_file.write_text(json.dumps(spec, indent=2), encoding="utf-8")

        # Create tb directory
        tb_dir = tmp_path / "workspace" / "tb"
        tb_dir.mkdir(parents=True, exist_ok=True)

        return str(tmp_path)

    def test_timing_with_valid_spec(self, agent, valid_project, mocker):
        """Test TimingAgent with valid spec.json."""
        # Mock LLM call
        mock_output = """
# Timing Model for ALU

clock:
  name: clk
  period_ns: 10.0  # 100MHz

scenarios:
  - name: add_operation
    description: Basic addition
    inputs:
      a: 32'h00000005
      b: 32'h00000003
      op: 4'b0000
    expected_result: 32'h00000008

module: alu
ports_validated: true
"""
        mock_testbench = """
`timescale 1ns/1ps
module tb_alu;
  reg clk;
  reg rst_n;
  reg [31:0] a, b;
  reg [3:0] op;
  wire [31:0] result;

  // Instantiate DUT
  alu dut(.*);

  // Clock generation
  initial clk = 0;
  always #5 clk = ~clk;

  initial begin
    rst_n = 0;
    #10 rst_n = 1;
    #10;
    a = 5; b = 3; op = 4'b0000; // ADD
    #10;
    $display("Result: %d", result);
    $finish;
  end
endmodule
"""
        full_output = "```yaml\n" + mock_output.strip() + "\n```\n```verilog\n" + mock_testbench.strip() + "\n```"
        mocker.patch.object(agent, 'call_llm', return_value=full_output)

        # Execute
        context = {"project_dir": valid_project, "mode": "standard"}
        result = agent.execute(context)

        # Verify
        assert result.success is True
        assert result.stage == "timing"
        assert len(result.artifacts) == 2  # timing_model.yaml and testbench
        assert result.metrics["timing_model_size"] > 0

    def test_timing_timing_model_yaml_output(self, agent, valid_project, mocker):
        """Test timing_model.yaml output generation."""
        mock_yaml = """
clock:
  name: clk
  period_ns: 10.0

scenarios:
  - name: add
    inputs: {a: 5, b: 3}
    expected: 8
"""
        mock_tb = """
module tb_alu;
  initial begin
    $display("Test");
  end
endmodule
"""
        mocker.patch.object(agent, 'call_llm', return_value=f"```yaml\n{mock_yaml}\n```\n```verilog\n{mock_tb}\n```")

        context = {"project_dir": valid_project, "mode": "standard"}
        result = agent.execute(context)

        # Verify timing_model.yaml was created
        timing_path = Path(valid_project) / "workspace" / "docs" / "timing_model.yaml"
        assert timing_path.exists()
        content = timing_path.read_text(encoding="utf-8")
        assert "clock:" in content
        assert "period_ns: 10.0" in content

    def test_timing_testbench_generation(self, agent, valid_project, mocker):
        """Test testbench generation."""
        mock_output = """
```yaml
timing:
  clock: 10ns
```

```verilog
`timescale 1ns/1ps
module tb_alu;
  reg clk, rst_n;
  reg [31:0] a, b;
  reg [3:0] op;
  wire [31:0] result;

  alu dut (.*);

  initial begin
    $dumpfile("alu.vcd");
    $dumpvars(0, tb_alu);
    #100 $finish;
  end
endmodule
```
"""
        mocker.patch.object(agent, 'call_llm', return_value=mock_output)

        context = {"project_dir": valid_project, "mode": "standard"}
        result = agent.execute(context)

        # Verify testbench file
        assert result.success is True
        tb_path = Path(valid_project) / "workspace" / "tb" / "tb_alu.v"
        assert tb_path.exists()
        content = tb_path.read_text(encoding="utf-8")
        assert "module tb_alu" in content
        assert "alu dut" in content

    def test_timing_llm_error_handling(self, agent, valid_project, mocker):
        """Test LLM error handling."""
        # Mock LLM to raise exception
        mocker.patch.object(
            agent,
            'call_llm',
            side_effect=Exception("LLM service unavailable")
        )

        context = {"project_dir": valid_project, "mode": "standard"}
        result = agent.execute(context)

        # Verify error handling
        assert result.success is False
        assert result.stage == "timing"
        assert "LLM invocation failed" in result.errors[0]
