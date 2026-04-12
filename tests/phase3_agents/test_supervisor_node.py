"""Tests for SupervisorAgent and node_supervisor.

Verifies that:
- SupervisorAgent._parse_decision handles various LLM output formats
- SupervisorAgent._validate_decision sanitizes invalid inputs
- SupervisorAgent._mechanical_fallback produces valid decisions
- node_supervisor correctly updates state fields
"""

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from veriflow_agent.agents.base import AgentResult
from veriflow_agent.agents.supervisor import (
    VALID_ACTIONS,
    VALID_TARGETS,
    SupervisorAgent,
)
from veriflow_agent.graph.graph import node_supervisor
from veriflow_agent.graph.state import (
    MAX_SUPERVISOR_CALLS,
    StageOutput,
    create_initial_state,
)


class TestSupervisorParseDecision:
    """Tests for SupervisorAgent._parse_decision."""

    @pytest.fixture
    def agent(self):
        """Create a SupervisorAgent instance."""
        return SupervisorAgent()

    def test_parse_json_in_code_fences(self, agent):
        """Test parsing JSON inside markdown code fences."""
        llm_output = '''Here is my analysis:

```json
{
    "action": "retry_stage",
    "target_stage": "debugger",
    "hint": "Fix syntax error in ALU module",
    "root_cause": "Missing semicolon at line 10",
    "severity": "high",
    "modules": ["alu.v"]
}
```

The error is a simple syntax issue.'''

        result = agent._parse_decision(llm_output)

        assert result is not None
        assert result["action"] == "retry_stage"
        assert result["target_stage"] == "debugger"
        assert "semicolon" in result["root_cause"].lower()
        assert result["severity"] == "high"
        assert result["modules"] == ["alu.v"]

    def test_parse_json_in_plain_code_fences(self, agent):
        """Test parsing JSON inside plain code fences (no language tag)."""
        llm_output = '''```
{
    "action": "escalate_stage",
    "target_stage": "microarch",
    "hint": "Design flaw",
    "root_cause": "Fundamental architecture issue",
    "severity": "high",
    "modules": []
}
```'''

        result = agent._parse_decision(llm_output)

        assert result is not None
        assert result["action"] == "escalate_stage"
        assert result["target_stage"] == "microarch"

    def test_parse_raw_json_in_text(self, agent):
        """Test parsing raw JSON object embedded in text."""
        llm_output = '''Based on my analysis, I recommend:
{"action": "continue", "target_stage": "sim", "hint": "", "root_cause": "Transient", "severity": "low", "modules": []}
Please proceed accordingly.'''

        result = agent._parse_decision(llm_output)

        assert result is not None
        assert result["action"] == "continue"
        assert result["target_stage"] == "sim"

    def test_parse_entire_output_as_json(self, agent):
        """Test parsing when the entire output is a JSON object."""
        llm_output = '{"action": "abort", "target_stage": "", "hint": "", "root_cause": "Unrecoverable", "severity": "high", "modules": []}'

        result = agent._parse_decision(llm_output)

        assert result is not None
        assert result["action"] == "abort"

    def test_parse_invalid_input_returns_none(self, agent):
        """Test that invalid input returns None."""
        llm_output = "I cannot determine the issue. Please review manually."

        result = agent._parse_decision(llm_output)

        assert result is None

    def test_parse_empty_string_returns_none(self, agent):
        """Test that empty string returns None."""
        result = agent._parse_decision("")

        assert result is None

    def test_parse_whitespace_only_returns_none(self, agent):
        """Test that whitespace-only input returns None."""
        result = agent._parse_decision("   \n\t  ")

        assert result is None

    def test_parse_none_returns_none(self, agent):
        """Test that None input returns None (gracefully handles falsy)."""
        result = agent._parse_decision(None)

        assert result is None

    def test_parse_malformed_json_in_fences_returns_none(self, agent):
        """Test that malformed JSON in code fences returns None."""
        llm_output = '''```json
{action: retry_stage, target_stage: debugger}
```'''

        result = agent._parse_decision(llm_output)

        assert result is None

    def test_parse_json_without_action_key_in_text(self, agent):
        """Test that JSON without 'action' key in text is not matched by strategy 2."""
        llm_output = '{"target_stage": "debugger", "hint": "fix it"}'

        result = agent._parse_decision(llm_output)

        # Strategy 3 (full output parse) also requires "action" key
        assert result is None


class TestSupervisorValidateDecision:
    """Tests for SupervisorAgent._validate_decision."""

    @pytest.fixture
    def agent(self):
        """Create a SupervisorAgent instance."""
        return SupervisorAgent()

    def test_valid_decision_passes_through(self, agent):
        """Test that a valid decision passes through unchanged."""
        decision = {
            "action": "retry_stage",
            "target_stage": "debugger",
            "hint": "Fix the syntax error",
            "root_cause": "Missing semicolon",
            "severity": "high",
            "modules": ["alu.v"],
        }

        result = agent._validate_decision(decision)

        assert result["action"] == "retry_stage"
        assert result["target_stage"] == "debugger"
        assert result["hint"] == "Fix the syntax error"
        assert result["root_cause"] == "Missing semicolon"
        assert result["severity"] == "high"
        assert result["modules"] == ["alu.v"]

    def test_invalid_action_defaults_to_retry_stage(self, agent):
        """Test that an invalid action defaults to 'retry_stage'."""
        decision = {
            "action": "invalid_action",
            "target_stage": "debugger",
            "hint": "",
            "root_cause": "",
            "severity": "medium",
            "modules": [],
        }

        result = agent._validate_decision(decision)

        assert result["action"] == "retry_stage"

    def test_invalid_target_stage_defaults_to_debugger(self, agent):
        """Test that an invalid target_stage defaults to 'debugger'."""
        decision = {
            "action": "retry_stage",
            "target_stage": "nonexistent_stage",
            "hint": "",
            "root_cause": "",
            "severity": "medium",
            "modules": [],
        }

        result = agent._validate_decision(decision)

        assert result["target_stage"] == "debugger"

    def test_missing_action_defaults_to_retry_stage(self, agent):
        """Test that missing action defaults to 'retry_stage'."""
        decision = {
            "target_stage": "coder",
            "hint": "",
            "root_cause": "",
            "severity": "medium",
            "modules": [],
        }

        result = agent._validate_decision(decision)

        assert result["action"] == "retry_stage"

    def test_missing_target_stage_defaults_to_debugger(self, agent):
        """Test that missing target_stage defaults to 'debugger'."""
        decision = {
            "action": "retry_stage",
            "hint": "",
            "root_cause": "",
            "severity": "medium",
            "modules": [],
        }

        result = agent._validate_decision(decision)

        assert result["target_stage"] == "debugger"

    def test_invalid_severity_defaults_to_medium(self, agent):
        """Test that an invalid severity defaults to 'medium'."""
        decision = {
            "action": "retry_stage",
            "target_stage": "debugger",
            "hint": "",
            "root_cause": "",
            "severity": "critical",
            "modules": [],
        }

        result = agent._validate_decision(decision)

        assert result["severity"] == "medium"

    def test_valid_severity_values(self, agent):
        """Test all valid severity values pass through."""
        for severity in ("low", "medium", "high"):
            decision = {
                "action": "retry_stage",
                "target_stage": "debugger",
                "hint": "",
                "root_cause": "",
                "severity": severity,
                "modules": [],
            }

            result = agent._validate_decision(decision)

            assert result["severity"] == severity

    def test_modules_not_list_defaults_to_empty_list(self, agent):
        """Test that non-list modules default to empty list."""
        decision = {
            "action": "retry_stage",
            "target_stage": "debugger",
            "hint": "",
            "root_cause": "",
            "severity": "medium",
            "modules": "not_a_list",
        }

        result = agent._validate_decision(decision)

        assert result["modules"] == []

    def test_hint_truncated_to_200_chars(self, agent):
        """Test that hint is truncated to 200 characters."""
        long_hint = "x" * 300
        decision = {
            "action": "retry_stage",
            "target_stage": "debugger",
            "hint": long_hint,
            "root_cause": "",
            "severity": "medium",
            "modules": [],
        }

        result = agent._validate_decision(decision)

        assert len(result["hint"]) == 200

    def test_root_cause_truncated_to_200_chars(self, agent):
        """Test that root_cause is truncated to 200 characters."""
        long_cause = "y" * 300
        decision = {
            "action": "retry_stage",
            "target_stage": "debugger",
            "hint": "",
            "root_cause": long_cause,
            "severity": "medium",
            "modules": [],
        }

        result = agent._validate_decision(decision)

        assert len(result["root_cause"]) == 200

    def test_all_valid_actions(self, agent):
        """Test that all valid actions pass through."""
        for action in VALID_ACTIONS:
            decision = {
                "action": action,
                "target_stage": "debugger",
                "hint": "",
                "root_cause": "",
                "severity": "medium",
                "modules": [],
            }

            result = agent._validate_decision(decision)

            assert result["action"] == action

    def test_all_valid_targets(self, agent):
        """Test that all valid target stages pass through."""
        for target in VALID_TARGETS:
            decision = {
                "action": "retry_stage",
                "target_stage": target,
                "hint": "",
                "root_cause": "",
                "severity": "medium",
                "modules": [],
            }

            result = agent._validate_decision(decision)

            assert result["target_stage"] == target


class TestSupervisorMechanicalFallback:
    """Tests for SupervisorAgent._mechanical_fallback."""

    @pytest.fixture
    def agent(self):
        """Create a SupervisorAgent instance."""
        return SupervisorAgent()

    def test_syntax_error_falls_back_to_coder(self, agent):
        """Test that syntax errors fall back to coder."""
        error_log = "rtl/top.v:10: syntax error: unexpected token"

        result = agent._mechanical_fallback(error_log, "lint")

        assert result.success is True
        assert result.stage == "supervisor"
        assert result.metrics["action"] == "retry_stage"
        assert result.metrics["target_stage"] == "coder"
        assert "syntax" in result.metrics["root_cause"]

    def test_logic_error_from_sim_falls_back_to_microarch(self, agent):
        """Test that logic errors from sim fall back to microarch."""
        error_log = "simulation fail: mismatch at time 100ns"

        result = agent._mechanical_fallback(error_log, "sim")

        assert result.success is True
        assert result.metrics["target_stage"] == "microarch"

    def test_timing_error_from_synth_falls_back_to_timing(self, agent):
        """Test that timing errors from synth fall back to timing."""
        error_log = "timing violation on critical path: slack negative"

        result = agent._mechanical_fallback(error_log, "synth")

        assert result.success is True
        assert result.metrics["target_stage"] == "timing"

    def test_resource_error_from_synth_falls_back_to_timing(self, agent):
        """Test that resource errors from synth fall back to timing."""
        error_log = "area exceeds limit: design too large"

        result = agent._mechanical_fallback(error_log, "synth")

        assert result.success is True
        assert result.metrics["target_stage"] == "timing"

    def test_unknown_error_falls_back_to_lint(self, agent):
        """Test that unknown errors fall back to lint (conservative)."""
        error_log = "something went wrong but we don't know what"

        result = agent._mechanical_fallback(error_log, "lint")

        assert result.success is True
        assert result.metrics["target_stage"] == "lint"

    def test_empty_error_log_falls_back_to_lint(self, agent):
        """Test that empty error log falls back to lint."""
        result = agent._mechanical_fallback("", "lint")

        assert result.success is True
        assert result.metrics["target_stage"] == "lint"
        assert "mechanical fallback" in result.metrics["root_cause"]

    def test_fallback_includes_severity(self, agent):
        """Test that mechanical fallback always has severity='medium'."""
        result = agent._mechanical_fallback("syntax error at line 10", "lint")

        assert result.metrics["severity"] == "medium"

    def test_fallback_includes_empty_modules(self, agent):
        """Test that mechanical fallback always has empty modules list."""
        result = agent._mechanical_fallback("syntax error", "lint")

        assert result.metrics["modules"] == []


class TestNodeSupervisor:
    """Tests for node_supervisor graph node function."""

    def test_node_supervisor_state_updates(self):
        """Test that node_supervisor updates all expected state fields."""
        state = create_initial_state("/tmp/test")
        state["lint_output"] = StageOutput(
            success=False,
            errors=["rtl/top.v:10: syntax error: unexpected token"],
        )
        state["feedback_source"] = "lint"
        state["supervisor_call_count"] = 0
        state["supervisor_history"] = []

        # Mock _run_stage to avoid actual LLM call
        mock_supervisor_output = StageOutput(
            success=True,
            metrics={
                "action": "retry_stage",
                "target_stage": "debugger",
                "hint": "Fix syntax error in ALU",
                "root_cause": "Missing semicolon",
                "severity": "high",
                "modules": ["alu.v"],
            },
        )
        with patch(
            "veriflow_agent.graph.graph._run_stage",
            return_value={"supervisor_output": mock_supervisor_output},
        ):
            updates = node_supervisor(state)

        # Verify all state updates
        assert updates["supervisor_call_count"] == 1
        assert updates["supervisor_decision"]["action"] == "retry_stage"
        assert updates["supervisor_decision"]["target_stage"] == "debugger"
        assert updates["supervisor_hint"] == "Fix syntax error in ALU"
        assert updates["target_rollback_stage"] == "debugger"
        assert updates["feedback_source"] == "lint"

        # Verify history entry
        history = updates["supervisor_history"]
        assert len(history) == 1
        assert history[0]["action"] == "retry_stage"
        assert history[0]["failing_stage"] == "lint"
        assert history[0]["call_number"] == 1

    def test_node_supervisor_increments_call_count(self):
        """Test that supervisor_call_count is incremented."""
        state = create_initial_state("/tmp/test")
        state["lint_output"] = StageOutput(
            success=False,
            errors=["syntax error"],
        )
        state["feedback_source"] = "lint"
        state["supervisor_call_count"] = 3
        state["supervisor_history"] = []

        mock_output = StageOutput(
            success=True,
            metrics={
                "action": "retry_stage",
                "target_stage": "debugger",
                "hint": "",
                "root_cause": "syntax",
                "severity": "medium",
                "modules": [],
            },
        )
        with patch(
            "veriflow_agent.graph.graph._run_stage",
            return_value={"supervisor_output": mock_output},
        ):
            updates = node_supervisor(state)

        assert updates["supervisor_call_count"] == 4

    def test_node_supervisor_cap_reached_returns_abort(self):
        """Test that reaching MAX_SUPERVISOR_CALLS returns abort decision."""
        state = create_initial_state("/tmp/test")
        state["supervisor_call_count"] = MAX_SUPERVISOR_CALLS
        state["feedback_source"] = "lint"

        updates = node_supervisor(state)

        # Should not call _run_stage at all
        assert updates["supervisor_decision"]["action"] == "abort"
        assert updates["supervisor_call_count"] == MAX_SUPERVISOR_CALLS  # unchanged

    def test_node_supervisor_history_accumulates(self):
        """Test that supervisor_history accumulates entries."""
        state = create_initial_state("/tmp/test")
        state["sim_output"] = StageOutput(
            success=False,
            errors=["simulation fail: mismatch"],
        )
        state["feedback_source"] = "sim"
        state["supervisor_call_count"] = 1
        state["supervisor_history"] = [
            {
                "action": "retry_stage",
                "target_stage": "debugger",
                "failing_stage": "lint",
                "call_number": 1,
                "timestamp": time.time(),
            }
        ]

        mock_output = StageOutput(
            success=True,
            metrics={
                "action": "escalate_stage",
                "target_stage": "microarch",
                "hint": "Redesign ALU",
                "root_cause": "logic error",
                "severity": "high",
                "modules": [],
            },
        )
        with patch(
            "veriflow_agent.graph.graph._run_stage",
            return_value={"supervisor_output": mock_output},
        ):
            updates = node_supervisor(state)

        history = updates["supervisor_history"]
        assert len(history) == 2
        assert history[0]["failing_stage"] == "lint"  # previous
        assert history[1]["failing_stage"] == "sim"  # current
        assert history[1]["call_number"] == 2

    def test_node_supervisor_strategy_override_injection(self):
        """Test that hint is injected into strategy_override for target stage."""
        state = create_initial_state("/tmp/test")
        state["coder_output"] = StageOutput(
            success=False,
            errors=["RTL generation failed"],
        )
        state["feedback_source"] = "coder"
        state["supervisor_call_count"] = 0
        state["supervisor_history"] = []
        state["strategy_override"] = {}

        mock_output = StageOutput(
            success=True,
            metrics={
                "action": "retry_stage",
                "target_stage": "coder",
                "hint": "Simplify the module interface",
                "root_cause": "Over-complex design",
                "severity": "medium",
                "modules": [],
            },
        )
        with patch(
            "veriflow_agent.graph.graph._run_stage",
            return_value={"supervisor_output": mock_output},
        ):
            updates = node_supervisor(state)

        assert "coder" in updates["strategy_override"]
        assert updates["strategy_override"]["coder"] == "Simplify the module interface"

    def test_node_supervisor_mechanical_fallback_on_llm_failure(self):
        """Test that supervisor falls back to mechanical routing when LLM fails."""
        state = create_initial_state("/tmp/test")
        state["lint_output"] = StageOutput(
            success=False,
            errors=["rtl/top.v:10: syntax error: unexpected token"],
        )
        state["feedback_source"] = "lint"
        state["supervisor_call_count"] = 0
        state["supervisor_history"] = []

        # Mock _run_stage to return no supervisor_output (simulates LLM failure)
        with patch(
            "veriflow_agent.graph.graph._run_stage",
            return_value={},  # No supervisor_output
        ):
            updates = node_supervisor(state)

        # Should fall back to mechanical categorization
        assert updates["supervisor_decision"]["action"] == "retry_stage"
        assert updates["supervisor_decision"]["target_stage"] == "coder"  # syntax → coder
        assert updates["supervisor_call_count"] == 1

    def test_node_supervisor_identifies_failing_stage_from_feedback(self):
        """Test that failing stage is correctly identified from feedback_source."""
        state = create_initial_state("/tmp/test")
        state["synth_output"] = StageOutput(
            success=False,
            errors=["timing violation on path clk -> out"],
        )
        state["feedback_source"] = "synth"
        state["supervisor_call_count"] = 0
        state["supervisor_history"] = []

        mock_output = StageOutput(
            success=True,
            metrics={
                "action": "escalate_stage",
                "target_stage": "timing",
                "hint": "Revise timing constraints",
                "root_cause": "timing",
                "severity": "high",
                "modules": [],
            },
        )
        with patch(
            "veriflow_agent.graph.graph._run_stage",
            return_value={"supervisor_output": mock_output},
        ):
            updates = node_supervisor(state)

        assert updates["feedback_source"] == "synth"
        assert updates["supervisor_decision"]["target_stage"] == "timing"

    def test_node_supervisor_history_capped_at_20(self):
        """Test that supervisor_history is capped at 20 entries."""
        state = create_initial_state("/tmp/test")
        state["lint_output"] = StageOutput(
            success=False,
            errors=["syntax error"],
        )
        state["feedback_source"] = "lint"
        # Use a count below MAX_SUPERVISOR_CALLS (8) so we don't hit the early abort.
        # Pre-fill history with 20 entries so adding 1 more triggers the cap.
        state["supervisor_call_count"] = 5
        state["supervisor_history"] = [
            {"action": "retry_stage", "call_number": i}
            for i in range(20)
        ]

        mock_output = StageOutput(
            success=True,
            metrics={
                "action": "retry_stage",
                "target_stage": "debugger",
                "hint": "",
                "root_cause": "",
                "severity": "medium",
                "modules": [],
            },
        )
        with patch(
            "veriflow_agent.graph.graph._run_stage",
            return_value={"supervisor_output": mock_output},
        ):
            updates = node_supervisor(state)

        # History should be capped at 20 (21 entries total, sliced to last 20)
        assert len(updates["supervisor_history"]) == 20
        # The newest entry should be at the end
        assert updates["supervisor_history"][-1]["call_number"] == 6
