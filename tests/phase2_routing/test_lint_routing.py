"""Tests for Lint check routing.

Verifies that the lint conditional edge routes correctly based on
lint results, retry counts, and token budget.
"""


from langgraph.graph import END

from veriflow_agent.graph.graph import _route_lint
from veriflow_agent.graph.state import (
    MAX_SUPERVISOR_CALLS,
    StageOutput,
    create_initial_state,
)


class TestLintRouting:
    """Tests for Lint routing decisions."""

    def test_lint_pass_routes_to_sim(self):
        """Test that passing lint routes to sim."""
        state = create_initial_state("/tmp/test")
        state["lint_output"] = StageOutput(success=True)

        result = _route_lint(state)

        assert result == "sim"

    def test_lint_pass_with_artifacts_routes_to_sim(self):
        """Test passing lint with artifacts routes to sim."""
        state = create_initial_state("/tmp/test")
        state["lint_output"] = StageOutput(
            success=True,
            artifacts=["workspace/rtl/top.v"],
            metrics={"lint_errors": 0, "lint_warnings": 2},
        )

        result = _route_lint(state)

        assert result == "sim"

    def test_lint_fail_first_retry_routes_to_supervisor(self):
        """Test lint failure on first retry routes to supervisor."""
        state = create_initial_state("/tmp/test")
        state["lint_output"] = StageOutput(
            success=False,
            errors=["rtl/top.v:10: syntax error"],
        )
        state["retry_count"] = {"lint": 0, "sim": 0, "synth": 0}
        state["token_usage"] = 10000
        state["token_budget"] = 1000000
        state["supervisor_call_count"] = 0

        result = _route_lint(state)

        assert result == "supervisor"
        # Note: feedback_source is now set by node_lint, not the routing function

    def test_lint_fail_second_retry_routes_to_supervisor(self):
        """Test lint failure on second retry still routes to supervisor."""
        state = create_initial_state("/tmp/test")
        state["lint_output"] = StageOutput(
            success=False,
            errors=["rtl/top.v:10: syntax error"],
        )
        state["retry_count"] = {"lint": 2, "sim": 0, "synth": 0}
        state["token_usage"] = 50000
        state["token_budget"] = 1000000
        state["supervisor_call_count"] = 0

        result = _route_lint(state)

        assert result == "supervisor"

    def test_lint_fail_max_retries_exceeded_routes_to_supervisor(self):
        """Test lint failure after max retries routes to supervisor."""
        state = create_initial_state("/tmp/test")
        state["lint_output"] = StageOutput(
            success=False,
            errors=["rtl/top.v:10: syntax error"],
        )
        state["retry_count"] = {"lint": 3, "sim": 0, "synth": 0}  # MAX_RETRIES = 3
        state["total_retries"] = {"lint": 3, "sim": 0, "synth": 0}
        state["token_usage"] = 50000
        state["token_budget"] = 1000000
        state["supervisor_call_count"] = 0

        result = _route_lint(state)

        assert result == "supervisor"

    def test_lint_fail_supervisor_cap_reached_ends(self):
        """Test lint failure after supervisor call cap reached ends pipeline."""
        state = create_initial_state("/tmp/test")
        state["lint_output"] = StageOutput(
            success=False,
            errors=["rtl/top.v:10: syntax error"],
        )
        state["retry_count"] = {"lint": 3, "sim": 0, "synth": 0}
        state["token_usage"] = 50000
        state["token_budget"] = 1000000
        state["supervisor_call_count"] = MAX_SUPERVISOR_CALLS

        result = _route_lint(state)

        assert result == END

    def test_lint_budget_exceeded_ends(self):
        """Test lint with exceeded token budget ends pipeline."""
        state = create_initial_state("/tmp/test", token_budget=10000)
        state["lint_output"] = StageOutput(
            success=False,
            errors=["rtl/top.v:10: syntax error"],
        )
        state["retry_count"] = {"lint": 0, "sim": 0, "synth": 0}
        state["token_usage"] = 12000  # Exceeds budget

        result = _route_lint(state)

        assert result == END

    def test_lint_no_output_defaults_to_supervisor(self):
        """Test missing lint output routes to supervisor."""
        state = create_initial_state("/tmp/test")
        # lint_output is None
        state["retry_count"] = {"lint": 0, "sim": 0, "synth": 0}
        state["token_usage"] = 0
        state["token_budget"] = 1000000
        state["supervisor_call_count"] = 0

        result = _route_lint(state)

        assert result == "supervisor"
        # Note: feedback_source is now set by node_lint, not the routing function

    def test_lint_fail_routing_is_readonly(self):
        """Test that routing function does not mutate state directly."""
        state = create_initial_state("/tmp/test")
        state["lint_output"] = StageOutput(
            success=False,
            errors=["syntax error"],
        )
        state["retry_count"] = {"lint": 0, "sim": 0, "synth": 0}
        state["token_usage"] = 10000
        state["token_budget"] = 1000000
        state["supervisor_call_count"] = 0

        _route_lint(state)

        # feedback_source should NOT be set by the routing function
        assert state["feedback_source"] == ""
