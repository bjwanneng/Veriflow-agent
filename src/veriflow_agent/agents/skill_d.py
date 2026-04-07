"""Skill D Agent - Stage 3.5: Quality Gatekeeper.

Performs static analysis AND LLM-based quality pre-check on generated RTL code.
Low-quality code is caught here — before expensive iverilog/Yosys runs — and
routed directly to the debugger for correction.

Quality scoring:
  - Static analysis: module structure, naming, file organization (no LLM cost)
  - LLM pre-check: code style, hardware anti-patterns, synthesizability (cheap LLM call)
  - Combined score < quality_threshold → FAIL → debugger

The actual iverilog lint check is handled by LintAgent as a separate graph node.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from veriflow_agent.agents.base import AgentResult, BaseAgent


# Default quality threshold (0.0 - 1.0). Below this, code goes to debugger.
DEFAULT_QUALITY_THRESHOLD = 0.5


class SkillDAgent(BaseAgent):
    """Quality Gatekeeper Agent for RTL code.

    Input: workspace/rtl/*.v (from coder stage)
    Output: workspace/docs/quality_report.json

    Two-phase analysis:
    1. Static analysis (free): module structure, naming conventions, file sizes
    2. LLM pre-check (cheap): hardware anti-patterns, style, synthesizability

    If combined score < quality_threshold, the agent returns success=False,
    triggering the debugger via the graph's conditional edge.
    """

    def __init__(self, quality_threshold: float = DEFAULT_QUALITY_THRESHOLD):
        super().__init__(
            name="skill_d",
            prompt_file="stage35_skill_d.md",
            required_inputs=["workspace/rtl/*.v"],
            output_artifacts=["workspace/docs/quality_report.json"],
            max_retries=1,
            llm_backend="claude_cli",
        )
        self.quality_threshold = quality_threshold

    def execute(self, context: dict[str, Any]) -> AgentResult:
        """Execute quality gatekeeper analysis on RTL code.

        Args:
            context: Dictionary containing:
                - project_dir: Path to project root

        Returns:
            AgentResult with quality report. success=False if quality
            is below threshold (triggers debugger).
        """
        project_dir = Path(context.get("project_dir", "."))
        rtl_dir = project_dir / "workspace" / "rtl"
        report_path = project_dir / "workspace" / "docs" / "quality_report.json"

        if not rtl_dir.exists():
            return AgentResult(
                success=False,
                stage=self.name,
                errors=[f"RTL directory not found: {rtl_dir}"],
            )

        rtl_files = list(rtl_dir.glob("*.v"))
        if not rtl_files:
            return AgentResult(
                success=False,
                stage=self.name,
                errors=["No RTL files found in workspace/rtl/"],
            )

        # Phase 1: Static analysis (no LLM cost)
        static_result = self._run_static_analysis(rtl_files)
        static_score = self._compute_static_score(static_result)

        # Phase 2: LLM pre-check (cheap call)
        llm_score, llm_issues = self._run_llm_precheck(rtl_files, context)

        # Combined score: weighted average
        combined_score = static_score * 0.4 + llm_score * 0.6

        # Build report
        report = self._generate_report(
            static_result, static_score, llm_score, llm_issues,
            combined_score, rtl_files,
        )

        # Save report
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

        # Quality gate decision
        passed = combined_score >= self.quality_threshold
        errors = []
        if not passed:
            issues_summary = "; ".join(llm_issues[:5]) if llm_issues else "Quality below threshold"
            errors = [
                f"Quality score {combined_score:.2f} below threshold {self.quality_threshold}",
                f"Issues: {issues_summary}",
            ]

        return AgentResult(
            success=passed,
            stage=self.name,
            artifacts=[str(report_path)],
            metrics={
                "quality_score": round(combined_score, 3),
                "static_score": round(static_score, 3),
                "llm_score": round(llm_score, 3),
                "rtl_files_analyzed": len(rtl_files),
                "lines_of_code": static_result.get("total_lines", 0),
                "total_modules": static_result.get("total_modules", 0),
                "quality_threshold": self.quality_threshold,
            },
            errors=errors,
            metadata={"report": report},
        )

    # ── Phase 1: Static Analysis ──────────────────────────────────────

    def _run_static_analysis(self, rtl_files: list[Path]) -> dict[str, Any]:
        """Run automated static analysis on RTL files."""
        total_lines = 0
        total_modules = 0
        modules_info = []
        issues = []

        for rtl_file in rtl_files:
            content = rtl_file.read_text(encoding="utf-8")
            lines = content.split("\n")
            total_lines += len(lines)

            # Check file size
            if len(lines) > 500:
                issues.append(f"{rtl_file.name}: file too large ({len(lines)} lines > 500)")

            # Check for tabs vs spaces
            tab_lines = sum(1 for line in lines if "\t" in line)
            if tab_lines > len(lines) * 0.5 and len(lines) > 10:
                issues.append(f"{rtl_file.name}: mixed tabs/spaces")

            module_pattern = r"module\s+(\w+)\s*\((.*?)\);"
            for match in re.finditer(module_pattern, content, re.DOTALL):
                module_name = match.group(1)
                ports_str = match.group(2)
                total_modules += 1

                ports = []
                for port in ports_str.split(","):
                    port = port.strip()
                    if port:
                        ports.append(port)

                # Check module naming
                if not re.match(r'^[a-z][a-z0-9_]*$', module_name):
                    issues.append(f"{rtl_file.name}: module '{module_name}' naming convention violation")

                modules_info.append({
                    "name": module_name,
                    "file": rtl_file.name,
                    "ports_count": len(ports),
                    "ports": ports[:10],
                })

        return {
            "total_lines": total_lines,
            "total_modules": total_modules,
            "modules": modules_info,
            "files_analyzed": len(rtl_files),
            "issues": issues,
        }

    def _compute_static_score(self, analysis: dict[str, Any]) -> float:
        """Compute a 0-1 quality score from static analysis.

        Scoring:
        - Base: 0.8
        - Issue penalty: -0.1 per issue (min 0.0)
        - Module present bonus: +0.1 if modules found
        """
        score = 0.8
        issues = analysis.get("issues", [])
        score -= len(issues) * 0.1
        if analysis.get("total_modules", 0) > 0:
            score += 0.1
        return max(0.0, min(1.0, score))

    # ── Phase 2: LLM Pre-check ────────────────────────────────────────

    def _run_llm_precheck(
        self,
        rtl_files: list[Path],
        context: dict[str, Any],
    ) -> tuple[float, list[str]]:
        """Run LLM-based quality pre-check on RTL code.

        Uses a cheap LLM call to detect:
        - Hardware anti-patterns (latches, comb loops, uninitialized regs)
        - Synthesizability issues
        - Code style violations

        Returns:
            Tuple of (score 0-1, list of issue descriptions).
        """
        # Concatenate all RTL files for analysis (truncated to save tokens)
        rtl_content = ""
        for f in rtl_files[:5]:  # Max 5 files
            content = f.read_text(encoding="utf-8")
            rtl_content += f"\n// --- File: {f.name} ---\n"
            rtl_content += content[:2000]  # Truncate per file
        rtl_content = rtl_content[:8000]  # Total cap

        if not rtl_content.strip():
            return 0.5, ["No RTL content to analyze"]

        prompt = (
            "You are an RTL quality reviewer. Analyze the following Verilog code "
            "for hardware design issues. Check for:\n"
            "1. Latch inference (incomplete if/else or case)\n"
            "2. Combinational loops\n"
            "3. Uninitialized registers\n"
            "4. Multi-driven signals\n"
            "5. Missing resets\n"
            "6. Non-synthesizable constructs\n"
            "7. Signal width mismatches\n\n"
            f"RTL Code:\n```\n{rtl_content}\n```\n\n"
            "Respond in this EXACT format:\n"
            "SCORE: <0-100>\n"
            "ISSUES:\n"
            "- <issue1>\n"
            "- <issue2>\n"
        )

        try:
            llm_output = self.call_llm(
                context,
                prompt_override=prompt,
            )
            return self._parse_llm_score(llm_output)
        except Exception as e:
            # If LLM fails, use static-only score
            return 0.5, [f"LLM pre-check unavailable: {e}"]

    def _parse_llm_score(self, llm_output: str) -> tuple[float, list[str]]:
        """Parse the LLM output for score and issues."""
        score = 0.5  # default
        issues = []

        # Extract score
        score_match = re.search(r'SCORE:\s*(\d+)', llm_output)
        if score_match:
            raw_score = int(score_match.group(1))
            score = raw_score / 100.0

        # Extract issues
        issues_match = re.search(r'ISSUES:\s*\n((?:-\s+.*\n?)+)', llm_output)
        if issues_match:
            issues_text = issues_match.group(1)
            issues = [
                line.strip().lstrip('- ').strip()
                for line in issues_text.split('\n')
                if line.strip().startswith('-')
            ]

        return score, issues

    # ── Report Generation ─────────────────────────────────────────────

    def _generate_report(
        self,
        static_result: dict[str, Any],
        static_score: float,
        llm_score: float,
        llm_issues: list[str],
        combined_score: float,
        rtl_files: list[Path],
    ) -> dict[str, Any]:
        """Generate the quality report structure."""
        return {
            "version": "2.0.0",
            "stage": "3.5",
            "stage_name": "Quality Gatekeeper (Skill D)",
            "quality_score": round(combined_score, 3),
            "quality_threshold": self.quality_threshold,
            "passed": combined_score >= self.quality_threshold,
            "scores": {
                "static": round(static_score, 3),
                "llm_precheck": round(llm_score, 3),
            },
            "static_analysis": {
                "rtl_files_analyzed": len(rtl_files),
                "total_lines": static_result.get("total_lines", 0),
                "total_modules": static_result.get("total_modules", 0),
                "issues": static_result.get("issues", []),
            },
            "llm_issues": llm_issues,
            "modules": static_result.get("modules", []),
        }
