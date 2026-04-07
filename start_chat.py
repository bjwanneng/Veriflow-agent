"""VeriFlow-Agent Chat Launcher.

Auto-installs dependencies, finds an available port,
starts the Gradio chat server, and opens the browser.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path


def is_port_available(port: int, host: str = "127.0.0.1") -> bool:
    """Check if a port is available for binding."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            s.bind((host, port))
            return True
    except OSError:
        return False


def find_available_port(start: int = 7860, end: int = 7980, host: str = "127.0.0.1") -> int:
    """Find the first available port in the given range."""
    for port in range(start, end):
        if is_port_available(port, host):
            return port
    raise RuntimeError(f"No available port found in range {start}-{end}")


def ensure_venv() -> Path | None:
    """Find or create the virtual environment. Returns python path or None."""
    script_dir = Path(__file__).resolve().parent
    venv_dir = script_dir / ".venv"

    if os.name == "nt":
        python_exe = venv_dir / "Scripts" / "python.exe"
        pip_exe = venv_dir / "Scripts" / "pip.exe"
    else:
        python_exe = venv_dir / "bin" / "python"
        pip_exe = venv_dir / "bin" / "pip"

    # Create venv if missing
    if not python_exe.exists():
        print("[1/3] Creating virtual environment...")
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            check=True,
        )
        print("      Virtual environment created.")
    else:
        print("[1/3] Virtual environment found.")

    return python_exe, pip_exe


def install_deps(pip_exe: Path) -> None:
    """Install required dependencies."""
    print("[2/3] Checking dependencies...")

    # Core deps (always install)
    deps = [
        "langgraph>=0.2.0",
        "langchain-core>=0.3.0",
        "langchain-anthropic>=0.2.0",
        "anthropic>=0.30.0",
        "pydantic>=2.0",
        "click>=8.0",
        "pyyaml>=6.0",
        "rich>=13.0",
        "gradio>=4.0",
    ]

    # Install quietly; pip skips already-satisfied packages quickly
    result = subprocess.run(
        [str(pip_exe), "install", "-q"] + deps,
        capture_output=True, text=True,
    )

    if result.returncode == 0:
        print("      Dependencies OK.")
    else:
        print("      Installing dependencies...")
        subprocess.run(
            [str(pip_exe), "install"] + deps,
            check=True,
        )
        print("      Dependencies installed.")


def main():
    print()
    print("=" * 50)
    print("  VeriFlow-Agent Chat Launcher")
    print("=" * 50)
    print()

    script_dir = Path(__file__).resolve().parent

    # Step 1: Ensure venv
    python_exe, pip_exe = ensure_venv()

    # Step 2: Install deps
    install_deps(pip_exe)

    # Step 3: Find port and launch
    print("[3/3] Starting chat server...")
    port = find_available_port(7860, 7980)
    url = f"http://127.0.0.1:{port}"
    print(f"      Port: {port}")
    print(f"      URL:  {url}")
    print()
    print("  Opening browser... (close this window to stop)")
    print()

    # Open browser after a short delay so server has time to start
    def _open_browser():
        time.sleep(2)
        webbrowser.open(url)

    import threading
    threading.Thread(target=_open_browser, daemon=True).start()

    # Launch Gradio chat
    # Add project src to path so imports work without pip install -e
    src_dir = script_dir / "src"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(src_dir) + os.pathsep + env.get("PYTHONPATH", "")

    subprocess.run(
        [str(python_exe), "-c", (
            "import sys;"
            f"sys.path.insert(0, r'{src_dir}');"
            "from veriflow_agent.chat import launch_chat;"
            f"launch_chat(host='0.0.0.0', port={port})"
        )],
        env=env,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  VeriFlow-Agent stopped.")
    except Exception as e:
        print(f"\n  Error: {e}")
        input("\n  Press Enter to exit...")
        sys.exit(1)
