"""Shared test fixtures and configuration for VeriFlow-Agent tests."""

import json
import shutil
import tempfile
from pathlib import Path
from typing import Generator

import pytest


@pytest.fixture
def sample_project(tmp_path):
    """Create a copy of the sample project fixture in a temp directory.

    Yields the Path to the temp project directory with:
        - requirement.md
        - workspace/docs/spec.json
        - workspace/rtl/alu.v
        - workspace/tb/tb_alu.v
    """
    fixture_src = Path(__file__).parent / "fixtures" / "sample_project"
    dest = tmp_path / "sample_project"
    shutil.copytree(str(fixture_src), str(tmp_path / "sample_project"))
    return tmp_path / "sample_project"


@pytest.fixture
def sample_project_dir(sample_project):
    """Return the project directory path as string (for agent context)."""
    return str(sample_project)


@pytest.fixture
def spec_json(sample_project):
    """Path to spec.json in sample project."""
    return sample_project / "workspace" / "docs" / "spec.json"


@pytest.fixture
def spec_data(spec_json):
    """Parsed spec.json data."""
    return json.loads(spec_json.read_text(encoding="utf-8"))


