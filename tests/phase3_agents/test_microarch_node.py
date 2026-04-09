"""Tests for MicroArchAgent node.

Verifies the MicroArchAgent's independent functionality including:
- Input validation (spec.json existence)
- micro_arch.md output generation
- LLM error handling
"""

import json
from pathlib import Path

import pytest

from veriflow_agent.agents.microarch import MicroArchAgent


class TestMicroArchAgent:
    """Tests for MicroArchAgent node functionality."""

    @pytest.fixture
    def agent(self):
        """Create a MicroArchAgent instance."""
        return MicroArchAgent()

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

        # Create requirement.md
        req_file = tmp_path / "requirement.md"
        req_file.write_text("# ALU Design\n\nSimple ALU design.", encoding="utf-8")

        return str(tmp_path)

    def test_microarch_with_valid_spec(self, agent, valid_project, mocker):
        """Test MicroArchAgent with valid spec.json."""
        # Mock LLM call
        mock_micro_arch = """
# Micro-Architecture Specification: ALU

## Module Overview
The ALU is a 32-bit arithmetic logic unit supporting basic operations.

## Internal Signals
- `op_reg`: Registered operation code
- `result_next`: Combinational next value for result

## Pipelines
Single-cycle combinational path from inputs to output.

## FSMs
No FSMs - fully combinational with registered output.

## Control Logic
Operation selection based on `op` input:
- 4'b0000: ADD
- 4'b0001: SUB
- 4'b0010: AND
- 4'b0011: OR

## Memory
No internal memory elements.

## Timing Budgets
- Input to output: < 10ns (100MHz target)
- Clock-to-out: < 2ns
"""
        mocker.patch.object(agent, 'call_llm', return_value=mock_micro_arch)

        # Execute
        context = {"project_dir": valid_project, "mode": "standard"}
        result = agent.execute(context)

        # Verify
        assert result.success is True
        assert result.stage == "microarch"
        assert len(result.artifacts) == 1
        assert "micro_arch.md" in result.artifacts[0]
        assert result.metrics["doc_size_bytes"] > 100
        assert result.metrics["module_sections"] >= 0

        # Verify file was written
        micro_arch_path = Path(valid_project) / "workspace" / "docs" / "micro_arch.md"
        assert micro_arch_path.exists()
        content = micro_arch_path.read_text(encoding="utf-8")
        assert "ALU" in content

    def test_microarch_missing_spec(self, agent, tmp_path):
        """Test MicroArchAgent with missing spec.json."""
        # Create project without spec.json
        project_dir = str(tmp_path)

        # Execute
        context = {"project_dir": project_dir, "mode": "standard"}
        result = agent.execute(context)

        # Verify failure
        assert result.success is False
        assert result.stage == "microarch"
        assert "Missing required inputs" in result.errors[0]
        assert "spec.json" in result.errors[0]

    def test_microarch_micro_arch_md_output(self, agent, valid_project, mocker):
        """Test micro_arch.md output generation."""
        # Mock LLM to return short content
        mock_content = "# Micro-Architecture\n\n## Module: ALU\n\nBasic ALU implementation."
        mocker.patch.object(agent, 'call_llm', return_value=mock_content)

        context = {"project_dir": valid_project, "mode": "standard"}
        result = agent.execute(context)

        # Verify output file
        assert result.success is True
        micro_arch_path = Path(valid_project) / "workspace" / "docs" / "micro_arch.md"
        assert micro_arch_path.exists()

        # Verify content
        content = micro_arch_path.read_text(encoding="utf-8")
        assert "Micro-Architecture" in content
        assert len(content) > 50  # Should have some content

    def test_microarch_llm_error_handling(self, agent, valid_project, mocker):
        """Test LLM error handling."""
        # Mock LLM to raise exception
        mocker.patch.object(
            agent,
            'call_llm',
            side_effect=Exception("LLM service timeout")
        )

        context = {"project_dir": valid_project, "mode": "standard"}
        result = agent.execute(context)

        # Verify error handling
        assert result.success is False
        assert result.stage == "microarch"
        assert "LLM invocation failed" in result.errors[0]
