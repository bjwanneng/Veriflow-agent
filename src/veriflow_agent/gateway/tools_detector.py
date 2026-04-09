"""EDA tool detection for VeriFlow-Agent.

Probes the system for EDA tools used in the RTL pipeline:
  - iverilog / vvp  → Lint and Simulation stages
  - yosys           → Synthesis stage
  - nextpnr         → FPGA P&R (optional)
  - openroad        → ASIC P&R (optional)
  - verilator       → Alternative simulator (optional)
  - claude / claude.cmd → Claude CLI (LLM backend)

Each tool entry reports: found, path, version, status (ok/warn/missing).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class ToolInfo:
    """Result of probing a single tool."""
    name: str
    display: str
    path: str | None        # Full resolved path or None
    version: str | None     # Extracted version string or None
    status: str             # "ok" | "warn" | "missing"
    note: str               # Human-readable note
    required: bool          # Whether pipeline stages depend on this tool

    def to_dict(self) -> dict:
        return asdict(self)


# ── Tool definitions ──────────────────────────────────────────────────────

_TOOLS: dict[str, dict] = {
    "iverilog": {
        "display": "iVerilog",
        "candidates": ["iverilog"],
        "version_cmd": ["iverilog", "-V"],
        "version_pattern": r"Icarus Verilog version\s+([\d.]+)",
        "required": True,
        "note_missing": "Required for Lint and Simulation stages. Install via: apt install iverilog / brew install icarus-verilog",
    },
    "vvp": {
        "display": "vvp (iverilog runner)",
        "candidates": ["vvp"],
        "version_cmd": None,      # No standalone version flag; bundled with iverilog
        "version_pattern": None,
        "required": True,
        "note_missing": "Bundled with iverilog. Install iverilog first.",
    },
    "yosys": {
        "display": "Yosys",
        "candidates": ["yosys"],
        "version_cmd": ["yosys", "--version"],
        "version_pattern": r"Yosys\s+([\d.]+)",
        "required": True,
        "note_missing": "Required for Synthesis stage. Install via: apt install yosys / brew install yosys",
    },
    "nextpnr": {
        "display": "nextpnr",
        "candidates": ["nextpnr-ice40", "nextpnr-ecp5", "nextpnr"],
        "version_cmd": ["nextpnr-ice40", "--version"],
        "version_pattern": r"nextpnr-\w+\s+([\d.]+)",
        "required": False,
        "note_missing": "Optional FPGA P&R tool.",
    },
    "openroad": {
        "display": "OpenROAD",
        "candidates": ["openroad"],
        "version_cmd": ["openroad", "--version"],
        "version_pattern": r"([\d.]+)",
        "required": False,
        "note_missing": "Optional ASIC P&R tool.",
    },
    "verilator": {
        "display": "Verilator",
        "candidates": ["verilator"],
        "version_cmd": ["verilator", "--version"],
        "version_pattern": r"Verilator\s+([\d.]+)",
        "required": False,
        "min_version": "4.0",
        "note_missing": "Optional high-speed simulator.",
    },
}

_CLAUDE_CANDIDATES = [
    "claude", "claude.cmd", "claude.bat", "claude.exe",
]
_CLAUDE_HOME_PATHS = [
    Path.home() / ".claude" / "local" / "claude",
    Path.home() / ".claude" / "local" / "claude.exe",
    Path.home() / "AppData" / "Roaming" / "npm" / "claude.cmd",
    Path.home() / "AppData" / "Roaming" / "npm" / "claude",
    Path.home() / "AppData" / "Local" / "Programs" / "claude" / "claude.exe",
    Path("/usr/local/bin/claude"),
    Path("/opt/homebrew/bin/claude"),
]


# ── Detection helpers ─────────────────────────────────────────────────────

def _find_exe(candidates: list[str], extra_paths: list[Path] | None = None) -> str | None:
    """Find first matching executable in PATH or extra_paths."""
    for name in candidates:
        found = shutil.which(name)
        if found:
            return found
    if extra_paths:
        for p in extra_paths:
            if p.exists() and p.is_file():
                return str(p)
    return None


def _run_version(cmd: list[str]) -> str | None:
    """Run a command and return combined stdout+stderr, or None on failure."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=5,
            text=True,
            errors="replace",
        )
        output = (result.stdout + result.stderr).strip()
        return output if output else None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _extract_version(output: str, pattern: str) -> str | None:
    """Extract version string from tool output using regex pattern."""
    if not output or not pattern:
        return None
    match = re.search(pattern, output)
    return match.group(1) if match else None


def _version_ge(v: str, min_v: str) -> bool:
    """Return True if version v >= min_v (simple dot-split comparison)."""
    def parts(s: str) -> list[int]:
        return [int(x) for x in re.split(r"[.\-]", s) if x.isdigit()]
    try:
        return parts(v) >= parts(min_v)
    except (ValueError, IndexError):
        return True  # If we can't compare, assume OK


# ── Main detection function ───────────────────────────────────────────────

def detect_tools(tool_paths_override: dict[str, str] = None) -> dict[str, dict]:
    """Probe all EDA tools and Claude CLI.

    Args:
        tool_paths_override: Dict mapping tool_id to absolute path

    Returns:
        Dict mapping tool_id → ToolInfo.to_dict()
    """
    results: dict[str, dict] = {}
    overrides = tool_paths_override or {}

    # ── EDA tools ─────────────────────────────────────────────────────
    for tool_id, spec in _TOOLS.items():
        path = overrides.get(tool_id)
        if path and Path(path).exists():
            # Use override if valid
            pass
        else:
            path = _find_exe(spec["candidates"])

        version: str | None = None
        status: str
        note: str

        if path:
            # Try to get version
            version_cmd = spec.get("version_cmd")
            if version_cmd:
                output = _run_version(version_cmd)
                pattern = spec.get("version_pattern")
                if output and pattern:
                    version = _extract_version(output, pattern)

            # Version check
            min_version = spec.get("min_version")
            if min_version and version and not _version_ge(version, min_version):
                status = "warn"
                note = f"Found v{version}, but v{min_version}+ is recommended"
            else:
                status = "ok"
                note = f"v{version}" if version else "Found"
        else:
            status = "missing"
            note = spec["note_missing"]

        results[tool_id] = ToolInfo(
            name=tool_id,
            display=spec["display"],
            path=path,
            version=version,
            status=status,
            note=note,
            required=spec["required"],
        ).to_dict()

    # ── Claude CLI ────────────────────────────────────────────────────
    claude_path = overrides.get("claude_cli")
    if claude_path and Path(claude_path).exists():
        pass
    else:
        claude_path = _find_exe(_CLAUDE_CANDIDATES, _CLAUDE_HOME_PATHS)

    claude_version: str | None = None
    claude_status: str
    claude_note: str

    if claude_path:
        output = _run_version([claude_path, "--version"])
        if output:
            # Claude CLI typically outputs: "Claude Code X.Y.Z"
            m = re.search(r"Claude\s+(?:Code\s+)?([\d.]+)", output, re.IGNORECASE)
            claude_version = m.group(1) if m else output.split("\n")[0][:40]
        claude_status = "ok"
        claude_note = f"v{claude_version}" if claude_version else "Found"
    else:
        claude_status = "missing"
        claude_note = "Claude CLI not found. Install from: https://claude.ai/code"

    results["claude_cli"] = ToolInfo(
        name="claude_cli",
        display="Claude CLI",
        path=claude_path,
        version=claude_version,
        status=claude_status,
        note=claude_note,
        required=False,  # Only required if llm_backend == "claude_cli"
    ).to_dict()

    return results


async def test_claude_cli(claude_path: str) -> dict:
    """Run a simple test invocation of Claude CLI to verify it's working.

    Returns:
        dict with keys: success (bool), message (str), duration_ms (float)
    """
    import asyncio
    import time

    if not claude_path:
        return {"success": False, "message": "Claude CLI not found", "duration_ms": 0}

    t0 = time.perf_counter()
    try:
        proc = await asyncio.create_subprocess_exec(
            claude_path, "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            output = (stdout + stderr).decode("utf-8", errors="replace").strip()
            if proc.returncode == 0 or output:
                return {
                    "success": True,
                    "message": f"CLI responded: {output[:100]}",
                    "duration_ms": round(elapsed_ms, 1),
                }
            return {
                "success": False,
                "message": f"CLI exited with code {proc.returncode}",
                "duration_ms": round(elapsed_ms, 1),
            }
        except asyncio.TimeoutError:
            proc.kill()
            return {"success": False, "message": "CLI test timed out (10s)", "duration_ms": 10000}
    except Exception as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return {"success": False, "message": str(e), "duration_ms": round(elapsed_ms, 1)}
