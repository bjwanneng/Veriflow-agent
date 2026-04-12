"""Tests for Supervisor routing logic.

Verifies that:
- _route_supervisor routes based on supervisor decision action
- All EDA check routing functions (_route_lint, _route_sim, _route_synth,
  _route_coder, _route_skill_d, _route_debugger) route to "supervisor" on failure
- Supervisor call count cap is enforced across routing functions
"""

from langgraph.graph import END

from veriflow_agent.graph.graph import (
    _route_coder,
    _route_debugger,
    _route_lint,
    _route_sim,
    _route_skill_d,
    _route_supervisor,
    _route_synth,
)
from veriflow_agent.graph.state import (
    MAX_SUPERVISOR_CALLS,
    StageOutput,
    create_initial_state,
)


class TestRouteSupervisor:
    """Tests for _route_supervisor routing decisions."""

    def test_retry_stage_returns_target(self):
        """Test that action='retry_stage' routes to target_stage."""
        state = create_initial_state("/tmp/test")
        state["supervisor_decision"] = {
            "action": "retry_stage",
            "target_stage": "debugger",
            "hint": "Fix syntax error",
            "root_cause": "syntax",
            "severity": "high",
            "modules": [],
        }

        result = _route_supervisor(state)

        assert result == "debugger"

    def test_retry_stage_returns_coder_target(self):
        """Test that action='retry_stage' can target coder."""
        state = create_initial_state("/tmp/test")
        state["supervisor_decision"] = {
            "action": "retry_stage",
            "target_stage": "coder",
            "hint": "Rewrite ALU module",
            "root_cause": "logic",
            "severity": "high",
            "modules": ["alu.v"],
        }

        result = _route_supervisor(state)

        assert result == "coder"

    def test_escalate_stage_returns_target(self):
        """Test that action='escalate_stage' routes to target_stage."""
        state = create_initial_state("/tmp/test")
        state["supervisor_decision"] = {
            "action": "escalate_stage",
            "target_stage": "microarch",
            "hint": "Fundamental design flaw",
            "root_cause": "logic",
            "severity": "high",
            "modules": [],
        }

        result = _route_supervisor(state)

        assert result == "microarch"

    def test_continue_returns_next_normal_stage_from_lint(self):
        """Test that action='continue' with feedback_source='lint' routes to sim."""
        state = create_initial_state("/tmp/test")
        state["supervisor_decision"] = {
            "action": "continue",
            "target_stage": "",
            "hint": "",
            "root_cause": "transient error",
            "severity": "low",
            "modules": [],
        }
        state["feedback_source"] = "lint"

        result = _route_supervisor(state)

        assert result == "sim"

    def test_continue_returns_next_normal_stage_from_sim(self):
        """Test that action='continue' with feedback_source='sim' routes to synth."""
        state = create_initial_state("/tmp/test")
        state["supervisor_decision"] = {
            "action": "continue",
            "target_stage": "",
            "hint": "",
            "root_cause": "",
            "severity": "low",
            "modules": [],
        }
        state["feedback_source"] = "sim"

        result = _route_supervisor(state)

        assert result == "synth"

    def test_continue_returns_end_from_synth(self):
        """Test that action='continue' after synth routes to END."""
        state = create_initial_state("/tmp/test")
        state["supervisor_decision"] = {
            "action": "continue",
            "target_stage": "",
            "hint": "",
            "root_cause": "",
            "severity": "low",
            "modules": [],
        }
        state["feedback_source"] = "synth"

        result = _route_supervisor(state)

        assert result == END

    def test_abort_returns_end(self):
        """Test that action='abort' routes to END."""
        state = create_initial_state("/tmp/test")
        state["supervisor_decision"] = {
            "action": "abort",
            "target_stage": "",
            "hint": "",
            "root_cause": "Unrecoverable error",
            "severity": "high",
            "modules": [],
        }

        result = _route_supervisor(state)

        assert result == END

    def test_no_decision_defaults_to_abort(self):
        """Test that missing supervisor_decision defaults to abort (END)."""
        state = create_initial_state("/tmp/test")
        # supervisor_decision is None by default

        result = _route_supervisor(state)

        assert result == END

    def test_none_decision_defaults_to_abort(self):
        """Test that explicitly None supervisor_decision routes to END."""
        state = create_initial_state("/tmp/test")
        state["supervisor_decision"] = None

        result = _route_supervisor(state)

        assert result == END

    def test_empty_decision_defaults_to_abort(self):
        """Test that empty dict decision routes to END."""
        state = create_initial_state("/tmp/test")
        state["supervisor_decision"] = {}

        result = _route_supervisor(state)

        assert result == END

    def test_unknown_action_defaults_to_target_stage(self):
        """Test that unknown action falls through to returning target_stage."""
        state = create_initial_state("/tmp/test")
        state["supervisor_decision"] = {
            "action": "unknown_action",
            "target_stage": "debugger",
            "hint": "",
            "root_cause": "",
            "severity": "medium",
            "modules": [],
        }

        result = _route_supervisor(state)

        # Unknown action falls through the if/elif chain to the
        # default return which returns target_stage
        assert result == "debugger"


class TestRouteLintSupervisor:
    """Tests for _route_lint routing to supervisor on failure."""

    def test_lint_pass_routes_to_sim(self):
        """Test that passing lint routes to sim."""
        state = create_initial_state("/tmp/test")
        state["lint_output"] = StageOutput(success=True)

        result = _route_lint(state)

        assert result == "sim"

    def test_lint_fail_routes_to_supervisor(self):
        """Test that failing lint routes to supervisor."""
        state = create_initial_state("/tmp/test")
        state["lint_output"] = StageOutput(
            success=False,
            errors=["rtl/top.v:10: syntax error"],
        )
        state["supervisor_call_count"] = 0
        state["token_usage"] = 10000
        state["token_budget"] = 1000000

        result = _route_lint(state)

        assert result == "supervisor"

    def test_lint_fail_supervisor_cap_reached_routes_to_end(self):
        """Test that failing lint with supervisor cap reached ends pipeline."""
        state = create_initial_state("/tmp/test")
        state["lint_output"] = StageOutput(
            success=False,
            errors=["rtl/top.v:10: syntax error"],
        )
        state["supervisor_call_count"] = MAX_SUPERVISOR_CALLS
        state["token_usage"] = 10000
        state["token_budget"] = 1000000

        result = _route_lint(state)

        assert result == END

    def test_lint_fail_token_budget_exceeded_routes_to_end(self):
        """Test that failing lint with exceeded token budget ends pipeline."""
        state = create_initial_state("/tmp/test", token_budget=10000)
        state["lint_output"] = StageOutput(
            success=False,
            errors=["rtl/top.v:10: syntax error"],
        )
        state["supervisor_call_count"] = 0
        state["token_usage"] = 12000  # Exceeds budget

        result = _route_lint(state)

        assert result == END

    def test_lint_no_output_routes_to_supervisor(self):
        """Test that missing lint output (None) routes to supervisor."""
        state = create_initial_state("/tmp/test")
        # lint_output is None by default
        state["supervisor_call_count"] = 0
        state["token_usage"] = 0
        state["token_budget"] = 1000000

        result = _route_lint(state)

        assert result == "supervisor"

    def test_lint_routing_is_readonly(self):
        """Test that routing function does not mutate state."""
        state = create_initial_state("/tmp/test")
        state["lint_output"] = StageOutput(
            success=False,
            errors=["syntax error"],
        )
        state["supervisor_call_count"] = 0
        state["token_usage"] = 10000
        state["token_budget"] = 1000000

        _route_lint(state)

        # feedback_source should NOT be set by the routing function
        assert state["feedback_source"] == ""


class TestRouteSimSupervisor:
    """Tests for _route_sim routing to supervisor on failure."""

    def test_sim_pass_routes_to_synth(self):
        """Test that passing sim routes to synth."""
        state = create_initial_state("/tmp/test")
        state["sim_output"] = StageOutput(success=True)

        result = _route_sim(state)

        assert result == "synth"

    def test_sim_fail_routes_to_supervisor(self):
        """Test that failing sim routes to supervisor."""
        state = create_initial_state("/tmp/test")
        state["sim_output"] = StageOutput(
            success=False,
            errors=["simulation fail: mismatch at time 100"],
        )
        state["supervisor_call_count"] = 0
        state["token_usage"] = 10000
        state["token_budget"] = 1000000

        result = _route_sim(state)

        assert result == "supervisor"

    def test_sim_fail_supervisor_cap_reached_routes_to_end(self):
        """Test that failing sim with supervisor cap reached ends pipeline."""
        state = create_initial_state("/tmp/test")
        state["sim_output"] = StageOutput(
            success=False,
            errors=["simulation fail"],
        )
        state["supervisor_call_count"] = MAX_SUPERVISOR_CALLS
        state["token_usage"] = 10000
        state["token_budget"] = 1000000

        result = _route_sim(state)

        assert result == END

    def test_sim_fail_token_budget_exceeded_routes_to_end(self):
        """Test that failing sim with exceeded token budget ends pipeline."""
        state = create_initial_state("/tmp/test", token_budget=10000)
        state["sim_output"] = StageOutput(
            success=False,
            errors=["simulation fail"],
        )
        state["supervisor_call_count"] = 0
        state["token_usage"] = 12000

        result = _route_sim(state)

        assert result == END


class TestRouteSynthSupervisor:
    """Tests for _route_synth routing to supervisor on failure."""

    def test_synth_pass_routes_to_end(self):
        """Test that passing synth routes to END."""
        state = create_initial_state("/tmp/test")
        state["synth_output"] = StageOutput(success=True)

        result = _route_synth(state)

        assert result == END

    def test_synth_fail_routes_to_supervisor(self):
        """Test that failing synth routes to supervisor."""
        state = create_initial_state("/tmp/test")
        state["synth_output"] = StageOutput(
            success=False,
            errors=["timing violation on path clk -> out"],
        )
        state["supervisor_call_count"] = 0
        state["token_usage"] = 10000
        state["token_budget"] = 1000000

        result = _route_synth(state)

        assert result == "supervisor"

    def test_synth_fail_supervisor_cap_reached_routes_to_end(self):
        """Test that failing synth with supervisor cap reached ends pipeline."""
        state = create_initial_state("/tmp/test")
        state["synth_output"] = StageOutput(
            success=False,
            errors=["timing violation"],
        )
        state["supervisor_call_count"] = MAX_SUPERVISOR_CALLS
        state["token_usage"] = 10000
        state["token_budget"] = 1000000

        result = _route_synth(state)

        assert result == END


class TestRouteCoderSupervisor:
    """Tests for _route_coder routing to supervisor on failure."""

    def test_coder_pass_routes_to_skill_d(self):
        """Test that passing coder routes to skill_d."""
        state = create_initial_state("/tmp/test")
        state["coder_output"] = StageOutput(success=True)

        result = _route_coder(state)

        assert result == "skill_d"

    def test_coder_fail_routes_to_supervisor(self):
        """Test that failing coder routes to supervisor."""
        state = create_initial_state("/tmp/test")
        state["coder_output"] = StageOutput(
            success=False,
            errors=["Failed to generate RTL"],
        )
        state["supervisor_call_count"] = 0
        state["token_usage"] = 10000
        state["token_budget"] = 1000000

        result = _route_coder(state)

        assert result == "supervisor"

    def test_coder_fail_supervisor_cap_reached_routes_to_end(self):
        """Test that failing coder with supervisor cap reached ends pipeline."""
        state = create_initial_state("/tmp/test")
        state["coder_output"] = StageOutput(
            success=False,
            errors=["Failed to generate RTL"],
        )
        state["supervisor_call_count"] = MAX_SUPERVISOR_CALLS
        state["token_usage"] = 10000
        state["token_budget"] = 1000000

        result = _route_coder(state)

        assert result == END


class TestRouteSkillDSupervisor:
    """Tests for _route_skill_d routing to supervisor on failure."""

    def test_skill_d_pass_routes_to_tool_check(self):
        """Test that passing skill_d routes to tool_check."""
        state = create_initial_state("/tmp/test")
        state["skill_d_output"] = StageOutput(success=True)

        result = _route_skill_d(state)

        assert result == "tool_check"

    def test_skill_d_fail_routes_to_supervisor(self):
        """Test that failing skill_d routes to supervisor."""
        state = create_initial_state("/tmp/test")
        state["skill_d_output"] = StageOutput(
            success=False,
            errors=["Quality score 0.35 below threshold"],
        )
        state["supervisor_call_count"] = 0
        state["token_usage"] = 10000
        state["token_budget"] = 1000000

        result = _route_skill_d(state)

        assert result == "supervisor"

    def test_skill_d_fail_supervisor_cap_reached_routes_to_end(self):
        """Test that failing skill_d with supervisor cap reached ends pipeline."""
        state = create_initial_state("/tmp/test")
        state["skill_d_output"] = StageOutput(
            success=False,
            errors=["Quality check failed"],
        )
        state["supervisor_call_count"] = MAX_SUPERVISOR_CALLS
        state["token_usage"] = 10000
        state["token_budget"] = 1000000

        result = _route_skill_d(state)

        assert result == END


class TestRouteDebuggerSupervisor:
    """Tests for _route_debugger routing to supervisor after fix."""

    def test_debugger_routes_to_supervisor(self):
        """Test that debugger routes back to supervisor for re-evaluation."""
        state = create_initial_state("/tmp/test")
        state["supervisor_call_count"] = 0

        result = _route_debugger(state)

        assert result == "supervisor"

    def test_debugger_supervisor_cap_reached_falls_back_to_target(self):
        """Test that debugger with supervisor cap reached routes to rollback target."""
        state = create_initial_state("/tmp/test")
        state["supervisor_call_count"] = MAX_SUPERVISOR_CALLS
        state["target_rollback_stage"] = "lint"

        result = _route_debugger(state)

        assert result == "lint"

    def test_debugger_supervisor_cap_reached_default_target(self):
        """Test that debugger with cap reached defaults to 'lint' target."""
        state = create_initial_state("/tmp/test")
        state["supervisor_call_count"] = MAX_SUPERVISOR_CALLS
        # target_rollback_stage defaults to "lint" in create_initial_state

        result = _route_debugger(state)

        assert result == "lint"

    def test_debugger_supervisor_cap_reached_coder_target(self):
        """Test that debugger with cap reached routes to coder rollback target."""
        state = create_initial_state("/tmp/test")
        state["supervisor_call_count"] = MAX_SUPERVISOR_CALLS
        state["target_rollback_stage"] = "coder"

        result = _route_debugger(state)

        assert result == "coder"

    def test_debugger_near_cap_still_routes_to_supervisor(self):
        """Test that debugger at MAX-1 still routes to supervisor."""
        state = create_initial_state("/tmp/test")
        state["supervisor_call_count"] = MAX_SUPERVISOR_CALLS - 1

        result = _route_debugger(state)

        assert result == "supervisor"
