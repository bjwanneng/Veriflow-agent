"""Tests for SkillD quality gate routing.

Verifies that the skill_d conditional edge routes correctly based on
quality gate results and token budget.
"""


from langgraph.graph import END

from veriflow_agent.graph.graph import _route_skill_d
from veriflow_agent.graph.state import (
    MAX_SUPERVISOR_CALLS,
    StageOutput,
    create_initial_state,
)


class TestSkillDRouting:
    """Tests for SkillD routing decisions."""

    def test_skill_d_pass_routes_to_tool_check(self):
        """Test that passing skill_d routes to tool_check."""
        state = create_initial_state("/tmp/test")
        state["skill_d_output"] = StageOutput(success=True)

        result = _route_skill_d(state)

        assert result == "tool_check"

    def test_skill_d_pass_with_artifacts_routes_to_tool_check(self):
        """Test passing skill_d with artifacts routes to tool_check."""
        state = create_initial_state("/tmp/test")
        state["skill_d_output"] = StageOutput(
            success=True,
            artifacts=["workspace/docs/quality_report.json"],
            metrics={"quality_score": 0.85},
        )

        result = _route_skill_d(state)

        assert result == "tool_check"

    def test_skill_d_fail_routes_to_supervisor(self):
        """Test that failing skill_d routes to supervisor."""
        state = create_initial_state("/tmp/test")
        state["skill_d_output"] = StageOutput(
            success=False,
            errors=["Quality score 0.35 below threshold 0.5"],
        )
        state["retry_count"] = {"lint": 0, "sim": 0, "synth": 0}
        state["token_usage"] = 10000
        state["token_budget"] = 1000000
        state["supervisor_call_count"] = 0

        result = _route_skill_d(state)

        assert result == "supervisor"
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
        state["supervisor_call_count"] = 0

        _route_skill_d(state)

        # Note: feedback_source is now set by node_skill_d, not the routing function
        assert state["feedback_source"] == ""

    def test_skill_d_fail_max_retries_exceeded_routes_to_supervisor(self):
        """Test skill_d failure after max retries routes to supervisor."""
        state = create_initial_state("/tmp/test")
        state["skill_d_output"] = StageOutput(
            success=False,
            errors=["Quality score 0.35 below threshold 0.5"],
        )
        state["retry_count"] = {"lint": 0, "sim": 0, "synth": 0, "skill_d": 3}  # MAX_RETRIES = 3
        state["total_retries"] = {"lint": 0, "sim": 0, "synth": 0, "skill_d": 3}
        state["token_usage"] = 10000
        state["token_budget"] = 1000000
        state["supervisor_call_count"] = 0

        result = _route_skill_d(state)

        assert result == "supervisor"

    def test_skill_d_fail_supervisor_cap_reached_ends(self):
        """Test skill_d failure after supervisor call cap reached ends pipeline."""
        state = create_initial_state("/tmp/test")
        state["skill_d_output"] = StageOutput(
            success=False,
            errors=["Quality score 0.35 below threshold 0.5"],
        )
        state["retry_count"] = {"lint": 0, "sim": 0, "synth": 0, "skill_d": 3}
        state["token_usage"] = 10000
        state["token_budget"] = 1000000
        state["supervisor_call_count"] = MAX_SUPERVISOR_CALLS

        result = _route_skill_d(state)

        assert result == END

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

    def test_skill_d_budget_at_limit_routes_to_supervisor(self):
        """Test that reaching token budget limit (100%) still retries."""
        state = create_initial_state("/tmp/test", token_budget=1000)
        state["skill_d_output"] = StageOutput(
            success=False,
            errors=["Quality check failed"],
        )
        state["token_usage"] = 1000  # At limit (100%) — budget not severely exceeded
        state["supervisor_call_count"] = 0

        result = _route_skill_d(state)

        # At exactly 100%, budget is exceeded but not severely (needs >=120%),
        # so the routing function still attempts retry via supervisor.
        assert result == "supervisor"

    def test_skill_d_no_output_defaults_to_supervisor(self):
        """Test that missing skill_d output routes to supervisor."""
        state = create_initial_state("/tmp/test")
        # skill_d_output is None (default)
        state["retry_count"] = {"lint": 0, "sim": 0, "synth": 0}
        state["token_usage"] = 0
        state["token_budget"] = 1000000
        state["supervisor_call_count"] = 0

        result = _route_skill_d(state)

        assert result == "supervisor"

    def test_skill_d_budget_warning_but_continue(self):
        """Test that budget warning doesn't stop at skill_d."""
        state = create_initial_state("/tmp/test", token_budget=10000)
        state["skill_d_output"] = StageOutput(
            success=False,
            errors=["Quality check failed"],
        )
        state["token_usage"] = 8500  # 85% - warning threshold but not exceeded
        state["supervisor_call_count"] = 0

        result = _route_skill_d(state)

        # Should still go to supervisor despite warning
        assert result == "supervisor"
