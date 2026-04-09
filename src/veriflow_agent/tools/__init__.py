"""Tool layer (ACI - Agent-Computer Interface) for VeriFlow-Agent.

This package provides Python wrappers for EDA tools, transforming shell scripts
into type-safe, testable Python classes that can be used by Agents.
"""

from veriflow_agent.tools.base import BaseTool, ToolError, ToolResult
from veriflow_agent.tools.constraint_gen import generate_constraints, read_constraint_file
from veriflow_agent.tools.lint import IverilogTool, LintResult
from veriflow_agent.tools.simulate import SimResult, VvpTool
from veriflow_agent.tools.synth import SynthResult, YosysTool

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
    "generate_constraints",
    "read_constraint_file",
]
