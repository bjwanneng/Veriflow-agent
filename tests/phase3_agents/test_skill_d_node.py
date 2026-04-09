"""Tests for SkillDAgent node.

Verifies the SkillDAgent's independent functionality including:
- Input validation (RTL file existence)
- Static analysis
- LLM pre-check
- Quality threshold pass/fail
- Artifact generation
"""


import pytest

from veriflow_agent.agents.skill_d import DEFAULT_QUALITY_THRESHOLD, SkillDAgent


class TestSkillDAgent:
    """Tests for SkillDAgent node functionality."""

    @pytest.fixture
    def agent(self):
        """Create a SkillDAgent instance."""
        return SkillDAgent()

    @pytest.fixture
    def valid_project(self, tmp_path):
        """Create a valid project structure with RTL files."""
        # Create RTL files
        rtl_dir = tmp_path / "workspace" / "rtl"
        rtl_dir.mkdir(parents=True, exist_ok=True)

        # Create a valid Verilog file
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

    def test_skill_d_with_valid_rtl(self, agent, valid_project, mocker):
        """Test SkillDAgent with valid RTL files."""
        # Mock LLM pre-check to return good score
        mock_llm_output = """
SCORE: 85
ISSUES:
- None
"""
        mocker.patch.object(agent, 'call_llm', return_value=mock_llm_output)

        # Execute
        context = {"project_dir": valid_project}
        result = agent.execute(context)

        # Verify
        assert result.success is True
        assert result.stage == "skill_d"
        assert len(result.artifacts) == 1
        assert "quality_report.json" in result.artifacts[0]
        assert result.metrics["quality_score"] >= DEFAULT_QUALITY_THRESHOLD
        assert "quality_threshold" in result.metrics

    def test_skill_d_missing_rtl(self, agent, tmp_path):
        """Test SkillDAgent with missing RTL files."""
        # Create project without RTL
        project_dir = str(tmp_path)
        (tmp_path / "workspace" / "rtl").mkdir(parents=True, exist_ok=True)

        # Execute
        context = {"project_dir": project_dir}
        result = agent.execute(context)

        # Verify failure
        assert result.success is False
        assert result.stage == "skill_d"
        assert "No RTL files" in result.errors[0]

    def test_skill_d_static_analysis(self, agent, valid_project, mocker):
        """Test static analysis of RTL files."""
        # Mock LLM to pass
        mocker.patch.object(agent, 'call_llm', return_value="SCORE: 90\nISSUES:\n- None")

        context = {"project_dir": valid_project}
        result = agent.execute(context)

        # Verify static analysis metrics
        assert result.success is True
        assert result.metrics["lines_of_code"] > 0
        assert result.metrics["total_modules"] > 0
        assert "static_score" in result.metrics

    def test_skill_d_llm_precheck(self, agent, valid_project, mocker):
        """Test LLM pre-check functionality."""
        mock_llm_output = """
SCORE: 75
ISSUES:
- Signal 'unused_sig' declared but not used
- Missing default case in case statement
"""
        mocker.patch.object(agent, 'call_llm', return_value=mock_llm_output)

        context = {"project_dir": valid_project}
        result = agent.execute(context)

        # Verify LLM pre-check results
        assert result.metrics["llm_score"] == 0.75
        assert result.metrics["quality_score"] < 1.0  # Combined score

    def test_skill_d_quality_threshold_pass(self, agent, valid_project, mocker):
        """Test quality threshold pass scenario."""
        # Mock LLM to return high score
        mocker.patch.object(agent, 'call_llm', return_value="SCORE: 95\nISSUES:\n- None")

        context = {"project_dir": valid_project}
        result = agent.execute(context)

        # Should pass quality gate
        assert result.success is True
        assert result.metrics["quality_score"] >= DEFAULT_QUALITY_THRESHOLD
        assert len(result.errors) == 0

    def test_skill_d_quality_threshold_fail(self, agent, valid_project, mocker):
        """Test quality threshold fail scenario."""
        # Mock LLM to return low score
        mocker.patch.object(agent, 'call_llm', return_value="SCORE: 30\nISSUES:\n- Poor coding style\n- Many warnings")

        context = {"project_dir": valid_project}
        result = agent.execute(context)

        # Should fail quality gate
        assert result.success is False
        assert result.metrics["quality_score"] < DEFAULT_QUALITY_THRESHOLD
        assert len(result.errors) > 0
        assert "Quality score" in result.errors[0]
