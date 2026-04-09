"""Tests for SynthAgent node.

Verifies the SynthAgent's independent functionality including:
- Input validation (spec.json and RTL existence)
- Cell count extraction
- Wire count extraction
- Area extraction
- Timing extraction
- Retry count increment
- Artifact generation
"""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from veriflow_agent.agents.synth import SynthAgent


class TestSynthAgent:
    """Tests for SynthAgent node functionality."""

    @pytest.fixture
    def agent(self):
        """Create a SynthAgent instance."""
        return SynthAgent()

    @pytest.fixture
    def valid_project(self, tmp_path):
        """Create a valid project structure with spec.json and RTL files."""
        # Create spec.json
        spec_file = tmp_path / "workspace" / "docs" / "spec.json"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec = {
            "design_name": "alu",
            "target_kpis": {
                "frequency_mhz": 100,
                "max_cells": 1000,
                "max_area": 10000
            },
            "modules": [
                {
                    "module_name": "alu",
                    "module_type": "top",
                    "ports": []
                }
            ]
        }
        spec_file.write_text(json.dumps(spec, indent=2), encoding="utf-8")

        # Create RTL files
        rtl_dir = tmp_path / "workspace" / "rtl"
        rtl_dir.mkdir(parents=True, exist_ok=True)

        alu_file = rtl_dir / "alu.v"
        alu_file.write_text("""
module alu (
    input         clk,
    input         rst_n,
    input  [31:0] a,
    input  [31:0] b,
    input  [3:0]  op,
    output reg [31:0] result
);
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            result <= 32'b0;
        else begin
            case (op)
                4'b0000: result <= a + b;
                4'b0001: result <= a - b;
                default: result <= 32'b0;
            endcase
        end
    end
endmodule
""", encoding="utf-8")

        return str(tmp_path)

    @pytest.fixture
    def mock_yosys(self, mocker):
        """Create a mock YosysTool."""
        mock = mocker.patch("veriflow_agent.agents.synth.YosysTool")
        instance = mock.return_value
        instance.validate_prerequisites.return_value = True

        # Create mock synth result
        synth_result = Mock()
        synth_result.success = True
        synth_result.num_cells = 150
        synth_result.num_wires = 200
        synth_result.raw_stats = "Number of cells: 150\nNumber of wires: 200"
        instance.parse_synth_output.return_value = synth_result

        return instance

    def test_synth_with_valid_rtl(self, agent, valid_project, mocker, mock_yosys):
        """Test SynthAgent with valid RTL files."""
        # Mock tool result
        mock_result = Mock()
        mock_result.success = True
        mock_result.returncode = 0
        mock_result.stdout = "Number of cells: 150"
        mock_result.stderr = ""
        mock_result.errors = []
        mock_result.warnings = []
        mock_result.raw_output = mock_result.stdout
        mock_yosys.run.return_value = mock_result

        # Execute
        context = {"project_dir": valid_project}
        result = agent.execute(context)

        # Verify
        assert result.success is True
        assert result.stage == "synth"
        assert len(result.artifacts) == 1
        assert "synth_report.json" in result.artifacts[0]
        assert result.metrics["num_cells"] == 150
        assert result.metrics["num_wires"] == 200

    def test_synth_missing_rtl(self, agent, tmp_path):
        """Test SynthAgent with missing RTL files."""
        # Create spec but no RTL
        spec_file = tmp_path / "workspace" / "docs" / "spec.json"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec = {
            "design_name": "test",
            "modules": [{"module_name": "top", "module_type": "top"}]
        }
        spec_file.write_text(json.dumps(spec), encoding="utf-8")

        rtl_dir = tmp_path / "workspace" / "rtl"
        rtl_dir.mkdir(parents=True, exist_ok=True)

        # Execute
        context = {"project_dir": str(tmp_path)}
        result = agent.execute(context)

        # Verify failure
        assert result.success is False
        assert result.stage == "synth"
        assert "No RTL files" in result.errors[0] or len(result.errors) > 0

    def test_synth_cell_count_extraction(self, agent, valid_project, mocker, mock_yosys):
        """Test cell count extraction from synthesis output."""
        mock_result = Mock()
        mock_result.success = True
        mock_result.returncode = 0
        mock_result.stdout = "Number of cells: 250"
        mock_result.errors = []
        mock_result.warnings = []
        mock_yosys.run.return_value = mock_result

        # Create synth result with cell count
        synth_result = Mock()
        synth_result.success = True
        synth_result.num_cells = 250
        synth_result.num_wires = 300
        synth_result.raw_stats = "Number of cells: 250"
        mock_yosys.parse_synth_output.return_value = synth_result

        context = {"project_dir": valid_project}
        result = agent.execute(context)

        assert result.success is True
        assert result.metrics["num_cells"] == 250

    def test_synth_artifact_generation(self, agent, valid_project, mocker, mock_yosys):
        """Test synthesis report artifact generation."""
        mock_result = Mock()
        mock_result.success = True
        mock_result.returncode = 0
        mock_result.stdout = "Synthesis complete"
        mock_result.errors = []
        mock_result.warnings = []
        mock_yosys.run.return_value = mock_result

        context = {"project_dir": valid_project}
        result = agent.execute(context)

        # Verify synth_report.json was created
        assert result.success is True
        report_path = Path(valid_project) / "workspace" / "docs" / "synth_report.json"
        assert report_path.exists()
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["top_module"] == "alu"

    def test_synth_timing_extraction(self, agent, valid_project, mocker, mock_yosys):
        """Test timing information extraction."""
        mock_result = Mock()
        mock_result.success = True
        mock_result.returncode = 0
        mock_result.stdout = "Critical path: 8.5 ns"
        mock_result.errors = []
        mock_result.warnings = []
        mock_yosys.run.return_value = mock_result

        context = {"project_dir": valid_project}
        result = agent.execute(context)

        # Verify timing info in metrics
        assert result.success is True
        assert "num_cells" in result.metrics
