"""Tests for token budget tracking and checking.

Verifies that token budget is correctly tracked and enforced at various
thresholds (80% warning, 100% exceeded).
"""


from veriflow_agent.graph.state import (
    DEFAULT_TOKEN_BUDGET,
    check_token_budget,
    create_initial_state,
)


class TestTokenBudgetBasic:
    """Tests for basic token budget checking."""

    def test_default_token_budget(self):
        """Test that default token budget is 1M."""
        assert DEFAULT_TOKEN_BUDGET == 1_000_000

    def test_under_80_percent_ok(self):
        """Test that under 80% usage is OK."""
        state = create_initial_state("/tmp", token_budget=1000)
        state["token_usage"] = 500

        ok, msg = check_token_budget(state)

        assert ok is True
        assert msg == ""

    def test_at_79_percent_ok_no_warning(self):
        """Test that 79% usage is OK without warning."""
        state = create_initial_state("/tmp", token_budget=1000)
        state["token_usage"] = 790

        ok, msg = check_token_budget(state)

        assert ok is True
        assert msg == ""

    def test_at_80_percent_warning(self):
        """Test that 80% usage triggers warning."""
        state = create_initial_state("/tmp", token_budget=1000)
        state["token_usage"] = 800

        ok, msg = check_token_budget(state)

        assert ok is True
        assert "warning" in msg.lower()
        assert "80%" in msg or "800" in msg

    def test_at_90_percent_warning(self):
        """Test that 90% usage triggers warning."""
        state = create_initial_state("/tmp", token_budget=1000)
        state["token_usage"] = 900

        ok, msg = check_token_budget(state)

        assert ok is True
        assert "warning" in msg.lower()

    def test_at_99_percent_warning(self):
        """Test that 99% usage triggers warning."""
        state = create_initial_state("/tmp", token_budget=1000)
        state["token_usage"] = 990

        ok, msg = check_token_budget(state)

        assert ok is True
        assert "warning" in msg.lower()

    def test_at_100_percent_exceeded(self):
        """Test that 100% usage is exceeded."""
        state = create_initial_state("/tmp", token_budget=1000)
        state["token_usage"] = 1000

        ok, msg = check_token_budget(state)

        assert ok is False
        assert "exceeded" in msg.lower()

    def test_over_100_percent_exceeded(self):
        """Test that over 100% usage is exceeded."""
        state = create_initial_state("/tmp", token_budget=1000)
        state["token_usage"] = 1200

        ok, msg = check_token_budget(state)

        assert ok is False
        assert "exceeded" in msg.lower()
        assert "1200" in msg or "120%" in msg

    def test_zero_budget_always_ok(self):
        """Test that zero budget always passes."""
        state = create_initial_state("/tmp", token_budget=0)
        state["token_usage"] = 999999

        ok, msg = check_token_budget(state)

        assert ok is True
        assert msg == ""

    def test_negative_budget_always_ok(self):
        """Test that negative budget always passes."""
        state = create_initial_state("/tmp", token_budget=-100)
        state["token_usage"] = 999999

        ok, msg = check_token_budget(state)

        assert ok is True
        assert msg == ""


class TestTokenTrackingByStage:
    """Tests for per-stage token tracking."""

    def test_token_usage_by_stage_starts_empty(self):
        """Test that token usage by stage starts empty."""
        state = create_initial_state("/tmp")

        assert state["token_usage_by_stage"] == {}
        assert state["token_usage"] == 0

    def test_token_accumulation(self):
        """Test that tokens accumulate across stages."""
        state = create_initial_state("/tmp")

        # Simulate architect using 1000 tokens
        state["token_usage"] = 1000
        state["token_usage_by_stage"]["architect"] = 1000

        # Simulate coder using 2000 tokens
        state["token_usage"] = 3000
        state["token_usage_by_stage"]["coder"] = 2000

        assert state["token_usage"] == 3000
        assert state["token_usage_by_stage"]["architect"] == 1000
        assert state["token_usage_by_stage"]["coder"] == 2000

    def test_custom_token_budget(self):
        """Test creating state with custom token budget."""
        state = create_initial_state("/tmp", token_budget=500_000)

        assert state["token_budget"] == 500_000

    def test_large_token_budget(self):
        """Test creating state with large token budget."""
        state = create_initial_state("/tmp", token_budget=10_000_000)

        assert state["token_budget"] == 10_000_000


class TestTokenBudgetInRouting:
    """Tests for token budget enforcement in routing."""

    def test_budget_check_in_skill_d_context(self):
        """Test budget check in skill_d routing context."""
        state = create_initial_state("/tmp", token_budget=10000)
        state["token_usage"] = 5000  # 50% - OK
        state["skill_d_output"] = None
        state["retry_count"] = {"lint": 0, "sim": 0, "synth": 0}

        ok, msg = check_token_budget(state)

        assert ok is True
        assert msg == ""

    def test_budget_check_blocks_when_exceeded(self):
        """Test that budget check blocks when exceeded."""
        state = create_initial_state("/tmp", token_budget=10000)
        state["token_usage"] = 15000  # 150% - Exceeded

        ok, msg = check_token_budget(state)

        assert ok is False
        assert "exceeded" in msg.lower()

    def test_budget_check_allows_when_under(self):
        """Test that budget check allows when under budget."""
        state = create_initial_state("/tmp", token_budget=10000)
        state["token_usage"] = 7500  # 75% - OK

        ok, msg = check_token_budget(state)

        assert ok is True
        assert msg == "" or "warning" in msg.lower()


class TestTokenBudgetEdgeCases:
    """Tests for token budget edge cases."""

    def test_no_usage_with_budget(self):
        """Test state with budget but no usage."""
        state = create_initial_state("/tmp", token_budget=1000)
        state["token_usage"] = 0

        ok, msg = check_token_budget(state)

        assert ok is True
        assert msg == ""

    def test_exact_80_percent_boundary(self):
        """Test exact 80% boundary."""
        state = create_initial_state("/tmp", token_budget=1000)
        state["token_usage"] = 800  # Exactly 80%

        ok, msg = check_token_budget(state)

        # At 80% should be warning
        assert ok is True
        assert "warning" in msg.lower()

    def test_just_under_80_percent(self):
        """Test just under 80% boundary."""
        state = create_initial_state("/tmp", token_budget=1000)
        state["token_usage"] = 799  # Just under 80%

        ok, msg = check_token_budget(state)

        assert ok is True
        assert msg == ""

    def test_just_over_100_percent(self):
        """Test just over 100% boundary."""
        state = create_initial_state("/tmp", token_budget=1000)
        state["token_usage"] = 1001  # Just over 100%

        ok, msg = check_token_budget(state)

        assert ok is False
        assert "exceeded" in msg.lower()

    def test_very_large_token_usage(self):
        """Test with very large token usage."""
        state = create_initial_state("/tmp", token_budget=1000)
        state["token_usage"] = 1000000  # 1000x budget

        ok, msg = check_token_budget(state)

        assert ok is False
        assert "exceeded" in msg.lower()

    def test_very_small_budget(self):
        """Test with very small budget."""
        state = create_initial_state("/tmp", token_budget=1)
        state["token_usage"] = 1

        ok, msg = check_token_budget(state)

        assert ok is False  # 100% = exceeded
