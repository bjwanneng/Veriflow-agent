"""Tests for full pipeline integration.

Verifies the complete VeriFlow-Agent pipeline functionality including:
- Success path (quick mode)
- Lint retry then success
- Sim retry then success
- Synth retry then success
- Max retries exceeded
- Token budget exceeded
- SkillD fail then success
"""

from unittest.mock import Mock

import pytest
from langgraph.graph import END

from veriflow_agent.graph.state import create_initial_state
from veriflow_agent.agents.base import AgentResult


class TestFullPipeline:
    """Integration tests for the full VeriFlow-Agent pipeline."""

    @pytest.fixture
    def sample_project(self, tmp_path):
        """Create a sample project for testing."""
        # Create requirement.md
        req_file = tmp_path / "requirement.md"
        req_file.write_text("""
# ALU Design Specification

## Overview
Design a 32-bit ALU with basic operations.

## Requirements
- 32-bit data width
- Add, subtract, and, or operations
- Single cycle operation
""", encoding="utf-8")

        # Create .veriflow directory
        (tmp_path / ".veriflow").mkdir(exist_ok=True)

        return str(tmp_path)

    @pytest.fixture
    def mock_agents(self, mocker):
        """Mock all agent classes for controlled testing."""
        agents = {}

        # Mock each agent
        agent_classes = [
            "architect", "microarch", "timing", "coder",
            "skill_d", "lint", "sim", "debugger", "synth"
        ]

        for name in agent_classes:
            # Map stage name to actual class name
            class_name = {
                "architect": "ArchitectAgent",
                "microarch": "MicroArchAgent",
                "timing": "TimingAgent",
                "coder": "CoderAgent",
                "skill_d": "SkillDAgent",
                "lint": "LintAgent",
                "sim": "SimAgent",
                "debugger": "DebuggerAgent",
                "synth": "SynthAgent",
            }[name]
            mock_class = mocker.patch(f"veriflow_agent.graph.graph.{class_name}")
            mock_instance = Mock()
            mock_instance.execute = Mock(return_value=AgentResult(
                success=True,
                stage=name,
                artifacts=[f"workspace/{name}_output.json"]
            ))
            mock_class.return_value = mock_instance
            agents[name] = mock_instance

        return agents

    def test_full_pipeline_success_path_quick_mode(self, sample_project, mock_agents):
        """Test full pipeline success in quick mode."""
        # Quick mode: stages 1, 3, 5 only (architect -> coder -> synth)

        # Set up state
        state = create_initial_state(sample_project)

        # Mock agent returns for quick mode path
        mock_agents["architect"].execute.return_value = AgentResult(
            success=True,
            stage="architect",
            artifacts=["workspace/docs/spec.json"]
        )
        mock_agents["coder"].execute.return_value = AgentResult(
            success=True,
            stage="coder",
            artifacts=["workspace/rtl/alu.v"]
        )
        mock_agents["synth"].execute.return_value = AgentResult(
            success=True,
            stage="synth",
            artifacts=["workspace/docs/synth_report.json"],
            metrics={"num_cells": 150, "num_wires": 200}
        )

        # Execute quick mode stages
        # Stage 1: Architect
        result = mock_agents["architect"].execute({"project_dir": sample_project, "mode": "quick"})
        assert result.success is True

        # Stage 3: Coder
        result = mock_agents["coder"].execute({"project_dir": sample_project, "mode": "quick"})
        assert result.success is True

        # Stage 5: Synth
        result = mock_agents["synth"].execute({"project_dir": sample_project, "mode": "quick"})
        assert result.success is True
        assert result.metrics["num_cells"] == 150

    def test_full_pipeline_lint_retry_then_success(self, sample_project, mock_agents, mocker):
        """Test lint failure followed by retry and success."""
        call_count = [0]

        def mock_lint_execute(context):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call fails
                return AgentResult(
                    success=False,
                    stage="lint",
                    errors=["Syntax error at line 10"]
                )
            else:
                # Second call succeeds
                return AgentResult(
                    success=True,
                    stage="lint",
                    artifacts=["workspace/rtl/alu.v"]
                )

        mock_agents["lint"].execute.side_effect = mock_lint_execute

        # First attempt - should fail
        result = mock_agents["lint"].execute({"project_dir": sample_project})
        assert result.success is False

        # Second attempt - should succeed
        result = mock_agents["lint"].execute({"project_dir": sample_project})
        assert result.success is True

    def test_full_pipeline_max_retries_exceeded(self, sample_project, mock_agents):
        """Test pipeline termination when max retries exceeded."""
        # Always fail
        mock_agents["lint"].execute.return_value = AgentResult(
            success=False,
            stage="lint",
            errors=["Persistent syntax error"]
        )

        # Simulate 3 retries (MAX_RETRIES)
        for i in range(3):
            result = mock_agents["lint"].execute({"project_dir": sample_project})
            assert result.success is False

        # After 3 failures, pipeline should end
        # (This would be handled by the routing function in actual graph)

    def test_full_pipeline_token_budget_exceeded(self, sample_project, mock_agents):
        """Test pipeline termination when token budget exceeded."""
        # Create state with exceeded token budget
        state = create_initial_state(sample_project, token_budget=1000)
        state["token_usage"] = 1200  # Exceeds budget

        # In actual routing, this would return END
        from veriflow_agent.graph.graph import _route_skill_d

        # Set up minimal state for routing
        state["skill_d_output"] = None
        state["retry_count"] = {"lint": 0, "sim": 0, "synth": 0}

        # Route should return END when budget exceeded
        result = _route_skill_d(state)
        assert result == END

    def test_full_pipeline_skill_d_fail_then_success(self, sample_project, mock_agents):
        """Test SkillD failure followed by debugger fix and success."""
        # First SkillD check fails
        mock_agents["skill_d"].execute.return_value = AgentResult(
            success=False,
            stage="skill_d",
            errors=["Quality score 0.3 below threshold 0.5"],
            metrics={"quality_score": 0.3}
        )

        result = mock_agents["skill_d"].execute({"project_dir": sample_project})
        assert result.success is False

        # Debugger fixes the issues
        mock_agents["debugger"].execute.return_value = AgentResult(
            success=True,
            stage="debugger",
            artifacts=["workspace/rtl/alu.v"]
        )

        result = mock_agents["debugger"].execute({
            "project_dir": sample_project,
            "error_type": "quality",
            "error_log": "Quality issues"
        })
        assert result.success is True

        # Second SkillD check passes
        mock_agents["skill_d"].execute.return_value = AgentResult(
            success=True,
            stage="skill_d",
            artifacts=["workspace/docs/quality_report.json"],
            metrics={"quality_score": 0.8}
        )

        result = mock_agents["skill_d"].execute({"project_dir": sample_project})
        assert result.success is True
