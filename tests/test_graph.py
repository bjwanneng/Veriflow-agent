"""Tests for the VeriFlow-Agent LangGraph graph assembly.

These tests verify:
- Graph compilation
- Routing functions
- Initial state creation
- Mode-based stage selection
- Node wrapper behavior with missing inputs
"""

import pytest
from unittest.mock import patch, MagicMock

from veriflow_agent.graph.state import (
    VeriFlowState,
    StageOutput,
    create_initial_state,
    get_mode_stages,
)
from veriflow_agent.graph.graph import (
    create_veriflow_graph,
    _route_after_microarch,
    _route_after_skill_d,
    _run_stage,
    node_architect,
    node_coder,
    node_synth,
)
from veriflow_agent.agents.architect import ArchitectAgent
from veriflow_agent.agents.architect import ArchitectAgent
from langgraph.graph import END


# ── State creation ────────────────────────────────────────────────────


class TestCreateInitialState:
    def test_default_mode(self):
        state = create_initial_state("/tmp/project")
        assert state["project_dir"] == "/tmp/project"
        assert state["mode"] == "standard"
        assert state["current_stage"] == ""
        assert state["stages_completed"] == []
        assert state["stages_failed"] == []
        assert state["retry_count"]["architect"] == 0

    def test_quick_mode(self):
        state = create_initial_state("/tmp/project", mode="quick")
        assert state["mode"] == "quick"

    def test_all_stage_outputs_none(self):
        state = create_initial_state("/tmp/project")
        assert state["architect_output"] is None
        assert state["coder_output"] is None
        assert state["synth_output"] is None


class TestGetModeStages:
    def test_quick(self):
        stages = get_mode_stages("quick")
        assert "architect" in stages
        assert "timing" not in stages
        assert "debugger" not in stages

    def test_standard(self):
        stages = get_mode_stages("standard")
        assert "architect" in stages
        assert "timing" in stages
        assert "coder" in stages
        assert "debugger" in stages
        assert "synth" in stages

    def test_unknown_defaults_to_standard(self):
        stages = get_mode_stages("unknown")
        assert stages == get_mode_stages("standard")


# ── StageOutput ───────────────────────────────────────────────────────


class TestStageOutput:
    def test_to_dict(self):
        so = StageOutput(success=True, artifacts=["a.v"])
        d = so.to_dict()
        assert d["success"] is True
        assert d["artifacts"] == ["a.v"]

    def test_from_dict(self):
        so = StageOutput.from_dict({"success": False, "errors": ["e1"]})
        assert so.success is False
        assert so.errors == ["e1"]


# ── Routing functions ─────────────────────────────────────────────────


class TestRouting:
    def test_route_after_microarch_standard(self):
        state = create_initial_state("/tmp", mode="standard")
        assert _route_after_microarch(state) == "timing"

    def test_route_after_microarch_quick(self):
        state = create_initial_state("/tmp", mode="quick")
        assert _route_after_microarch(state) == "coder"

    def test_route_after_skill_d_standard(self):
        state = create_initial_state("/tmp", mode="standard")
        assert _route_after_skill_d(state) == "sim_loop"

    def test_route_after_skill_d_quick(self):
        state = create_initial_state("/tmp", mode="quick")
        assert _route_after_skill_d(state) == END


# ── Graph compilation ─────────────────────────────────────────────────


class TestGraphCompilation:
    def test_compiles_without_checkpointer(self):
        graph = create_veriflow_graph(with_checkpointer=False)
        assert graph is not None
        assert graph.name == "veriflow-pipeline"

    def test_compiles_with_checkpointer(self):
        graph = create_veriflow_graph(with_checkpointer=True)
        assert graph is not None

    def test_graph_has_all_nodes(self):
        graph = create_veriflow_graph(with_checkpointer=False)
        # Access the underlying graph nodes
        node_names = list(graph.nodes.keys())
        expected = ["__start__", "architect", "microarch", "timing",
                     "coder", "skill_d", "sim_loop", "synth"]
        for name in expected:
            assert name in node_names, f"Missing node: {name}"


# ── Node wrappers (with mock) ─────────────────────────────────────────


class TestNodeWrappers:
    def test_node_architect_missing_input(self):
        """Architect should fail gracefully without requirement.md."""
        state = create_initial_state("/nonexistent/path")
        result = node_architect(state)
        assert result["current_stage"] == "architect"
        # Should record failure since requirement.md doesn't exist
        assert "architect" in result.get("stages_failed", [])

    def test_node_coder_missing_spec(self):
        """Coder should fail gracefully without spec.json."""
        state = create_initial_state("/nonexistent/path")
        result = node_coder(state)
        assert result["current_stage"] == "coder"
        assert "coder" in result.get("stages_failed", [])

    def test_node_synth_missing_spec(self):
        """Synth should fail gracefully without spec.json."""
        state = create_initial_state("/nonexistent/path")
        result = node_synth(state)
        assert result["current_stage"] == "synth"
        assert "synth" in result.get("stages_failed", [])

    def test_run_stage_records_success(self):
        """Test _run_stage records a successful stage correctly."""
        from veriflow_agent.agents.base import AgentResult, BaseAgent

        # Create a minimal mock agent class
        class MockAgent(BaseAgent):
            def __init__(self):
                super().__init__(name="mock_stage", prompt_file="")
            def execute(self, context):
                return AgentResult(success=True, stage="mock_stage", artifacts=["out.v"])

        state = create_initial_state("/tmp")
        result = _run_stage(state, MockAgent)

        assert result["current_stage"] == "mock_stage"
        assert "mock_stage" in result["stages_completed"]
        assert result["quality_gates_passed"]["mock_stage"] is True
        assert result["mock_stage_output"].success is True
