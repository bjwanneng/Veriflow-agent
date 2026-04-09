#!/usr/bin/env python3
"""Test runner for VeriFlow-Agent test suite.

Usage:
    python run_tests.py              # Run all tests
    python run_tests.py -v           # Run with verbose output
    python run_tests.py phase1       # Run only phase 1 tests
    python run_tests.py phase3 -v    # Run phase 3 with verbose
"""

import argparse
import subprocess
import sys

TEST_PHASES = {
    "phase1": "tests/phase1_mocks/",
    "phase2": "tests/phase2_routing/",
    "phase3": "tests/phase3_agents/",
    "phase4": "tests/phase4_integration/",
}


def run_tests(phase=None, verbose=False, failfast=False):
    """Run tests for the specified phase or all phases."""
    cmd = ["python", "-m", "pytest"]

    if verbose:
        cmd.append("-v")

    if failfast:
        cmd.append("-x")

    if phase and phase in TEST_PHASES:
        cmd.append(TEST_PHASES[phase])
    elif phase == "all":
        cmd.append("tests/")
    else:
        # Run all tests
        cmd.append("tests/")

    print(f"Running: {' '.join(cmd)}")
    print("=" * 60)

    result = subprocess.run(cmd)
    return result.returncode


def main():
    parser = argparse.ArgumentParser(
        description="Run VeriFlow-Agent test suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_tests.py              # Run all tests
  python run_tests.py -v           # Run with verbose output
  python run_tests.py phase1       # Run only phase 1 (mock tests)
  python run_tests.py phase2       # Run only phase 2 (routing tests)
  python run_tests.py phase3 -v    # Run phase 3 (agent tests) verbose
  python run_tests.py phase4 -x    # Run phase 4 (integration) fail-fast
        """
    )

    parser.add_argument(
        "phase",
        nargs="?",
        choices=["phase1", "phase2", "phase3", "phase4", "all"],
        help="Test phase to run (default: all)"
    )

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose output"
    )

    parser.add_argument(
        "-x", "--failfast",
        action="store_true",
        help="Stop on first failure"
    )

    args = parser.parse_args()

    return_code = run_tests(
        phase=args.phase,
        verbose=args.verbose,
        failfast=args.failfast
    )

    sys.exit(return_code)


if __name__ == "__main__":
    main()
