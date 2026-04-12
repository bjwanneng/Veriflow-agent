"""Project directory management for chat sessions.

Translates user's natural language requirements into the project directory
structure that the VeriFlow pipeline expects.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path


def create_project_from_requirement(
    requirement_text: str,
    base_dir: str | Path | None = None,
) -> Path:
    """Create a project directory with requirement.md from user input.

    Args:
        requirement_text: User's natural language design requirement.
        base_dir: Parent directory for the project. Defaults to temp dir.

    Returns:
        Path to the created project directory.
    """
    slug = _generate_slug(requirement_text)

    if base_dir is not None:
        base = Path(base_dir)
        base.mkdir(parents=True, exist_ok=True)
    else:
        base = Path(tempfile.mkdtemp(prefix="veriflow-chat-"))

    project_dir = base / slug
    project_dir.mkdir(parents=True, exist_ok=True)

    # Create workspace directory tree
    (project_dir / "workspace" / "docs").mkdir(parents=True, exist_ok=True)
    (project_dir / "workspace" / "rtl").mkdir(parents=True, exist_ok=True)
    (project_dir / "workspace" / "tb").mkdir(parents=True, exist_ok=True)
    (project_dir / ".veriflow").mkdir(parents=True, exist_ok=True)

    # Write requirement.md
    req_path = project_dir / "requirement.md"
    req_path.write_text(requirement_text, encoding="utf-8")

    return project_dir


def update_requirement(project_dir: Path, new_requirement: str) -> None:
    """Update the requirement.md in an existing project.

    Appends the new requirement as an addendum.
    """
    req_path = project_dir / "requirement.md"
    if req_path.exists():
        existing = req_path.read_text(encoding="utf-8")
        updated = f"{existing}\n\n---\n\n## Additional Requirements\n\n{new_requirement}"
        req_path.write_text(updated, encoding="utf-8")
    else:
        req_path.write_text(new_requirement, encoding="utf-8")


def _generate_slug(text: str) -> str:
    """Generate a filesystem-safe directory slug from requirement text.

    Examples:
        "Design a 4-bit ALU" -> "alu_design"
        "Create a RISC-V core" -> "riscv_core"
    """
    # Take first line, truncate
    first_line = text.strip().split("\n")[0][:60]

    # Extract key noun phrases
    # Look for "a/an/the X" patterns
    matches = re.findall(
        r'(?:a|an|the)\s+([\w-]+(?:\s+[\w-]+)?)',
        first_line.lower(),
    )

    if matches:
        # Use the last (usually most specific) match
        slug_base = matches[-1].strip()
    else:
        # Fallback: use first meaningful words
        words = re.findall(r'[a-zA-Z]+', first_line.lower())
        # Skip common verbs
        skip = {"design", "create", "build", "make", "write", "implement", "generate"}
        meaningful = [w for w in words if w not in skip]
        slug_base = "_".join(meaningful[:3]) if meaningful else "design"

    # Clean up for filesystem
    slug = re.sub(r'[^a-z0-9_]', '_', slug_base)
    slug = re.sub(r'_+', '_', slug).strip('_')

    return slug or "rtl_design"
