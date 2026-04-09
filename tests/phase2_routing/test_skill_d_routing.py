"""Tests for SkillD quality gate routing.

Verifies that the skill_d conditional edge routes correctly based on
quality gate results and token budget.
"""


from veriflow_agent.graph.graph import _route_skill_d
from veriflow_agent.graph.state import (
    StageOutput,
    create_initial_state,
)


class TestSkillDRouting:
    """Tests for SkillD routing decisions."""

    def test_skill_d_pass_routes_to_lint(self):
        """Test that passing skill_d routes to lint."""
        state = create_initial_state("/tmp/test")
        state["skill_d_output"] = StageOutput(success=True)

        result = _route_skill_d(state)

        assert result == "lint"

    def test_skill_d_pass_with_artifacts_routes_to_lint(self):
        """Test passing skill_d with artifacts routes to lint."""
        state = create_initial_state("/tmp/test")
        state["skill_d_output"] = StageOutput(
            success=True,
            artifacts=["workspace/docs/quality_report.json"],
            metrics={"quality_score": 0.85},
        )

        result = _route_skill_d(state)

        assert result == "lint"

    def test_skill_d_fail_routes_to_debugger(self):
        """Test that failing skill_d routes to debugger."""
        state = create_initial_state("/tmp/test")
        state["skill_d_output"] = StageOutput(
            success=False,
            errors=["Quality score 0.35 below threshold 0.5"],
        )
        state["retry_count"] = {"lint": 0, "sim": 0, "synth": 0}
        state["token_usage"] = 10000
        state["token_budget"] = 1000000

        result = _route_skill_d(state)

        assert result == "debugger"
        # Note: feedback_source is now set by node_skill_d, not the routing function

    def test_skill_d_fail_sets_feedback_source(self):
        """Test that skill_d failure sets feedback source."""
        state = create_initial_state("/tmp/test")
        state["skill_d_output"] = StageOutput(
            success=False,
            errors=["Quality check failed"],
        )
        state["token_usage"] = 5000
        state["token_budget"] = 1000000

        _route_skill_d(state)

        # Note: feedback_source is now set by node_skill_d, not the routing function
        assert state["feedback_source"] == ""

    def test_skill_d_budget_exceeded_ends(self):
        """Test that exceeding token budget ends the pipeline."""
        from langgraph.graph import END

        state = create_initial_state("/tmp/test", token_budget=1000)
        state["skill_d_output"] = StageOutput(
            success=False,
            errors=["Quality check failed"],
        )
        state["token_usage"] = 1200  # Exceeds budget

        result = _route_skill_d(state)

        assert result == END

    def test_skill_d_budget_at_limit_ends(self):
        """Test that reaching token budget limit ends the pipeline."""
        from langgraph.graph import END

        state = create_initial_state("/tmp/test", token_budget=1000)
        state["skill_d_output"] = StageOutput(
            success=False,
            errors=["Quality check failed"],
        )
        state["token_usage"] = 1000  # At limit

        result = _route_skill_d(state)

        assert result == END

    def test_skill_d_no_output_defaults_to_debugger(self):
        """Test that missing skill_d output routes to debugger."""
        state = create_initial_state("/tmp/test")
        # skill_d_output is None (default)
        state["retry_count"] = {"lint": 0, "sim": 0, "synth": 0}
        state["token_usage"] = 0
        state["token_budget"] = 1000000

        result = _route_skill_d(state)

        assert result == "debugger"

    def test_skill_d_budget_warning_but_continue(self):
        """Test that budget warning doesn't stop at skill_d."""
        state = create_initial_state("/tmp/test", token_budget=10000)
        state["skill_d_output"] = StageOutput(
            success=False,
            errors=["Quality check failed"],
        )
        state["token_usage"] = 8500  # 85% - warning threshold but not exceeded

        result = _route_skill_d(state)

        # Should still go to debugger despite warning
        assert result == "debugger"
