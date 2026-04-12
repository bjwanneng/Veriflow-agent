"""SupervisorAgent - LLM-based intelligent pipeline routing.

Analyzes pipeline failures using LLM and outputs structured routing decisions.
Falls back to mechanical categorization (regex-based) when LLM fails.

Replaces the mechanical categorize_error() / get_rollback_target() routing
with LLM-driven root cause analysis and intelligent stage targeting.
"""

from __future__ import annotations

import json as _json
import logging
import re
from pathlib import Path
from typing import Any

from veriflow_agent.agents.base import AgentResult, BaseAgent

logger = logging.getLogger("veriflow")

# Valid values for supervisor decision fields
VALID_ACTIONS = {"retry_stage", "escalate_stage", "degrade", "continue", "abort"}
VALID_TARGETS = {
    "architect", "microarch", "timing", "coder", "debugger",
    "lint", "sim", "synth", "",
}


class SupervisorAgent(BaseAgent):
    """LLM-based pipeline supervisor for intelligent failure routing.

    Input: error context from a failed pipeline stage
    Output: AgentResult with routing decision in metrics
    """

    def __init__(self):
        super().__init__(
            name="supervisor",
            prompt_file="supervisor.md",
            required_inputs=[],
            output_artifacts=[],
            max_retries=1,
            llm_backend="claude_cli",
        )

    def execute(self, context: dict[str, Any]) -> AgentResult:
        """Analyze pipeline failure and produce a routing decision.

        Args:
            context: Must contain:
                - project_dir: Path to project root
                - failing_stage: Name of the stage that failed
                Optional:
                - error_log: Error text from the failed stage
                - spec_summary: Truncated spec.json content
                - supervisor_history_json: JSON string of previous decisions
                - error_history: List of previous error messages
                - recovery_context: "initial failure" or "re-evaluation"

        Returns:
            AgentResult with metrics containing:
                action, target_stage, hint, root_cause, severity, modules
        """
        failing_stage = context.get("failing_stage", "unknown")
        error_log = context.get("error_log", "")
        error_history = context.get("error_history", [])

        # Build prompt context
        llm_context = {
            "PIPELINE_CONTEXT": self._build_pipeline_context(context),
            "SPEC_SUMMARY": context.get("spec_summary", "")[:2000],
            "FAILING_STAGE": failing_stage,
            "ERROR_SUMMARY": self._build_error_summary(error_log, error_history),
            "ENVIRONMENT_PROBE": context.get("environment_probe", "(no environment data)"),
            "SUPERVISOR_HISTORY": context.get("supervisor_history_json", "[]"),
            "RTL_VALIDATION": context.get("rtl_validation", "(no RTL validation data)"),
            "FULL_PROJECT_CONTEXT": context.get("full_project_context", "(no project files available)"),
            "DEBUGGER_FAILURE_NOTE": context.get("debugger_failure_note", ""),
        }

        try:
            prompt = self.render_prompt(llm_context)

            # Check if EventCollector is available for streaming
            event_collector = context.get("_event_collector")
            if event_collector:
                llm_output = self._consume_streaming(context, prompt, event_collector)
            else:
                llm_output = self.call_llm(context, prompt_override=prompt)

        except Exception as e:
            logger.warning("Supervisor LLM call failed: %s", e)
            return self._mechanical_fallback(error_log, failing_stage, reason=str(e))

        # Parse JSON from LLM output
        decision = self._parse_decision(llm_output)

        if decision is None:
            logger.warning(
                "Supervisor JSON parse failed. Raw output: %s", llm_output[:200]
            )
            return self._mechanical_fallback(
                error_log, failing_stage,
                reason=f"LLM 输出无法解析为 JSON，原始输出: {llm_output[:100]}",
            )

        # Validate and sanitize decision
        decision = self._validate_decision(decision)

        logger.info(
            "Supervisor decision: action=%s, target=%s, root_cause=%s",
            decision["action"],
            decision["target_stage"],
            decision["root_cause"][:100],
        )

        return AgentResult(
            success=True,
            stage=self.name,
            metrics=decision,
            raw_output=llm_output[:2000],
        )

    def _build_pipeline_context(self, context: dict[str, Any]) -> str:
        """Build pipeline context JSON for the prompt."""
        return _json.dumps({
            "failing_stage": context.get("failing_stage", ""),
            "recovery_context": context.get("recovery_context", "initial failure"),
            "error_history_count": len(context.get("error_history", [])),
        })

    def _build_error_summary(self, error_log: str, error_history: list[str]) -> str:
        """Build truncated error summary for the prompt."""
        parts = []
        if error_log:
            parts.append(f"## Current Error\n{error_log[:4000]}")
        if error_history:
            history_text = "\n---\n".join(
                entry[:1000] for entry in error_history[-3:]
            )
            parts.append(f"## Previous Errors\n{history_text}")
        return "\n\n".join(parts) if parts else "(No error details available)"

    def _parse_decision(self, llm_output: str) -> dict[str, Any] | None:
        """Parse JSON decision from LLM output.

        Tries multiple extraction strategies:
        1. JSON inside code fences
        2. Raw JSON object in text
        3. Give up and return None
        """
        if not llm_output or not llm_output.strip():
            return None

        # Strategy 1: JSON inside code fences
        fence_match = re.search(
            r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', llm_output
        )
        if fence_match:
            try:
                return _json.loads(fence_match.group(1))
            except _json.JSONDecodeError:
                pass

        # Strategy 2: Raw JSON object containing "action" key
        json_match = re.search(
            r'\{[^{}]*"action"\s*:\s*"[^"]*"[^{}]*\}', llm_output, re.DOTALL
        )
        if json_match:
            try:
                return _json.loads(json_match.group(0))
            except _json.JSONDecodeError:
                pass

        # Strategy 3: Try to parse the entire output as JSON
        try:
            result = _json.loads(llm_output.strip())
            if isinstance(result, dict) and "action" in result:
                return result
        except _json.JSONDecodeError:
            pass

        return None

    def _validate_decision(self, decision: dict[str, Any]) -> dict[str, Any]:
        """Validate and sanitize a parsed decision dict."""
        action = decision.get("action", "retry_stage")
        if action not in VALID_ACTIONS:
            logger.warning("Invalid action '%s', defaulting to 'retry_stage'", action)
            action = "retry_stage"

        target_stage = decision.get("target_stage", "debugger")
        if target_stage not in VALID_TARGETS:
            logger.warning(
                "Invalid target_stage '%s', defaulting to 'debugger'", target_stage
            )
            target_stage = "debugger"

        hint = str(decision.get("hint", ""))[:200]
        root_cause = str(decision.get("root_cause", ""))[:200]
        severity = decision.get("severity", "medium")
        if severity not in ("low", "medium", "high"):
            severity = "medium"

        modules = decision.get("modules", [])
        if not isinstance(modules, list):
            modules = []

        return {
            "action": action,
            "target_stage": target_stage,
            "modules": modules,
            "hint": hint,
            "root_cause": root_cause,
            "severity": severity,
        }

    def _mechanical_fallback(
        self, error_log: str, failing_stage: str, reason: str = ""
    ) -> AgentResult:
        """LLM not available — do not guess mechanically.

        Returns failure so that node_supervisor signals abort and the
        pipeline pauses to notify the user rather than silently routing
        based on keyword heuristics.
        """
        msg = reason or "LLM 调用失败或输出无法解析"
        logger.warning("Supervisor LLM unavailable for %s: %s", failing_stage, msg)
        return AgentResult(
            success=False,
            stage=self.name,
            errors=[f"Supervisor LLM 不可用: {msg}"],
            raw_output="",
        )
