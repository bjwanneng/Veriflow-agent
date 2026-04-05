"""Tool layer (ACI - Agent-Computer Interface) for VeriFlow-Agent.

This package provides Python wrappers for EDA tools, transforming shell scripts
into type-safe, testable Python classes that can be used by Agents.
"""

from veriflow_agent.tools.base import BaseTool, ToolResult, ToolError
from veriflow_agent.tools.lint import IverilogTool, LintResult
from veriflow_agent.tools.simulate import VvpTool, SimResult
from veriflow_agent.tools.synth import YosysTool, SynthResult

__all__ = [
    "BaseTool",
    "ToolResult",
    "ToolError",
    "IverilogTool",
    "LintResult",
    "VvpTool",
    "SimResult",
    "YosysTool",
    "SynthResult",
]