#!/usr/bin/env node
"use strict";

const { execSync } = require("child_process");

/**
 * postinstall script: auto-install the Python CLI via pip.
 * Gracefully degrades if Python/pip is not available.
 */

function findPython() {
  const commands = process.platform === "win32"
    ? ["python", "python3", "py"]
    : ["python3", "python"];

  for (const cmd of commands) {
    try {
      const version = execSync(cmd + " --version 2>&1", { encoding: "utf-8" }).trim();
      const match = version.match(/(\d+)\.(\d+)/);
      if (match) {
        const major = parseInt(match[1]);
        const minor = parseInt(match[2]);
        if (major > 3 || (major === 3 && minor >= 10)) {
          return cmd;
        }
      }
    } catch {
      // not found, try next
    }
  }
  return null;
}

function findPip(pythonCmd) {
  // Try python -m pip first (most reliable)
  try {
    execSync(pythonCmd + " -m pip --version 2>&1", { encoding: "utf-8" });
    return pythonCmd + " -m pip";
  } catch {
    // fall through
  }
  // Try bare pip commands
  const pipCommands = process.platform === "win32"
    ? ["pip", "pip3"]
    : ["pip3", "pip"];

  for (const cmd of pipCommands) {
    try {
      execSync(cmd + " --version 2>&1", { encoding: "utf-8" });
      return cmd;
    } catch {
      // not found
    }
  }
  return null;
}

// --- Main ---

const python = findPython();
if (!python) {
  console.warn(
    "\nWarning: Python 3.10+ not found.\n" +
    "Please install Python 3.10 or later: https://www.python.org/downloads/\n" +
    "Then run:  pip install veriflow-agent\n"
  );
  process.exit(0); // Don't fail the npm install
}

const pyVersion = execSync(python + " --version 2>&1", { encoding: "utf-8" }).trim();
console.log("  Found: " + pyVersion);

const pip = findPip(python);
if (!pip) {
  console.warn(
    "\nWarning: pip not found.\n" +
    "Please install pip and then run: pip install veriflow-agent\n"
  );
  process.exit(0);
}

console.log("  Installing veriflow-agent from GitHub via pip...\n");
try {
  execSync(
    pip + " install git+https://github.com/bjwanneng/Veriflow-agent.git",
    { stdio: "inherit" }
  );
  console.log("\n  veriflow-agent installed successfully!");
} catch {
  console.warn(
    "\n  Warning: pip install failed. You can install manually:\n" +
    "    pip install git+https://github.com/bjwanneng/Veriflow-agent.git\n"
  );
}
