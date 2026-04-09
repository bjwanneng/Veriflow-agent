"""Tests for Debugger multi-level rollback routing.

Verifies that the debugger routes to the correct target stage based on
error category and feedback source.
"""


from veriflow_agent.graph.graph import _route_debugger
from veriflow_agent.graph.state import (
    ErrorCategory,
    categorize_error,
    get_rollback_target,
)


class TestErrorCategorization:
    """Tests for error categorization."""

    def test_syntax_error_detection(self):
        """Test detection of syntax errors."""
        errors = ["rtl/top.v:10: syntax error", "unexpected token 'end'"]
        category = categorize_error(errors)
        assert category == ErrorCategory.SYNTAX

    def test_undeclared_identifier_syntax(self):
        """Test undeclared identifier classified as syntax."""
        errors = ["error: undeclared identifier 'foo'"]
        category = categorize_error(errors)
        assert category == ErrorCategory.SYNTAX

    def test_logic_error_mismatch(self):
        """Test output mismatch classified as logic error."""
        errors = ["expected 0x42, got 0x00", "mismatch at cycle 100"]
        category = categorize_error(errors)
        assert category == ErrorCategory.LOGIC

    def test_logic_error_assertion(self):
        """Test assertion violation classified as logic error."""
        errors = ["Assertion violation in testbench", "test failed"]
        category = categorize_error(errors)
        assert category == ErrorCategory.LOGIC

    def test_timing_error_violation(self):
        """Test timing violation detection."""
        errors = ["timing violation: setup check failed"]
        category = categorize_error(errors)
        assert category == ErrorCategory.TIMING

    def test_timing_negative_slack(self):
        """Test negative slack classified as timing."""
        errors = ["slack is negative: -0.5ns"]
        category = categorize_error(errors)
        assert category == ErrorCategory.TIMING

    def test_resource_area_exceeded(self):
        """Test area exceeded classified as resource."""
        errors = ["area exceeds target by 200 cells", "cell count over limit"]
        category = categorize_error(errors)
        assert category == ErrorCategory.RESOURCE

    def test_resource_lut_exceeds(self):
        """Test LUT count exceeded classified as resource."""
        errors = ["LUT count exceeds target"]
        category = categorize_error(errors)
        assert category == ErrorCategory.RESOURCE

    def test_unknown_error(self):
        """Test unclassifiable errors return UNKNOWN."""
        errors = ["something weird happened", "internal error"]
        category = categorize_error(errors)
        assert category == ErrorCategory.UNKNOWN

    def test_empty_errors(self):
        """Test empty error list returns UNKNOWN."""
        category = categorize_error([])
        assert category == ErrorCategory.UNKNOWN


class TestGetRollbackTarget:
    """Tests for rollback target selection."""

    # Syntax errors -> coder
    def test_syntax_always_to_coder(self):
        """Test SYNTAX errors always rollback to coder."""
        assert get_rollback_target(ErrorCategory.SYNTAX, "lint") == "coder"
        assert get_rollback_target(ErrorCategory.SYNTAX, "sim") == "coder"
        assert get_rollback_target(ErrorCategory.SYNTAX, "synth") == "coder"

    # Logic errors -> microarch (from sim) / coder (from lint/synth)
    def test_logic_from_sim_to_microarch(self):
        """Test LOGIC from sim -> microarch."""
        target = get_rollback_target(ErrorCategory.LOGIC, "sim")
        assert target == "microarch"

    def test_logic_from_lint_to_coder(self):
        """Test LOGIC from lint -> coder."""
        target = get_rollback_target(ErrorCategory.LOGIC, "lint")
        assert target == "coder"

    def test_logic_from_synth_to_coder(self):
        """Test LOGIC from synth -> coder."""
        target = get_rollback_target(ErrorCategory.LOGIC, "synth")
        assert target == "coder"

    # Timing errors -> timing (from synth) / coder (from lint/sim)
    def test_timing_from_synth_to_timing(self):
        """Test TIMING from synth -> timing."""
        target = get_rollback_target(ErrorCategory.TIMING, "synth")
        assert target == "timing"

    def test_timing_from_lint_to_coder(self):
        """Test TIMING from lint -> coder."""
        target = get_rollback_target(ErrorCategory.TIMING, "lint")
        assert target == "coder"

    def test_timing_from_sim_to_coder(self):
        """Test TIMING from sim -> coder."""
        target = get_rollback_target(ErrorCategory.TIMING, "sim")
        assert target == "coder"

    # Resource errors -> timing (from synth) / coder (from lint/sim)
    def test_resource_from_synth_to_timing(self):
        """Test RESOURCE from synth -> timing."""
        target = get_rollback_target(ErrorCategory.RESOURCE, "synth")
        assert target == "timing"

    def test_resource_from_lint_to_coder(self):
        """Test RESOURCE from lint -> coder."""
        target = get_rollback_target(ErrorCategory.RESOURCE, "lint")
        assert target == "coder"

    # Unknown -> lint (conservative)
    def test_unknown_to_lint(self):
        """Test UNKNOWN errors rollback to lint."""
        assert get_rollback_target(ErrorCategory.UNKNOWN, "lint") == "lint"
        assert get_rollback_target(ErrorCategory.UNKNOWN, "sim") == "lint"
        assert get_rollback_target(ErrorCategory.UNKNOWN, "synth") == "lint"

    # SkillD always -> coder
    def test_skill_d_always_to_coder(self):
        """Test skill_d failures always rollback to coder."""
        assert get_rollback_target(ErrorCategory.UNKNOWN, "skill_d") == "coder"
        assert get_rollback_target(ErrorCategory.SYNTAX, "skill_d") == "coder"
        assert get_rollback_target(ErrorCategory.LOGIC, "skill_d") == "coder"


class TestDebuggerRouting:
    """Tests for debugger routing function."""

    def test_route_debugger_to_coder(self):
        """Test debugger routes to coder target."""
        state = {
            "project_dir": "/tmp/test",
            "target_rollback_stage": "coder",
        }
        result = _route_debugger(state)
        assert result == "coder"

    def test_route_debugger_to_microarch(self):
        """Test debugger routes to microarch target."""
        state = {
            "project_dir": "/tmp/test",
            "target_rollback_stage": "microarch",
        }
        result = _route_debugger(state)
        assert result == "microarch"

    def test_route_debugger_to_timing(self):
        """Test debugger routes to timing target."""
        state = {
            "project_dir": "/tmp/test",
            "target_rollback_stage": "timing",
        }
        result = _route_debugger(state)
        assert result == "timing"

    def test_route_debugger_to_lint(self):
        """Test debugger routes to lint target."""
        state = {
            "project_dir": "/tmp/test",
            "target_rollback_stage": "lint",
        }
        result = _route_debugger(state)
        assert result == "lint"

    def test_route_debugger_default_to_lint(self):
        """Test debugger defaults to lint when no target set."""
        state = {
            "project_dir": "/tmp/test",
            # target_rollback_stage not set
        }
        result = _route_debugger(state)
        assert result == "lint"
