"""Shared EDA tool discovery and environment utilities.

Extracted from the original veriflow_ctl.py to provide consistent tool
discovery, environment setup, and version detection across all EDA tool wrappers.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger("veriflow")


def find_eda_tool(tool_name: str) -> Optional[str]:
    """Locate an EDA tool executable.

    Search order:
    1. gui_config.json env override (~/.veriflow/gui_config.json)
    2. oss-cad-suite default location
    3. System PATH via shutil.which

    Args:
        tool_name: Base name of the tool (e.g. "iverilog", "vvp", "yosys").

    Returns:
        Absolute path to the executable, or None if not found.
    """
    # 1. Check gui_config.json for explicit path
    config_file = Path.home() / ".veriflow" / "gui_config.json"
    if config_file.exists():
        try:
            config = json.loads(config_file.read_text(encoding="utf-8"))
            env_config = config.get("env", {})
            explicit_path = env_config.get(f"{tool_name}_path", "")
            if explicit_path and Path(explicit_path).exists():
                return explicit_path
        except (json.JSONDecodeError, OSError):
            pass

    # 2. Check oss-cad-suite default locations
    oss_candidates = _get_oss_cad_bin_dirs()
    for bin_dir in oss_candidates:
        for suffix in _get_exec_suffixes():
            candidate = bin_dir / f"{tool_name}{suffix}"
            if candidate.exists():
                return str(candidate)

    # 3. Search system PATH
    for suffix in _get_exec_suffixes():
        found = shutil.which(f"{tool_name}{suffix}")
        if found:
            return found

    return None


def get_eda_env() -> dict[str, str]:
    """Build environment dict for running EDA tools.

    On Windows with oss-cad-suite, sets up PATH and required env vars.
    On other platforms, returns a copy of the current environment.

    Returns:
        Environment dictionary suitable for subprocess.run(env=...).
    """
    env = os.environ.copy()

    oss_bin = _find_oss_cad_suite()
    if oss_bin is None:
        return env

    oss_root = oss_bin.parent
    env["PATH"] = str(oss_bin) + os.pathsep + env.get("PATH", "")

    # oss-cad-suite specific env vars (Windows primarily)
    if platform.system() == "Windows":
        env["SSL_CERT_FILE"] = str(oss_root / "ssl" / "cert.pem")
        oss_python = oss_root / "python3"
        if oss_python.exists():
            env["PYTHONHOME"] = str(oss_python)
            env["PYTHONEXECUTABLE"] = str(oss_python / "python3.exe")

    return env


def _get_exec_suffixes() -> list[str]:
    """Return executable suffixes for the current platform."""
    if platform.system() == "Windows":
        return [".exe", ""]
    return [""]


def _get_oss_cad_bin_dirs() -> list[Path]:
    """Return candidate oss-cad-suite bin directories."""
    candidates: list[Path] = []
    home = Path.home()

    if platform.system() == "Windows":
        candidates.append(Path(r"C:\oss-cad-suite\bin"))
        candidates.append(home / "oss-cad-suite" / "bin")
    elif platform.system() == "Darwin":
        candidates.append(Path("/opt/oss-cad-suite/bin"))
        candidates.append(home / "oss-cad-suite" / "bin")
    else:  # Linux
        candidates.append(Path("/opt/oss-cad-suite/bin"))
        candidates.append(home / "oss-cad-suite" / "bin")

    return candidates


def _find_oss_cad_suite() -> Optional[Path]:
    """Find the oss-cad-suite root directory (containing bin/).

    Returns:
        Path to the oss-cad-suite root, or None if not found.
    """
    for bin_dir in _get_oss_cad_bin_dirs():
        if bin_dir.exists():
            return bin_dir.parent
    return None


# ── Version detection ───────────────────────────────────────────────────


# Known version flags and parse patterns for each tool
_TOOL_VERSION_CONFIG: dict[str, dict[str, str]] = {
    "iverilog": {
        "flag": "-V",
        "pattern": r"Icarus Verilog version\s+(\S+)",
    },
    "vvp": {
        "flag": "-V",
        "pattern": r"Icarus Verilog runtime version\s+(\S+)",
    },
    "yosys": {
        "flag": "--version",
        "pattern": r"Yosys\s+(\S+)",
    },
}

# Minimum recommended versions (None = no minimum)
_MIN_VERSIONS: dict[str, str] = {
    "iverilog": "10.0",
    "yosys": "0.9",
}


def get_tool_version(tool_name: str) -> Optional[str]:
    """Detect the version of an EDA tool.

    Args:
        tool_name: Tool executable name (e.g. "iverilog", "yosys", "vvp").

    Returns:
        Version string (e.g. "11.0", "0.23"), or None if detection fails.
    """
    tool_path = find_eda_tool(tool_name)
    if not tool_path:
        return None

    config = _TOOL_VERSION_CONFIG.get(tool_name)
    if not config:
        # Generic fallback: try --version
        return _try_generic_version(tool_path)

    flag = config["flag"]
    pattern = config["pattern"]

    try:
        result = subprocess.run(
            [tool_path, flag],
            capture_output=True,
            text=True,
            timeout=10,
            env=get_eda_env(),
        )
        output = (result.stdout or "") + (result.stderr or "")
        match = re.search(pattern, output)
        if match:
            return match.group(1)
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.debug("Version detection failed for %s: %s", tool_name, e)

    return None


def _try_generic_version(tool_path: str) -> Optional[str]:
    """Fallback version detection using --version flag."""
    for flag in ["--version", "-V", "-v"]:
        try:
            result = subprocess.run(
                [tool_path, flag],
                capture_output=True,
                text=True,
                timeout=10,
                env=get_eda_env(),
            )
            output = (result.stdout or "") + (result.stderr or "")
            # Try to find a version-like pattern (x.y.z or x.y)
            match = re.search(r'(\d+\.\d+(?:\.\d+)?)', output)
            if match:
                return match.group(1)
        except (subprocess.TimeoutExpired, OSError):
            continue
    return None


def get_all_tool_versions() -> dict[str, Optional[str]]:
    """Detect versions of all known EDA tools.

    Returns:
        Dict mapping tool name to version string (or None if not found).
    """
    versions = {}
    for tool_name in _TOOL_VERSION_CONFIG:
        versions[tool_name] = get_tool_version(tool_name)
    return versions


def check_version_compatibility(tool_name: str) -> tuple[bool, str]:
    """Check if a tool's version meets minimum requirements.

    Args:
        tool_name: Tool executable name.

    Returns:
        Tuple of (is_compatible, message).
        is_compatible is True if version is sufficient or no minimum set.
        Message describes the result or issue.
    """
    min_version = _MIN_VERSIONS.get(tool_name)
    if not min_version:
        return True, ""

    version = get_tool_version(tool_name)
    if not version:
        return True, f"Version unknown for {tool_name} (no check applied)"

    if _compare_versions(version, min_version) >= 0:
        return True, f"{tool_name} {version} >= {min_version}"
    else:
        return False, f"{tool_name} {version} < minimum {min_version}"


def _compare_versions(v1: str, v2: str) -> int:
    """Compare two version strings. Returns -1, 0, or 1.

    Handles formats like "11.0", "0.23", "10.3.1".
    """
    def parse(v: str) -> list[int]:
        parts = []
        for p in v.split("."):
            try:
                parts.append(int(p))
            except ValueError:
                break
        return parts

    a = parse(v1)
    b = parse(v2)
    # Pad shorter list with zeros
    max_len = max(len(a), len(b))
    a.extend([0] * (max_len - len(a)))
    b.extend([0] * (max_len - len(b)))

    for x, y in zip(a, b):
        if x < y:
            return -1
        if x > y:
            return 1
    return 0
