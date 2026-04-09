"""Tests for ArchitectAgent node.

Verifies the ArchitectAgent's independent functionality including:
- Input validation (requirement.md existence)
- spec.json extraction and validation
- LLM error handling
- Output artifact generation
"""

import json
from pathlib import Path

import pytest

from veriflow_agent.agents.architect import ArchitectAgent


class TestArchitectAgent:
    """Tests for ArchitectAgent node functionality."""

    @pytest.fixture
    def agent(self):
        """Create an ArchitectAgent instance."""
        return ArchitectAgent()

    @pytest.fixture
    def valid_project(self, tmp_path):
        """Create a valid project structure with requirement.md."""
        # Create requirement.md
        req_file = tmp_path / "requirement.md"
        req_file.write_text("""
# ALU Design Specification

## Overview
Design a 32-bit ALU with add, subtract, and, or, xor operations.

## Requirements
- 32-bit data width
- 4 operation modes
- Single cycle operation
""", encoding="utf-8")

        # Create .veriflow directory
        veriflow_dir = tmp_path / ".veriflow"
        veriflow_dir.mkdir(exist_ok=True)

        return str(tmp_path)

    def test_architect_with_valid_requirement(self, agent, valid_project, mocker):
        """Test ArchitectAgent with valid requirement.md."""
        # Mock LLM call
        mock_spec = {
            "design_name": "alu",
            "target_kpis": {"frequency_mhz": 100},
            "modules": [
                {
                    "module_name": "alu",
                    "ports": [
                        {"name": "clk", "direction": "input", "width": 1},
                        {"name": "a", "direction": "input", "width": 32},
                    ]
                }
            ]
        }

        mock_llm_output = f"```json\n{json.dumps(mock_spec, indent=2)}\n```"
        mocker.patch.object(agent, 'call_llm', return_value=mock_llm_output)

        # Execute
        context = {"project_dir": valid_project, "mode": "standard"}
        result = agent.execute(context)

        # Verify
        assert result.success is True
        assert result.stage == "architect"
        assert len(result.artifacts) == 1
        assert "spec.json" in result.artifacts[0]
        assert result.metrics["design_name"] == "alu"
        assert result.metrics["module_count"] == 1

        # Verify spec.json was written
        spec_path = Path(valid_project) / "workspace" / "docs" / "spec.json"
        assert spec_path.exists()
        saved_spec = json.loads(spec_path.read_text())
        assert saved_spec["design_name"] == "alu"

    def test_architect_missing_requirement(self, agent, tmp_path):
        """Test ArchitectAgent with missing requirement.md."""
        # Create project without requirement.md
        project_dir = str(tmp_path)

        # Execute
        context = {"project_dir": project_dir, "mode": "standard"}
        result = agent.execute(context)

        # Verify failure
        assert result.success is False
        assert result.stage == "architect"
        assert "Missing required inputs" in result.errors[0]
        assert "requirement.md" in result.errors[0]

    def test_architect_spec_json_extraction(self, agent, valid_project, mocker):
        """Test spec.json extraction from LLM output."""
        # Test with JSON in markdown code block
        mock_spec = {
            "design_name": "test_design",
            "target_kpis": {"frequency_mhz": 200},
            "modules": [{"module_name": "top", "ports": []}]
        }

        mock_output = f"Some text before\n```json\n{json.dumps(mock_spec)}\n```\nAfter"
        mocker.patch.object(agent, 'call_llm', return_value=mock_output)

        context = {"project_dir": valid_project, "mode": "standard"}
        result = agent.execute(context)

        assert result.success is True
        assert result.metrics["design_name"] == "test_design"

    def test_architect_spec_validation(self, agent, valid_project, mocker):
        """Test spec.json validation."""
        # Test with invalid spec (missing required fields)
        invalid_spec = {
            "target_kpis": {"frequency_mhz": 100},
            # Missing design_name and modules
        }

        mock_output = f"```json\n{json.dumps(invalid_spec)}\n```"
        mocker.patch.object(agent, 'call_llm', return_value=mock_output)

        context = {"project_dir": valid_project, "mode": "standard"}
        result = agent.execute(context)

        # Should still succeed but with validation errors
        assert result.success is False
        assert "design_name" in str(result.errors)

    def test_architect_llm_error_handling(self, agent, valid_project, mocker):
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
        assert result.stage == "architect"
        assert "LLM invocation failed" in result.errors[0]

    def test_architect_output_artifacts(self, agent, valid_project, mocker):
        """Test output artifact generation and tracking."""
        mock_spec = {
            "design_name": "alu",
            "target_kpis": {"frequency_mhz": 100},
            "modules": [{"module_name": "alu", "ports": []}]
        }

        mock_output = f"```json\n{json.dumps(mock_spec)}\n```"
        mocker.patch.object(agent, 'call_llm', return_value=mock_output)

        context = {"project_dir": valid_project, "mode": "standard"}
        result = agent.execute(context)

        # Verify artifact tracking
        assert len(result.artifacts) == 1
        artifact_path = result.artifacts[0]
        assert "spec.json" in artifact_path

        # Verify file exists
        assert Path(artifact_path).exists()

        # Verify metrics
        assert "checksum" in result.metrics
        assert "module_count" in result.metrics
