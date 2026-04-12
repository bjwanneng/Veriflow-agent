"""Context document scanner for VeriFlow-Agent.

Recursively scans ``context/`` under the project directory, auto-categorizes
files by extension and content heuristics, and returns structured context
for injection into the architect prompt.

Directory layout (short names, user-friendly):
    context/
    ├── req/        ← 需求文档 (requirements)
    ├── ref/        ← 参考设计 / IP datasheets (reference)
    ├── con/        ← 约束条件 (constraints: timing, area, power)
    └── code/       ← 编码规范 / 模板 (coding style / templates)

The scanner is tolerant: files placed in the wrong subdirectory or directly
in context/ root are still found and auto-categorized by content analysis.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

logger = logging.getLogger("veriflow.context")

# Maximum characters read per file to keep prompt size reasonable.
_MAX_FILE_CHARS = 12_000

# Maximum total characters across all context documents.
_MAX_TOTAL_CHARS = 80_000


class DocCategory(str, Enum):
    """Document categories used for prompt injection."""

    REQUIREMENT = "requirement"
    REFERENCE = "reference"
    CONSTRAINT = "constraint"
    CODE_STYLE = "code_style"
    UNKNOWN = "unknown"


# ── Heuristic rules ────────────────────────────────────────────────────────

# Extension → category mapping
_EXT_MAP: dict[str, DocCategory] = {
    # RTL / reference design
    ".v": DocCategory.REFERENCE,
    ".sv": DocCategory.REFERENCE,
    ".vh": DocCategory.REFERENCE,
    ".svh": DocCategory.REFERENCE,
    # Timing / physical constraints
    ".sdc": DocCategory.CONSTRAINT,
    ".xdc": DocCategory.CONSTRAINT,
    ".pcf": DocCategory.CONSTRAINT,
    ".qsf": DocCategory.CONSTRAINT,
    # Config / structured data
    ".json": DocCategory.UNKNOWN,
    ".yaml": DocCategory.UNKNOWN,
    ".yml": DocCategory.UNKNOWN,
    # Documents
    ".md": DocCategory.UNKNOWN,
    ".txt": DocCategory.UNKNOWN,
    ".rst": DocCategory.UNKNOWN,
    ".pdf": DocCategory.UNKNOWN,
}

# Directory name → category mapping (short names)
_DIR_MAP: dict[str, DocCategory] = {
    "req": DocCategory.REQUIREMENT,
    "ref": DocCategory.REFERENCE,
    "con": DocCategory.CONSTRAINT,
    "code": DocCategory.CODE_STYLE,
    # Long-form aliases (tolerant)
    "requirements": DocCategory.REQUIREMENT,
    "reference": DocCategory.REFERENCE,
    "constraints": DocCategory.CONSTRAINT,
    "coding": DocCategory.CODE_STYLE,
    "ip": DocCategory.REFERENCE,
    "knowledge": DocCategory.REFERENCE,
    "docs": DocCategory.REFERENCE,
}

# Content keywords → category (used when extension/dir are ambiguous)
_KEYWORD_RULES: list[tuple[DocCategory, list[str]]] = [
    (
        DocCategory.CONSTRAINT,
        ["clock", "timing", "constraint", "frequency", "period", "sdc", "xdc",
         "时序", "约束", "频率", "时钟"],
    ),
    (
        DocCategory.REQUIREMENT,
        ["需求", "规格", "功能要求", "requirement", "specification", "spec",
         "design goal", "设计目标"],
    ),
    (
        DocCategory.CODE_STYLE,
        ["coding style", "naming convention", "编码规范", "命名规则",
         "verilog style", "rtl style"],
    ),
    (
        DocCategory.REFERENCE,
        ["datasheet", "ip core", "reference design", "reference model",
         "数据手册", "参考设计", "ip 说明"],
    ),
]


@dataclass
class ContextFile:
    """A scanned context file with its categorization."""

    path: Path
    category: DocCategory
    content: str
    rel_path: str  # relative to project_dir
    size_chars: int = 0

    def __post_init__(self) -> None:
        self.size_chars = len(self.content)


@dataclass
class ContextBundle:
    """Aggregated context documents ready for prompt injection."""

    files: list[ContextFile] = field(default_factory=list)

    @property
    def total_chars(self) -> int:
        return sum(f.size_chars for f in self.files)

    @property
    def by_category(self) -> dict[DocCategory, list[ContextFile]]:
        buckets: dict[DocCategory, list[ContextFile]] = {}
        for f in self.files:
            buckets.setdefault(f.category, []).append(f)
        return buckets

    def to_prompt_section(self) -> str:
        """Format all context documents as a markdown section for prompt injection.

        Returns empty string if no documents found.
        """
        if not self.files:
            return ""

        sections: list[str] = []
        sections.append("# 参考文档 (context/)")
        sections.append(
            f"以下是从项目 context/ 目录扫描到的 {len(self.files)} 个参考文档，"
            f"共 {self.total_chars:,} 字符。请结合这些文档进行架构设计。\n"
        )

        for cat in (DocCategory.REQUIREMENT, DocCategory.REFERENCE,
                     DocCategory.CONSTRAINT, DocCategory.CODE_STYLE,
                     DocCategory.UNKNOWN):
            cat_files = [f for f in self.files if f.category == cat]
            if not cat_files:
                continue

            label = {
                DocCategory.REQUIREMENT: "需求补充",
                DocCategory.REFERENCE: "参考设计 / IP",
                DocCategory.CONSTRAINT: "约束条件",
                DocCategory.CODE_STYLE: "编码规范",
                DocCategory.UNKNOWN: "其他文档",
            }.get(cat, "文档")

            sections.append(f"## {label}")
            for f in cat_files:
                sections.append(f"### {f.rel_path}")
                sections.append(f"```")
                sections.append(f.content[:_MAX_FILE_CHARS])
                sections.append(f"```")
                sections.append("")

        return "\n".join(sections)


def _classify_by_dir(rel_path: str) -> DocCategory | None:
    """Classify by parent directory name."""
    parts = Path(rel_path).parts
    if len(parts) < 2:
        return None
    # First component under context/
    dir_name = parts[1].lower() if parts[0] == "context" else parts[0].lower()
    return _DIR_MAP.get(dir_name)


def _classify_by_ext(path: Path) -> DocCategory | None:
    """Classify by file extension."""
    return _EXT_MAP.get(path.suffix.lower())


def _classify_by_content(content: str) -> DocCategory:
    """Classify by content keyword analysis (first 2000 chars)."""
    sample = content[:2000].lower()
    for category, keywords in _KEYWORD_RULES:
        if any(kw in sample for kw in keywords):
            return category
    return DocCategory.UNKNOWN


def _classify_file(path: Path, rel_path: str, content: str) -> DocCategory:
    """Classify a file using cascading heuristics.

    Priority: directory hint > extension hint > content analysis.
    """
    # 1. Directory hint (strongest signal)
    cat = _classify_by_dir(rel_path)
    if cat is not None:
        return cat

    # 2. Extension hint
    cat = _classify_by_ext(path)
    if cat is not None and cat != DocCategory.UNKNOWN:
        return cat

    # 3. Content analysis
    return _classify_by_content(content)


def scan_context(project_dir: Path) -> ContextBundle:
    """Scan the context/ directory under project_dir.

    Returns a ContextBundle with all discovered documents, auto-categorized.
    Returns empty bundle if context/ doesn't exist.

    Args:
        project_dir: Root project directory containing context/

    Returns:
        ContextBundle with categorized context files.
    """
    context_dir = project_dir / "context"
    if not context_dir.is_dir():
        logger.debug("No context/ directory found in %s", project_dir)
        return ContextBundle()

    # Supported extensions (skip binaries, images, etc.)
    supported_exts = {
        ".v", ".sv", ".vh", ".svh",
        ".md", ".txt", ".rst",
        ".json", ".yaml", ".yml",
        ".sdc", ".xdc", ".pcf", ".qsf",
        ".c", ".h", ".py",
    }

    files: list[ContextFile] = []
    total_chars = 0

    # Recursively scan all files
    for path in sorted(context_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in supported_exts:
            # Warn about unsupported but classified extensions (e.g., .pdf)
            if path.suffix.lower() == ".pdf":
                logger.warning(
                    "PDF file %s found in context/ but cannot be read as text. "
                    "Convert to .md or .txt first.",
                    path.relative_to(context_dir),
                )
            continue
        # Skip hidden files and __pycache__
        if any(part.startswith(".") or part == "__pycache__" for part in path.parts):
            continue

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            logger.warning("Failed to read context file %s: %s", path, e)
            continue

        if not content.strip():
            continue

        rel_path = path.relative_to(project_dir)
        category = _classify_file(path, str(rel_path), content)

        # Truncate individual file
        if len(content) > _MAX_FILE_CHARS:
            content = content[:_MAX_FILE_CHARS] + "\n... (truncated)"
            logger.info("Truncated context file %s to %d chars", rel_path, _MAX_FILE_CHARS)

        ctx_file = ContextFile(
            path=path,
            category=category,
            content=content,
            rel_path=str(rel_path),
        )
        files.append(ctx_file)
        total_chars += ctx_file.size_chars

        if total_chars >= _MAX_TOTAL_CHARS:
            logger.info(
                "Context total reached %d chars limit, stopping scan",
                _MAX_TOTAL_CHARS,
            )
            break

    if files:
        logger.info(
            "Context scan: found %d files (%d chars) in %s",
            len(files), total_chars, context_dir,
        )
        for cat, cat_files in ContextBundle(files=files).by_category.items():
            logger.debug(
                "  %s: %d files", cat.value, len(cat_files),
            )

    return ContextBundle(files=files)
