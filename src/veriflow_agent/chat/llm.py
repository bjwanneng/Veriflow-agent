"""Lightweight LLM caller for conversational chat mode.

Supports three backends:
- claude_cli: Claude CLI subprocess (requires node in PATH)
- anthropic: Anthropic Python SDK (requires ANTHROPIC_API_KEY)
- openai: OpenAI-compatible API (requires API key + base URL)

Used by the chat handler for general conversation before/after pipeline runs.
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("veriflow")

# Security: --dangerously-skip-permissions is required for automated pipeline.
# Set VERIFLOW_SKIP_PERMISSIONS=false to disable in production.
_SKIP_PERMS = os.environ.get("VERIFLOW_SKIP_PERMISSIONS", "true").lower() in ("true", "1", "yes")

# ── Windows PATH fix ────────────────────────────────────────────────────

_WINDOWS_EXTRA_PATHS = [
    str(Path.home() / "AppData" / "Roaming" / "npm"),
    r"C:\Program Files\nodejs",
]


def _get_enriched_env() -> dict[str, str]:
    """Return os.environ with Windows-specific PATH additions."""
    env = dict(os.environ)
    extra = ";".join(_WINDOWS_EXTRA_PATHS)
    env["PATH"] = env.get("PATH", "") + ";" + extra
    return env


def _find_claude_cli() -> str | None:
    """Find claude CLI, with Windows-aware PATH enrichment."""
    import shutil

    env = _get_enriched_env()
    for name in ["claude.cmd", "claude.bat", "claude.exe", "claude"]:
        found = shutil.which(name, path=env.get("PATH"))
        if found:
            return found

    # Fallback: check known paths directly
    for p_str in _WINDOWS_EXTRA_PATHS:
        for ext in [".cmd", ".bat", ".exe", ""]:
            candidate = Path(p_str) / f"claude{ext}"
            if candidate.exists():
                return str(candidate)
    return None


# ── Configuration ───────────────────────────────────────────────────────


@dataclass
class LLMConfig:
    """LLM backend configuration. Persisted in Gradio session state."""

    backend: str = "openai"  # "openai"  (all routes use OpenAI-compatible format)
    api_key: str = ""
    base_url: str = ""
    model: str = ""

    def get_effective_model(self) -> str:
        """Return the model name, falling back to defaults."""
        if self.model:
            return self.model
        return os.environ.get("OPENAI_MODEL", os.environ.get("VERIFLOW_MODEL", "gpt-4o"))


# ── System prompt ───────────────────────────────────────────────────────

CHAT_SYSTEM_PROMPT = """\
You are VeriFlow-Agent, an expert RTL design assistant. You help engineers \
design digital circuits by analyzing requirements, generating Verilog code, \
and running verification.

Your capabilities:
- Architecture analysis and micro-architecture design
- Timing model creation
- Synthesizable Verilog RTL code generation
- Lint checking (Icarus Verilog)
- Functional simulation
- Logic synthesis (Yosys)

Conversation guidelines:
- When the user describes a specific circuit they want designed, tell them you'll \
start the RTL design pipeline. Use phrases like "I'll start the design pipeline" \
so the system can detect it.
- For general questions about digital design, Verilog, FPGA, or EDA tools, answer directly.
- Be concise and technical. Use code examples when helpful.
- If the user's request is ambiguous, ask clarifying questions before starting the pipeline.
"""


# ── LLM callers ─────────────────────────────────────────────────────────


def call_llm(
    messages: list[dict[str, str]],
    config: LLMConfig,
    system_prompt: str = CHAT_SYSTEM_PROMPT,
) -> str:
    """Call the configured LLM backend and return the response text.

    Args:
        messages: List of {"role": "user"|"assistant", "content": "..."} dicts.
        config: LLM configuration.
        system_prompt: System prompt to prepend.

    Returns:
        LLM response text.

    Raises:
        RuntimeError: If the LLM call fails.
    """
    if config.backend == "claude_cli":
        raise RuntimeError("claude_cli backend is disabled. Use 'openai'.")
    else:
        # All backends (openai, anthropic alias, etc.) use OpenAI-compatible format
        return _call_openai(messages, config, system_prompt)


def call_llm_stream(
    messages: list[dict[str, str]],
    config: LLMConfig,
    system_prompt: str = CHAT_SYSTEM_PROMPT,
) -> Generator[str, None, None]:
    """Stream LLM response, yielding text chunks.

    Falls back to non-streaming + chunked yield for backends that
    don't support native streaming (claude_cli).
    """
    if config.backend == "claude_cli":
        raise RuntimeError("claude_cli backend is disabled. Use 'openai'.")
    else:
        # All backends use OpenAI-compatible streaming format
        yield from _stream_openai(messages, config, system_prompt)


# ── Backend implementations ─────────────────────────────────────────────


def _call_claude_cli(
    messages: list[dict[str, str]],
    system_prompt: str,
) -> str:
    """Call LLM via Claude CLI subprocess."""
    claude_exe = _find_claude_cli()
    if not claude_exe:
        raise RuntimeError(
            "Claude CLI not found. Install Claude Code or configure an API-based backend."
        )

    # Build the prompt from conversation history
    parts = []
    if system_prompt:
        parts.append(f"<system>\n{system_prompt}\n</system>")
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "user":
            parts.append(f"Human: {content}")
        else:
            parts.append(f"Assistant: {content}")
    parts.append("Assistant:")

    prompt = "\n\n".join(parts)

    # On Windows, use cmd /c for .cmd files
    cmd = claude_exe
    shell = False
    if claude_exe.endswith(".cmd") or claude_exe.endswith(".bat"):
        cmd = "cmd"
        shell = False

    try:
        if cmd == "cmd":
            args = ["cmd", "/c", claude_exe, "--print"]
            if _SKIP_PERMS:
                args.append("--dangerously-skip-permissions")
            result = subprocess.run(
                args,
                input=prompt.encode("utf-8"),
                capture_output=True,
                timeout=600,
                env=_get_enriched_env(),
            )
        else:
            args = [claude_exe, "--print"]
            if _SKIP_PERMS:
                args.append("--dangerously-skip-permissions")
            result = subprocess.run(
                args,
                input=prompt.encode("utf-8"),
                capture_output=True,
                timeout=600,
                env=_get_enriched_env(),
            )

        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")

        if result.returncode != 0:
            raise RuntimeError(f"Claude CLI failed (rc={result.returncode}): {stderr[:300]}")

        return stdout.strip()

    except subprocess.TimeoutExpired:
        raise RuntimeError("Claude CLI timed out after 10 minutes")


def _make_openai_client(config: LLMConfig):
    """Build an OpenAI client from config + env vars."""
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("openai package not installed. Run: pip install openai")

    api_key = (
        config.api_key
        or os.environ.get("OPENAI_API_KEY", "")
    )
    if not api_key:
        raise RuntimeError(
            "API key not set. Set OPENAI_API_KEY or configure it in Settings."
        )
    base_url = config.base_url or os.environ.get("OPENAI_BASE_URL") or None
    return OpenAI(api_key=api_key, base_url=base_url)


def _call_openai(
    messages: list[dict[str, str]],
    config: LLMConfig,
    system_prompt: str,
) -> str:
    """Call LLM via OpenAI-compatible API (non-streaming)."""
    client = _make_openai_client(config)

    api_messages = [{"role": "system", "content": system_prompt}]
    api_messages.extend({"role": m["role"], "content": m["content"]} for m in messages)

    response = client.chat.completions.create(
        model=config.get_effective_model(),
        messages=api_messages,
        max_tokens=4096,
    )
    return response.choices[0].message.content or ""


def _stream_openai(
    messages: list[dict[str, str]],
    config: LLMConfig,
    system_prompt: str,
) -> Generator[str, None, None]:
    """Stream via OpenAI-compatible API."""
    client = _make_openai_client(config)

    api_messages = [{"role": "system", "content": system_prompt}]
    api_messages.extend({"role": m["role"], "content": m["content"]} for m in messages)

    stream = client.chat.completions.create(
        model=config.get_effective_model(),
        messages=api_messages,
        max_tokens=4096,
        stream=True,
    )
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
