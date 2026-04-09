"""Tests for Synth check routing.

Verifies that the synth conditional edge routes correctly based on
synthesis results, retry counts, and token budget.
"""


from langgraph.graph import END

from veriflow_agent.graph.graph import _route_synth
from veriflow_agent.graph.state import (
    StageOutput,
    create_initial_state,
)


class TestSynthRouting:
    """Tests for Synth routing decisions."""

    def test_synth_pass_routes_to_end(self):
        """Test that passing synth routes to END (pipeline complete)."""
        state = create_initial_state("/tmp/test")
        state["synth_output"] = StageOutput(success=True)

        result = _route_synth(state)

        assert result == END

    def test_synth_pass_with_metrics_routes_to_end(self):
        """Test passing synth with metrics routes to END."""
        state = create_initial_state("/tmp/test")
        state["synth_output"] = StageOutput(
            success=True,
            artifacts=["workspace/docs/synth_report.json"],
            metrics={
                "num_cells": 150,
                "num_wires": 300,
                "target_met": True,
            },
        )

        result = _route_synth(state)

        assert result == END

    def test_synth_pass_pipeline_complete(self):
        """Test that passing synth means pipeline is complete."""
        state = create_initial_state("/tmp/test")
        state["synth_output"] = StageOutput(success=True)
        state["stages_completed"] = [
            "architect", "microarch", "timing", "coder", "skill_d", "lint", "sim"
        ]

        result = _route_synth(state)

        # Pipeline ends on successful synth
        assert result == END

    def test_synth_fail_first_retry_routes_to_debugger(self):
        """Test synth failure on first retry routes to debugger."""
        state = create_initial_state("/tmp/test")
        state["synth_output"] = StageOutput(
            success=False,
            errors=["Area exceeds target by 200 cells"],
        )
        state["retry_count"] = {"lint": 0, "sim": 0, "synth": 0}
        state["token_usage"] = 100000
        state["token_budget"] = 1000000

        result = _route_synth(state)

        assert result == "debugger"
        # Note: feedback_source is now set by node_synth, not the routing function

    def test_synth_fail_timing_violation_routes_to_debugger(self):
        """Test synth timing violation routes to debugger."""
        state = create_initial_state("/tmp/test")
        state["synth_output"] = StageOutput(
            success=False,
            errors=["slack is negative: -0.5ns", "setup violation at clk"],
        )
        state["retry_count"] = {"lint": 1, "sim": 1, "synth": 0}
        state["token_usage"] = 120000
        state["token_budget"] = 1000000

        result = _route_synth(state)

        assert result == "debugger"

    def test_synth_fail_second_retry_routes_to_debugger(self):
        """Test synth failure on second retry still routes to debugger."""
        state = create_initial_state("/tmp/test")
        state["synth_output"] = StageOutput(
            success=False,
            errors=["Resource limit exceeded"],
        )
        state["retry_count"] = {"lint": 0, "sim": 1, "synth": 2}
        state["token_usage"] = 150000
        state["token_budget"] = 1000000

        result = _route_synth(state)

        assert result == "debugger"

    def test_synth_fail_max_retries_exceeded_ends(self):
        """Test synth failure after max retries ends pipeline."""
        state = create_initial_state("/tmp/test")
        state["synth_output"] = StageOutput(
            success=False,
            errors=["Unable to meet timing constraints"],
        )
        state["retry_count"] = {"lint": 1, "sim": 2, "synth": 3}  # MAX_RETRIES = 3
        state["token_usage"] = 200000
        state["token_budget"] = 1000000

        result = _route_synth(state)

        assert result == END

    def test_synth_budget_exceeded_ends(self):
        """Test synth with exceeded token budget ends pipeline."""
        state = create_initial_state("/tmp/test", token_budget=100000)
        state["synth_output"] = StageOutput(
            success=False,
            errors=["Synthesis error"],
        )
        state["retry_count"] = {"lint": 0, "sim": 0, "synth": 0}
        state["token_usage"] = 120000  # Exceeds budget

        result = _route_synth(state)

        assert result == END

    def test_synth_no_output_defaults_to_debugger(self):
        """Test missing synth output routes to debugger."""
        state = create_initial_state("/tmp/test")
        # synth_output is None
        state["retry_count"] = {"lint": 0, "sim": 0, "synth": 0}
        state["token_usage"] = 0
        state["token_budget"] = 1000000

        result = _route_synth(state)

        assert result == "debugger"
        # Note: feedback_source is now set by node_synth, not the routing function

    def test_synth_fail_sets_feedback_source(self):
        """Test that synth failure sets feedback source."""
        state = create_initial_state("/tmp/test")
        state["synth_output"] = StageOutput(
            success=False,
            errors=["Area exceeds limit"],
        )
        state["retry_count"] = {"lint": 0, "sim": 0, "synth": 0}
        state["token_usage"] = 50000
        state["token_budget"] = 1000000

        _route_synth(state)

        # Note: feedback_source is now set by node_synth, not the routing function
        assert state["feedback_source"] == ""

    def test_synth_with_timing_model_error(self):
        """Test synth with timing model errors routes correctly."""
        state = create_initial_state("/tmp/test")
        state["synth_output"] = StageOutput(
            success=False,
            errors=["timing violation: setup check failed"],
        )
        state["retry_count"] = {"lint": 0, "sim": 0, "synth": 1}
        state["token_usage"] = 100000
        state["token_budget"] = 1000000

        result = _route_synth(state)

        assert result == "debugger"
        # Note: feedback_source is now set by node_synth, not the routing function
