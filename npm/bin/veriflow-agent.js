#!/usr/bin/env node
"use strict";

const { spawn } = require("child_process");
const { execSync } = require("child_process");

const args = process.argv.slice(2);

/**
 * Find Python 3.10+ executable.
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

// Try direct CLI first (if pip-installed veriflow-agent is in PATH as a different name)
// This avoids recursion because we use the explicit .exe/.cmd extension
let cmd, cmdArgs;

if (process.platform === "win32") {
  // On Windows, try the pip-installed CLI directly with extension
  // to avoid calling ourselves (the npm wrapper)
  try {
    const result = execSync("where veriflow-agent.exe 2>&1", { encoding: "utf-8" });
    if (result.includes("Scripts")) {
      cmd = "veriflow-agent.exe";
      cmdArgs = args;
    }
  } catch {
    // not found via where
  }
}

if (!cmd) {
  // Fallback: run via python -m
  const python = findPython();
  if (!python) {
    console.error(
      "\nError: Python 3.10+ not found.\n" +
      "Please install Python 3.10+: https://www.python.org/downloads/\n" +
      "Then run:  pip install veriflow-agent\n"
    );
    process.exit(127);
  }
  cmd = python;
  cmdArgs = ["-m", "veriflow_agent.cli", ...args];
}

const child = spawn(cmd, cmdArgs, { stdio: "inherit", shell: true });

child.on("error", (err) => {
  console.error("Error:", err.message);
  console.error(
    "\nveriflow-agent Python package may not be installed.\n" +
    "Run: pip install veriflow-agent\n"
  );
  process.exit(127);
});

child.on("exit", (code) => {
  process.exit(code || 0);
});
