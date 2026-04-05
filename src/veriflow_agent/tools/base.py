"""Base tool classes for the VeriFlow-Agent tool layer.

This module defines the abstract base class and data structures for all EDA tool
wrappers. It provides a consistent interface for:
- Tool execution with timeout and error handling
- Result standardization
- Prerequisite validation
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class ToolStatus(Enum):
    """Status of a tool execution."""

    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    NOT_FOUND = "not_found"
    PREREQ_FAILED = "prereq_failed"


@dataclass
class ToolResult:
    """Standardized result from tool execution.

    Attributes:
        status: Execution status (success, failure, timeout, etc.)
        return_code: Process return code (if applicable)
        stdout: Standard output from the tool
        stderr: Standard error from the tool
        artifacts: Dictionary of generated file paths by category
        metrics: Performance metrics (time, memory, etc.)
        errors: List of error messages
        warnings: List of warning messages
        duration_ms: Execution time in milliseconds
    """

    status: ToolStatus
    return_code: int = 0
    stdout: str = ""
    stderr: str = ""
    artifacts: dict[str, list[str]] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    duration_ms: int = 0

    @property
    def success(self) -> bool:
        """Check if the tool execution was successful."""
        return self.status == ToolStatus.SUCCESS and len(self.errors) == 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "status": self.status.value,
            "return_code": self.return_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "artifacts": self.artifacts,
            "metrics": self.metrics,
            "errors": self.errors,
            "warnings": self.warnings,
            "duration_ms": self.duration_ms,
        }


class ToolError(Exception):
    """Base exception for tool-related errors."""

    def __init__(
        self,
        message: str,
        tool_name: str = "",
        result: ToolResult | None = None,
    ):
        super().__init__(message)
        self.tool_name = tool_name
        self.result = result


class ToolNotFoundError(ToolError):
    """Raised when the tool executable is not found."""

    pass


class PrerequisiteError(ToolError):
    """Raised when tool prerequisites are not met."""

    pass


class ExecutionError(ToolError):
    """Raised when tool execution fails."""

    pass


class BaseTool(ABC):
    """Abstract base class for all EDA tools.

    This class provides the common infrastructure for:
    - Tool discovery and validation
    - Execution with timeout and error handling
    - Result standardization

    Subclasses must implement the abstract methods to define tool-specific behavior.

    Example:
        ```python
        class MyTool(BaseTool):
            def __init__(self, config=None):
                super().__init__("my_tool", config)

            def validate_prerequisites(self) -> bool:
                # Check if tool is available
                return shutil.which("my_tool") is not None

            def run(self, input_file, **kwargs) -> ToolResult:
                # Execute the tool
                cmd = [self.executable, input_file]
                return self._execute(cmd, timeout=self.timeout)
        ```
    """

    def __init__(
        self,
        name: str,
        config: dict[str, Any] | None = None,
        executable: str | None = None,
    ):
        """Initialize the tool.

        Args:
            name: Unique identifier for this tool
            config: Configuration dictionary with tool settings
            executable: Path to tool executable (auto-detected if not provided)
        """
        self.name = name
        self.config = config or {}
        self.timeout = self.config.get("timeout", 60)
        self._executable = executable
        self._prerequisites_validated = False

    @property
    def executable(self) -> str:
        """Get the path to the tool executable.

        Returns:
            Path to executable

        Raises:
            ToolNotFoundError: If executable cannot be found
        """
        if self._executable:
            return self._executable

        # Auto-detect executable
        import shutil

        exe = shutil.which(self.name)
        if exe:
            self._executable = exe
            return exe

        raise ToolNotFoundError(
            f"Tool '{self.name}' not found in PATH. "
            f"Please install {self.name} or provide the executable path."
        )

    @abstractmethod
    def validate_prerequisites(self) -> bool:
        """Validate that all prerequisites for running this tool are met.

        This method should check:
        - Tool executable is available
        - Required environment variables are set
        - Required libraries/dependencies are present

        Returns:
            True if all prerequisites are met

        Raises:
            PrerequisiteError: If prerequisites are not met
        """
        pass

    @abstractmethod
    def run(self, **kwargs) -> ToolResult:
        """Execute the tool with the given arguments.

        This is the main entry point for tool execution. Subclasses should
        implement this method to define tool-specific execution logic.

        Args:
            **kwargs: Tool-specific arguments

        Returns:
            ToolResult with execution status and outputs

        Raises:
            ToolError: For tool-related errors
        """
        pass

    def _execute(
        self,
        command: list[str],
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
        capture_output: bool = True,
    ) -> ToolResult:
        """Execute a command with standard error handling.

        This is a helper method for subclasses to execute external commands
        with consistent error handling and result formatting.

        Args:
            command: Command and arguments as list
            cwd: Working directory for execution
            env: Environment variables
            timeout: Timeout in seconds
            capture_output: Whether to capture stdout/stderr

        Returns:
            ToolResult with execution results
        """
        import subprocess
        import time

        timeout = timeout or self.timeout
        start_time = time.time()

        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                capture_output=capture_output,
                text=True,
                timeout=timeout,
            )

            duration_ms = int((time.time() - start_time) * 1000)

            # Determine status
            if result.returncode == 0:
                status = ToolStatus.SUCCESS
                errors = []
            else:
                status = ToolStatus.FAILURE
                errors = [f"Command failed with exit code {result.returncode}"]

            return ToolResult(
                status=status,
                return_code=result.returncode,
                stdout=result.stdout if capture_output else "",
                stderr=result.stderr if capture_output else "",
                errors=errors,
                warnings=[],  # Populated by subclasses
                duration_ms=duration_ms,
            )

        except subprocess.TimeoutExpired:
            duration_ms = int((time.time() - start_time) * 1000)
            return ToolResult(
                status=ToolStatus.TIMEOUT,
                return_code=-1,
                errors=[f"Command timed out after {timeout} seconds"],
                duration_ms=duration_ms,
            )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return ToolResult(
                status=ToolStatus.FAILURE,
                return_code=-1,
                errors=[f"Execution error: {str(e)}"],
                duration_ms=duration_ms,
            )

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name='{self.name}')"


# Convenience type alias
ToolResultOrError = ToolResult | ToolError