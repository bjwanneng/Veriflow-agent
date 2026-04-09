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
    """Tests for mocking Claude CLI backend."""

    def test_claude_cli_mock_basic(self, mocker, tmp_path):
        """Test basic mocking of Claude CLI subprocess call."""
        agent = MockableAgent(llm_backend="claude_cli")

        # Mock subprocess.run
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=b"mocked claude output",
            stderr=b"",
        )

        # Execute
        result = agent.execute({"project_dir": str(tmp_path)})

        # Verify
        assert result.success is True
        assert result.raw_output == "mocked claude output"
        mock_run.assert_called_once()

    def test_claude_cli_mock_with_error(self, mocker, tmp_path):
        """Test mocking Claude CLI with error response."""
        agent = MockableAgent(llm_backend="claude_cli")

        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout=b"",
            stderr=b"Claude CLI error: permission denied",
        )

        result = agent.execute({"project_dir": str(tmp_path)})

        assert result.success is False
        assert "Claude CLI failed" in result.errors[0]

    def test_claude_cli_mock_timeout(self, mocker, tmp_path):
        """Test mocking Claude CLI timeout."""
        import subprocess
        agent = MockableAgent(llm_backend="claude_cli")

        mock_run = mocker.patch("subprocess.run")
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["claude"], timeout=600)

        result = agent.execute({"project_dir": str(tmp_path)})

        assert result.success is False
        assert "timed out" in result.errors[0].lower()


class TestAnthropicSdkMock:
    """Tests for mocking Anthropic SDK backend."""

    def test_anthropic_sdk_mock_basic(self, mocker):
        """Test basic mocking of Anthropic SDK."""
        agent = MockableAgent(llm_backend="anthropic")

        # Mock API key
        mocker.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test-key"})

        # Mock Anthropic client
        mock_anthropic = mocker.patch("anthropic.Anthropic")
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="mocked anthropic output")]
        mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.return_value = mock_client

        result = agent.execute({"project_dir": "/tmp"})

        assert result.success is True
        assert result.raw_output == "mocked anthropic output"
        mock_client.messages.create.assert_called_once()

    def test_anthropic_sdk_mock_with_error(self, mocker):
        """Test mocking Anthropic SDK with error."""
        agent = MockableAgent(llm_backend="anthropic")

        mocker.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test-key"})

        mock_anthropic = mocker.patch("anthropic.Anthropic")
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API rate limit exceeded")
        mock_anthropic.return_value = mock_client

        result = agent.execute({"project_dir": "/tmp"})

        assert result.success is False
        assert "API rate limit exceeded" in result.errors[0]


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
        """Test token estimation for Claude CLI backend."""
        agent = MockableAgent(llm_backend="claude_cli")

        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=b"a" * 400,  # 400 chars ~ 100 tokens
            stderr=b"",
        )

        agent.execute({"project_dir": str(tmp_path)})

        # Verify token usage was tracked
        assert agent.get_last_token_usage() > 0

    def test_token_tracking_anthropic_sdk(self, mocker):
        """Test token tracking with Anthropic SDK mock."""
        mocker.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test-key"})
        agent = MockableAgent(llm_backend="anthropic")

        mock_anthropic = mocker.patch("anthropic.Anthropic")
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="output")]
        mock_response.usage = MagicMock(input_tokens=150, output_tokens=75)
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.return_value = mock_client

        agent.execute({"project_dir": "/tmp"})

        # 150 + 75 = 225 tokens
        assert agent.get_last_token_usage() == 225


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
