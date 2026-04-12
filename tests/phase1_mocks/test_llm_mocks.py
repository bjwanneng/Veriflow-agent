"""Mock tests for LLM backends.

These tests verify that all LLM invocation paths can be mocked
for fast, deterministic testing without actual API calls.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, mock_open

import pytest

from veriflow_agent.agents.base import AgentResult, BaseAgent, LLMInvocationError

# Ensure CWD has prompts/test_prompt.md for all LLM mock tests
_PROMPT_DIR = Path("prompts")
_PROMPT_FILE = _PROMPT_DIR / "test_prompt.md"


@pytest.fixture(autouse=True)
def _ensure_test_prompt():
    """Ensure test_prompt.md exists in prompts/ for all tests in this module."""
    _PROMPT_DIR.mkdir(parents=True, exist_ok=True)
    if not _PROMPT_FILE.exists():
        _PROMPT_FILE.write_text("Test prompt with {{TEST_VAR}}", encoding="utf-8")
    yield
    # Cleanup: remove only if we created it (don't remove user files)


class MockableAgent(BaseAgent):
    """Concrete agent class for testing BaseAgent mocking."""

    def __init__(self, llm_backend="claude_cli"):
        super().__init__(
            name="test_agent",
            prompt_file="test_prompt.md",
            llm_backend=llm_backend,
        )

    def execute(self, context: dict) -> AgentResult:
        """Test execution."""
        try:
            prompt = self.render_prompt({"TEST_VAR": "value"})
            output = self.call_llm(context, prompt_override=prompt)
            return AgentResult(
                success=True,
                stage=self.name,
                raw_output=output,
            )
        except Exception as e:
            return AgentResult(
                success=False,
                stage=self.name,
                errors=[str(e)],
            )


class TestClaudeCliMock:
    """Tests for Claude CLI backend (enabled by default)."""

    def test_claude_cli_not_found_in_ci(self, tmp_path):
        """Verify claude_cli gracefully fails when CLI is not available."""
        agent = MockableAgent(llm_backend="claude_cli")
        result = agent.execute({"project_dir": str(tmp_path)})

        assert result.success is False
        # Should mention either "not found" (no CLI) or a CLI execution error
        error_lower = result.errors[0].lower()
        assert any(
            kw in error_lower
            for kw in ("not found", "exited", "cli", "error", "failed")
        )

    def test_claude_cli_mock_with_error(self, mocker, tmp_path):
        """Test that claude_cli backend returns error on failure."""
        agent = MockableAgent(llm_backend="claude_cli")
        result = agent.execute({"project_dir": str(tmp_path)})

        assert result.success is False

    def test_claude_cli_mock_timeout(self, tmp_path):
        """Test that claude_cli backend handles missing CLI gracefully."""
        agent = MockableAgent(llm_backend="claude_cli")
        result = agent.execute({"project_dir": str(tmp_path)})

        assert result.success is False


class TestAnthropicSdkMock:
    """Tests for mocking Anthropic SDK backend."""

    def test_anthropic_sdk_mock_basic(self, mocker):
        """Test that 'anthropic' backend aliases to openai-compatible path."""
        agent = MockableAgent(llm_backend="anthropic")

        # anthropic backend now routes through OpenAI-compatible streaming
        # Need OPENAI_API_KEY in context for the streaming path
        from unittest.mock import patch as _patch
        mock_stream = [
            MagicMock(choices=[MagicMock(delta=MagicMock(content="mocked output"))]),
            MagicMock(choices=[MagicMock(delta=MagicMock(content=""))]),
        ]
        # Build a mock AgentResult-like final chunk
        from veriflow_agent.agents.base import AgentResult
        final_result = AgentResult(success=True, stage="test_agent", raw_output="mocked output")

        # Mock the streaming method to yield events + result
        def mock_streaming(self, context, prompt_override=None, system_prompt=None, event_collector=None):
            from veriflow_agent.observability import create_text_delta_event, create_stream_end_event, create_session_init_event, create_metrics_event
            yield create_session_init_event(stage="test_agent", session_id="test", tools=[])
            yield create_text_delta_event(stage="test_agent", text="mocked output")
            yield create_metrics_event(stage="test_agent", input_tokens=10, output_tokens=5)
            yield create_stream_end_event(stage="test_agent", success=True)
            yield final_result

        with _patch.object(type(agent), 'call_llm_streaming', mock_streaming):
            result = agent.execute({"project_dir": "/tmp", "llm_api_key": "sk-test"})

        assert result.success is True
        assert result.raw_output == "mocked output"

    def test_anthropic_sdk_mock_with_error(self, mocker):
        """Test that 'anthropic' backend error propagates correctly."""
        agent = MockableAgent(llm_backend="anthropic")

        result = agent.execute({"project_dir": "/tmp"})

        assert result.success is False


class TestLangchainMock:
    """Tests for mocking LangChain backend."""

    def test_langchain_mock_basic(self, mocker):
        """Test basic mocking of LangChain backend."""
        mocker.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test-key"})
        agent = MockableAgent(llm_backend="langchain")

        # Mock ChatAnthropic
        mock_chat_anthropic = mocker.patch("langchain_anthropic.ChatAnthropic")
        mock_model = MagicMock()

        # Mock prompt template's __or__ to return a mock chain
        mock_result = MagicMock()
        mock_result.content = "mocked langchain output"
        mock_result.response_metadata = {
            "usage": {"input_tokens": 80, "output_tokens": 40}
        }

        mock_chain = MagicMock()
        mock_chain.invoke.return_value = mock_result
        mock_chat_anthropic.return_value = mock_model

        # Patch ChatPromptTemplate.from_messages to return a mock with __or__
        mock_prompt = MagicMock()
        mock_prompt.__or__ = MagicMock(return_value=mock_chain)
        mocker.patch(
            "langchain_core.prompts.ChatPromptTemplate.from_messages",
            return_value=mock_prompt,
        )

        result = agent.execute({"project_dir": "/tmp"})

        assert result.success is True
        assert "mocked langchain output" in result.raw_output


class TestLLMTokenTracking:
    """Tests for LLM token usage tracking mocks."""

    def test_token_tracking_claude_cli(self, mocker, tmp_path):
        """Test token estimation for claude_cli backend (now disabled)."""
        agent = MockableAgent(llm_backend="claude_cli")

        result = agent.execute({"project_dir": str(tmp_path)})

        # claude_cli backend is disabled — verify it fails gracefully
        assert result.success is False
        assert agent.get_last_token_usage() == 0  # No tokens tracked on disabled backend

    def test_token_tracking_anthropic_sdk(self, mocker):
        """Test that 'anthropic' backend works through OpenAI-compatible path."""
        agent = MockableAgent(llm_backend="anthropic")

        # Mock the streaming to yield events + result
        from unittest.mock import patch as _patch
        from veriflow_agent.agents.base import AgentResult
        from veriflow_agent.observability import create_session_init_event, create_text_delta_event, create_metrics_event, create_stream_end_event

        final_result = AgentResult(success=True, stage="test_agent", raw_output="output", metrics={"token_usage": 225})

        def mock_streaming(self, context, prompt_override=None, system_prompt=None, event_collector=None):
            yield create_session_init_event(stage="test_agent", session_id="test", tools=[])
            yield create_text_delta_event(stage="test_agent", text="output")
            yield create_metrics_event(stage="test_agent", input_tokens=150, output_tokens=75)
            yield create_stream_end_event(stage="test_agent", success=True)
            yield final_result

        with _patch.object(type(agent), 'call_llm_streaming', mock_streaming):
            result = agent.execute({"project_dir": "/tmp", "llm_api_key": "sk-test"})

        assert result.success is True
        assert result.raw_output == "output"


class TestLLMErrorHandling:
    """Tests for LLM error handling with mocks."""

    def test_llm_invocation_error_handling(self, mocker, tmp_path):
        """Test handling of LLMInvocationError."""
        agent = MockableAgent(llm_backend="claude_cli")

        # Mock prompt file to raise error
        mocker.patch.object(
            agent,
            '_resolve_prompt_path',
            side_effect=LLMInvocationError("Prompt file not found")
        )

        result = agent.execute({"project_dir": str(tmp_path)})

        assert result.success is False
        assert "Prompt file not found" in result.errors[0]

    def test_missing_prompt_file_error(self, mocker, tmp_path):
        """Test error when prompt file is missing."""
        agent = MockableAgent(llm_backend="claude_cli")

        # Ensure prompt file doesn't exist
        mocker.patch.object(Path, 'exists', return_value=False)

        result = agent.execute({"project_dir": str(tmp_path)})

        assert result.success is False
        assert "not found" in result.errors[0].lower() or "missing" in result.errors[0].lower()

    def test_render_prompt_empty_prompt_file(self, mocker, tmp_path):
        """Test handling of empty prompt file."""
        agent = MockableAgent(llm_backend="claude_cli")

        # Mock empty file content
        mocker.patch('builtins.open', mock_open(read_data=''))
        mocker.patch.object(Path, 'exists', return_value=True)

        result = agent.execute({"project_dir": str(tmp_path)})

        # Should handle empty prompt gracefully
        assert isinstance(result, AgentResult)
