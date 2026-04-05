"""Shared EDA tool discovery and environment utilities.

Extracted from the original veriflow_ctl.py to provide consistent tool
discovery and environment setup across all EDA tool wrappers.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
from pathlib import Path
from typing import Optional


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
