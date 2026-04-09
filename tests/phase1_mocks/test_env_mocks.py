"""Mock tests for environment detection.

These tests verify that environment detection (tool discovery,
version checking, PATH variables) can be mocked.
"""

import os
from unittest.mock import MagicMock

from veriflow_agent.tools.eda_utils import (
    _compare_versions,
    check_version_compatibility,
    find_eda_tool,
    get_all_tool_versions,
    get_eda_env,
    get_tool_version,
)


class TestToolDiscoveryMock:
    """Tests for mocking tool discovery."""

    def test_find_eda_tool_with_shutil_mock(self, mocker):
        """Test tool discovery returns a valid path."""
        result = find_eda_tool("iverilog")
        # On this machine, iverilog is found via oss-cad-suite or PATH
        assert result is not None
        assert "iverilog" in result.lower()

    def test_find_eda_tool_not_found_mock(self, mocker):
        """Test mocking tool not found scenario."""
        mock_which = mocker.patch("shutil.which")
        mock_which.return_value = None

        result = find_eda_tool("nonexistent_tool")

        assert result is None

    def test_find_eda_tool_with_custom_path_mock(self, mocker):
        """Test tool discovery with custom PATH."""
        mocker.patch.dict(os.environ, {"PATH": "/custom/path"})
        # Tool found via oss-cad-suite or system PATH
        result = find_eda_tool("yosys")
        assert result is not None
        assert "yosys" in result.lower()


class TestEnvironmentMock:
    """Tests for mocking environment setup."""

    def test_get_eda_env_mock(self, mocker):
        """Test mocking environment variable retrieval."""
        mocker.patch(
            "veriflow_agent.tools.eda_utils._find_oss_cad_suite",
            return_value=None,
        )
        mock_environ = {
            "PATH": "/usr/bin:/opt/eda/bin",
            "HOME": "/home/user",
            "OSS_CAD_SUITE": "/opt/oss-cad-suite",
        }
        mocker.patch.dict(os.environ, mock_environ, clear=True)

        env = get_eda_env()

        assert "PATH" in env

    def test_get_eda_env_with_missing_path_mock(self, mocker):
        """Test mocking environment with missing EDA tools in PATH."""
        mocker.patch(
            "veriflow_agent.tools.eda_utils._find_oss_cad_suite",
            return_value=None,
        )
        mocker.patch.dict(os.environ, {"PATH": "/usr/bin", "HOME": "/home/user"}, clear=True)

        env = get_eda_env()

        # Should still return the environment even if EDA tools not found
        assert "PATH" in env


class TestVersionDetectionMock:
    """Tests for mocking version detection."""

    def test_get_tool_version_mock(self, mocker):
        """Test mocking tool version retrieval."""
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Icarus Verilog version 12.0 (stable)",
            stderr="",
        )

        version = get_tool_version("iverilog")

        assert version is not None
        assert "12.0" in version
        mock_run.assert_called_once()

    def test_get_tool_version_failure_mock(self, mocker):
        """Test mocking version detection failure."""
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="command not found",
        )

        version = get_tool_version("nonexistent")

        assert version is None

    def test_get_all_tool_versions_mock(self, mocker):
        """Test mocking all tool version retrieval."""
        def mock_version(tool):
            versions = {
                "iverilog": "12.0",
                "vvp": "12.0",
                "yosys": "0.29",
            }
            return versions.get(tool)

        mocker.patch(
            "veriflow_agent.tools.eda_utils.get_tool_version",
            side_effect=mock_version,
        )

        versions = get_all_tool_versions()

        assert "iverilog" in versions
        assert versions["iverilog"] == "12.0"
        assert versions["yosys"] == "0.29"


class TestVersionComparisonMock:
    """Tests for mocking version comparison."""

    def test_compare_versions_equal_mock(self):
        """Test version comparison with equal versions."""
        result = _compare_versions("1.0.0", "1.0.0")
        assert result == 0

    def test_compare_versions_greater_mock(self):
        """Test version comparison with greater version."""
        result = _compare_versions("2.0", "1.0")
        assert result == 1

        result = _compare_versions("1.1", "1.0")
        assert result == 1

    def test_compare_versions_less_mock(self):
        """Test version comparison with lesser version."""
        result = _compare_versions("1.0", "2.0")
        assert result == -1

        result = _compare_versions("0.9", "1.0")
        assert result == -1

    def test_check_version_compatibility_mock(self, mocker):
        """Test mocking version compatibility check."""
        mocker.patch(
            "veriflow_agent.tools.eda_utils.get_tool_version",
            return_value="12.0",
        )

        ok, msg = check_version_compatibility("iverilog")

        assert ok is True

    def test_check_version_incompatible_mock(self, mocker):
        """Test mocking incompatible version check."""
        mocker.patch(
            "veriflow_agent.tools.eda_utils.get_tool_version",
            return_value="9.0",  # Very old version
        )

        ok, msg = check_version_compatibility("iverilog")

        # Version 9.0 is below minimum (10.0) → not compatible
        assert ok is False
