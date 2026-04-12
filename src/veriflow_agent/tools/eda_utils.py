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

logger = logging.getLogger("veriflow")


def find_eda_tool(tool_name: str) -> str | None:
    """Locate an EDA tool executable.

    Search order:
    1. gui_config.json env override (~/.veriflow/gui_config.json)
    2. oss-cad-suite default location
    3. Platform-specific common install paths
    4. Environment variable overrides (IVERILOG_HOME, YOSYS_PATH, etc.)
    5. Windows registry (Windows only)
    6. System PATH via shutil.which

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

    # 3. Platform-specific common install paths
    platform_candidates = _get_platform_specific_dirs(tool_name)
    for bin_dir in platform_candidates:
        for suffix in _get_exec_suffixes():
            candidate = Path(bin_dir) / f"{tool_name}{suffix}"
            if candidate.exists():
                return str(candidate)

    # 4. Environment variable overrides
    env_var_map = {
        "iverilog": ["IVERILOG_HOME", "IVERILOG_PATH"],
        "yosys": ["YOSYS_PATH", "YOSYS_HOME"],
        "vvp": ["IVERILOG_HOME", "IVERILOG_PATH"],
    }
    for var_name in env_var_map.get(tool_name, []):
        env_val = os.environ.get(var_name, "")
        if env_val:
            env_path = Path(env_val)
            # Could be the bin dir or the executable itself
            if env_path.is_file() and env_path.exists():
                return str(env_path)
            for suffix in _get_exec_suffixes():
                candidate = env_path / f"{tool_name}{suffix}"
                if candidate.exists():
                    return str(candidate)
                candidate = env_path / "bin" / f"{tool_name}{suffix}"
                if candidate.exists():
                    return str(candidate)

    # 5. Windows registry lookup
    if platform.system() == "Windows":
        reg_result = _search_windows_registry(tool_name)
        if reg_result:
            return reg_result

    # 6. Search system PATH
    for suffix in _get_exec_suffixes():
        found = shutil.which(f"{tool_name}{suffix}")
        if found:
            return found

    return None


def find_eda_tool_diagnostics(tool_name: str) -> dict[str, list[str]]:
    """Return diagnostic info about where find_eda_tool searched.

    Used for error reporting when a tool is not found.

    Returns:
        Dict with keys: 'searched', 'not_found', 'env_vars'.
        Each value is a list of human-readable strings.
    """
    searched: list[str] = []
    not_found: list[str] = []

    # gui_config.json
    config_file = Path.home() / ".veriflow" / "gui_config.json"
    if config_file.exists():
        try:
            config = json.loads(config_file.read_text(encoding="utf-8"))
            env_config = config.get("env", {})
            explicit_path = env_config.get(f"{tool_name}_path", "")
            if explicit_path:
                if Path(explicit_path).exists():
                    searched.append(f"gui_config.json: {explicit_path} ✓")
                else:
                    not_found.append(f"gui_config.json: {explicit_path} (path not found)")
            else:
                not_found.append("gui_config.json: not configured")
        except Exception:
            not_found.append("gui_config.json: read error")
    else:
        not_found.append("gui_config.json: not configured")

    # oss-cad-suite
    for bin_dir in _get_oss_cad_bin_dirs():
        searched.append(str(bin_dir))

    # Platform-specific
    for bin_dir in _get_platform_specific_dirs(tool_name):
        searched.append(str(bin_dir))

    # Env vars
    env_var_map = {
        "iverilog": ["IVERILOG_HOME", "IVERILOG_PATH"],
        "yosys": ["YOSYS_PATH", "YOSYS_HOME"],
    }
    env_vars: list[str] = []
    for var_name in env_var_map.get(tool_name, []):
        val = os.environ.get(var_name, "")
        if val:
            env_vars.append(f"{var_name}={val}")
        else:
            env_vars.append(f"{var_name}: not set")

    return {"searched": searched, "not_found": not_found, "env_vars": env_vars}


def get_eda_env() -> dict[str, str]:
    """Build environment dict for running EDA tools.

    On Windows with oss-cad-suite, sets up PATH and required env vars.
    On other platforms, returns a copy of the current environment.

    Returns:
        Environment dictionary suitable for subprocess.run(env=...).
    """
    env = os.environ.copy()

    oss_root = _find_oss_cad_suite()
    if oss_root is None:
        return env

    oss_bin = oss_root / "bin"
    oss_lib = oss_root / "lib"
    # YOSYSHQ_ROOT is required for oss-cad-suite tools to find their resources
    env["YOSYSHQ_ROOT"] = str(oss_root) + os.sep
    # Both bin and lib are needed (lib contains DLLs on Windows)
    env["PATH"] = str(oss_bin) + os.pathsep + str(oss_lib) + os.pathsep + env.get("PATH", "")

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


def _get_platform_specific_dirs(tool_name: str) -> list[str]:
    """Return platform-specific directories to search for EDA tools.

    Covers common installation methods on each platform:
    - Windows: standalone installers, chocolatey, MSYS2, conda, cygwin
    - macOS: Homebrew, conda
    - Linux: /usr/local, conda
    """
    candidates: list[str] = []
    home = str(Path.home())

    if platform.system() == "Windows":
        # Standalone iverilog installer (bleyer.org)
        candidates.extend([
            r"C:\iverilog\bin",
            r"C:\iverilog",
            r"C:\Program Files\iverilog\bin",
            r"C:\Program Files (x86)\iverilog\bin",
            os.path.join(home, "iverilog", "bin"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "iverilog", "bin"),
        ])
        # Chocolatey
        candidates.append(r"C:\ProgramData\chocolatey\bin")
        # MSYS2 / MinGW
        candidates.extend([
            r"C:\msys64\usr\bin",
            r"C:\msys64\mingw64\bin",
        ])
        # Conda environments
        conda_prefix = os.environ.get("CONDA_PREFIX", "")
        if conda_prefix:
            candidates.append(os.path.join(conda_prefix, "bin"))
            candidates.append(os.path.join(conda_prefix, "Library", "bin"))
        candidates.extend([
            os.path.join(home, "miniconda3", "bin"),
            os.path.join(home, "anaconda3", "bin"),
            os.path.join(home, "miniforge3", "bin"),
        ])
        # Cygwin
        candidates.append(r"C:\cygwin64\bin")
        # Generic EDA tools path
        eda_path = os.environ.get("EDA_TOOLS_PATH", "")
        if eda_path:
            candidates.append(eda_path)
            candidates.append(os.path.join(eda_path, "bin"))
    elif platform.system() == "Darwin":
        # Homebrew
        candidates.extend([
            "/usr/local/bin",
            "/opt/homebrew/bin",
        ])
        # Conda
        conda_prefix = os.environ.get("CONDA_PREFIX", "")
        if conda_prefix:
            candidates.append(os.path.join(conda_prefix, "bin"))
        candidates.extend([
            os.path.join(home, "miniconda3", "bin"),
            os.path.join(home, "anaconda3", "bin"),
        ])
    else:  # Linux
        candidates.extend([
            "/usr/local/bin",
            "/usr/bin",
        ])
        conda_prefix = os.environ.get("CONDA_PREFIX", "")
        if conda_prefix:
            candidates.append(os.path.join(conda_prefix, "bin"))
        candidates.extend([
            os.path.join(home, "miniconda3", "bin"),
            os.path.join(home, "anaconda3", "bin"),
        ])

    # Filter to existing directories only
    return [d for d in candidates if d and Path(d).exists()]


def _search_windows_registry(tool_name: str) -> str | None:
    """Search Windows registry for EDA tool install locations.

    Checks Uninstall registry keys for iverilog/yosys entries
    and looks for the executable in the install directory.
    """
    if platform.system() != "Windows":
        return None

    try:
        import winreg
    except ImportError:
        return None

    search_names = {
        "iverilog": ["iverilog", "icarus", "icarus verilog"],
        "yosys": ["yosys", "yosys hq"],
        "vvp": ["iverilog", "icarus", "icarus verilog"],
    }
    name_hints = search_names.get(tool_name, [tool_name])

    for hive_key, hive_name in [
        (winreg.HKEY_LOCAL_MACHINE, "HKLM"),
        (winreg.HKEY_CURRENT_USER, "HKCU"),
    ]:
        try:
            key = winreg.OpenKey(
                hive_key,
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                0,
                winreg.KEY_READ | winreg.KEY_WOW64_32KEY,
            )
        except OSError:
            continue

        try:
            idx = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(key, idx)
                    idx += 1
                    # Check if subkey name matches any hint
                    subkey_lower = subkey_name.lower()
                    if not any(h in subkey_lower for h in name_hints):
                        continue

                    subkey = winreg.OpenKey(key, subkey_name)
                    try:
                        install_loc, _ = winreg.QueryValueEx(subkey, "InstallLocation")
                        if install_loc:
                            # Search for the executable in install location
                            for suffix in _get_exec_suffixes():
                                candidate = Path(install_loc) / f"{tool_name}{suffix}"
                                if candidate.exists():
                                    logger.info(
                                        "Found %s via Windows registry: %s",
                                        tool_name, candidate,
                                    )
                                    return str(candidate)
                                candidate = Path(install_loc) / "bin" / f"{tool_name}{suffix}"
                                if candidate.exists():
                                    logger.info(
                                        "Found %s via Windows registry: %s",
                                        tool_name, candidate,
                                    )
                                    return str(candidate)
                    finally:
                        winreg.CloseKey(subkey)
                except OSError:
                    break
        finally:
            winreg.CloseKey(key)

    return None


def _find_oss_cad_suite() -> Path | None:
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


def get_tool_version(tool_name: str) -> str | None:
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


def _try_generic_version(tool_path: str) -> str | None:
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


def get_all_tool_versions() -> dict[str, str | None]:
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
