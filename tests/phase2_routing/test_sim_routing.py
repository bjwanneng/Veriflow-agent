"""Tests for Sim check routing.

Verifies that the sim conditional edge routes correctly based on
simulation results, retry counts, and token budget.
"""


from langgraph.graph import END

from veriflow_agent.graph.graph import _route_sim
from veriflow_agent.graph.state import (
    MAX_SUPERVISOR_CALLS,
    StageOutput,
    create_initial_state,
)


class TestSimRouting:
    """Tests for Sim routing decisions."""

    def test_sim_pass_routes_to_synth(self):
        """Test that passing sim routes to synth."""
        state = create_initial_state("/tmp/test")
        state["sim_output"] = StageOutput(success=True)

        result = _route_sim(state)

        assert result == "synth"

    def test_sim_pass_with_artifacts_routes_to_synth(self):
        """Test passing sim with artifacts routes to synth."""
        state = create_initial_state("/tmp/test")
        state["sim_output"] = StageOutput(
            success=True,
            artifacts=["sim.log"],
            metrics={"pass_count": 5, "fail_count": 0},
        )

        result = _route_sim(state)

        assert result == "synth"

    def test_sim_fail_first_retry_routes_to_supervisor(self):
        """Test sim failure on first retry routes to supervisor."""
        state = create_initial_state("/tmp/test")
        state["sim_output"] = StageOutput(
            success=False,
            errors=["Test 1: FAIL - mismatch at 100ns"],
        )
        state["retry_count"] = {"lint": 0, "sim": 0, "synth": 0}
        state["token_usage"] = 50000
        state["token_budget"] = 1000000
        state["supervisor_call_count"] = 0

        result = _route_sim(state)

        assert result == "supervisor"
        # Note: feedback_source is now set by node_sim, not the routing function

    def test_sim_fail_second_retry_routes_to_supervisor(self):
        """Test sim failure on second retry still routes to supervisor."""
        state = create_initial_state("/tmp/test")
        state["sim_output"] = StageOutput(
            success=False,
            errors=["Assertion failed"],
        )
        state["retry_count"] = {"lint": 1, "sim": 2, "synth": 0}
        state["token_usage"] = 80000
        state["token_budget"] = 1000000
        state["supervisor_call_count"] = 0

        result = _route_sim(state)

        assert result == "supervisor"

    def test_sim_fail_max_retries_exceeded_routes_to_supervisor(self):
        """Test sim failure after max retries routes to supervisor."""
        state = create_initial_state("/tmp/test")
        state["sim_output"] = StageOutput(
            success=False,
            errors=["Simulation failed"],
        )
        state["retry_count"] = {"lint": 1, "sim": 3, "synth": 0}  # MAX_RETRIES = 3
        state["total_retries"] = {"lint": 1, "sim": 3, "synth": 0}
        state["token_usage"] = 100000
        state["token_budget"] = 1000000
        state["supervisor_call_count"] = 0

        result = _route_sim(state)

        assert result == "supervisor"

    def test_sim_fail_supervisor_cap_reached_ends(self):
        """Test sim failure after supervisor call cap reached ends pipeline."""
        state = create_initial_state("/tmp/test")
        state["sim_output"] = StageOutput(
            success=False,
            errors=["Simulation failed"],
        )
        state["retry_count"] = {"lint": 1, "sim": 3, "synth": 0}
        state["token_usage"] = 100000
        state["token_budget"] = 1000000
        state["supervisor_call_count"] = MAX_SUPERVISOR_CALLS

        result = _route_sim(state)

        assert result == END

    def test_sim_budget_exceeded_ends(self):
        """Test sim with exceeded token budget ends pipeline."""
        state = create_initial_state("/tmp/test", token_budget=50000)
        state["sim_output"] = StageOutput(
            success=False,
            errors=["Simulation error"],
        )
        state["retry_count"] = {"lint": 0, "sim": 0, "synth": 0}
        state["token_usage"] = 60000  # Exceeds budget

        result = _route_sim(state)

        assert result == END

    def test_sim_no_output_defaults_to_supervisor(self):
        """Test missing sim output routes to supervisor."""
        state = create_initial_state("/tmp/test")
        # sim_output is None
        state["retry_count"] = {"lint": 0, "sim": 0, "synth": 0}
        state["token_usage"] = 0
        state["token_budget"] = 1000000
        state["supervisor_call_count"] = 0

        result = _route_sim(state)

        assert result == "supervisor"
        # Note: feedback_source is now set by node_sim, not the routing function

    def test_sim_fail_sets_feedback_source(self):
        """Test that sim failure sets feedback source."""
        state = create_initial_state("/tmp/test")
        state["sim_output"] = StageOutput(
            success=False,
            errors=["Output mismatch"],
        )
        state["retry_count"] = {"lint": 0, "sim": 0, "synth": 0}
        state["token_usage"] = 10000
        state["token_budget"] = 1000000
        state["supervisor_call_count"] = 0

        _route_sim(state)

        # Note: feedback_source is now set by node_sim, not the routing function
        assert state["feedback_source"] == ""

    def test_sim_pass_with_logic_error_does_not_affect_routing(self):
        """Test that passing sim ignores errors in output."""
        state = create_initial_state("/tmp/test")
        # Even with errors in output, success=True means pass
        state["sim_output"] = StageOutput(
            success=True,
            errors=["Some non-critical error"],
            warnings=["Timing warning"],
        )

        result = _route_sim(state)

        assert result == "synth"
