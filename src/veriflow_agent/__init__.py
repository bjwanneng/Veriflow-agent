"""VeriFlow-Agent: Agent-based RTL design pipeline using LangGraph.

This package provides an agentic architecture for RTL design automation,
replacing the traditional state machine approach with a flexible,
graph-based agent system powered by LangGraph.
"""

__version__ = "0.1.0"
__author__ = "VeriFlow Team"
__email__ = "team@veriflow.ai"

# Import main types for convenience
from veriflow_agent.graph.state import VeriFlowState, StageOutput
from veriflow_agent.agents.base import BaseAgent, AgentResult

__all__ = [
    "__version__",
    "VeriFlowState",
    "StageOutput",
    "BaseAgent",
    "AgentResult",
]