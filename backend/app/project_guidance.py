from __future__ import annotations

from pathlib import Path

from . import db


MAX_GUIDANCE_CHARS = 20_000
MAX_RAG_CHARS = 8_000
MAX_FILE_CHARS = 8_000
ROOT_GUIDANCE_FILES = (
    "AGENTS.md", "README.md", "CONTRIBUTING.md", "DOMAIN_ANALYSIS.md", "ERD_DIAGRAM.md",
    "UI_CODING_STANDARDS.md", "ARCHITECTURE.md", "docs/PROJECT_CONVENTIONS.md",
    "docs/DEVELOPMENT_CONVENTIONS.md", "docs/README.md", "docs/DOMAIN_ANALYSIS.md",
    "docs/ERD_DIAGRAM.md", "docs/UI_CODING_STANDARDS.md", "docs/ARCHITECTURE.md",
)


def load_project_guidance(repository_root: str | Path, file_globs: list[str],
                          repository_id: str | None = None, query: str = "",
                          include_rag: bool = True) -> tuple[str, list[str]]:
    """Collect bounded system and repository instructions before an Agent starts editing."""
    root = Path(repository_root).expanduser().resolve()
    candidates: list[tuple[Path, str]] = []

    # The Orchestrator's own baseline is always included, even for another registered project.
    baseline = Path(db.ROOT) / "docs" / "PROJECT_CONVENTIONS.md"
    if baseline.is_file():
        candidates.append((baseline, "Orchestrator baseline: docs/PROJECT_CONVENTIONS.md"))

    for relative in ROOT_GUIDANCE_FILES:
        candidates.append((root / relative, relative))

    # Also read AGENTS.md files between the repository root and each explicitly scoped path.
    for pattern in file_globs:
        current = root
        for part in Path(pattern.replace("\\", "/")).parts:
            if any(char in part for char in "*?[]"):
                break
            current = current / part
            if current.is_dir():
                candidates.append((current / "AGENTS.md", (current / "AGENTS.md").relative_to(root).as_posix()))

    seen: set[Path] = set()
    sections: list[str] = []
    filenames: list[str] = []
    consumed = 0
    for path, display_name in candidates:
        try:
            resolved = path.resolve(strict=True)
            if resolved in seen or not resolved.is_file():
                continue
            if display_name != "Orchestrator baseline: docs/PROJECT_CONVENTIONS.md" and not resolved.is_relative_to(root):
                continue
            content = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeError, RuntimeError):
            continue
        remaining = MAX_GUIDANCE_CHARS - consumed
        if remaining <= 0:
            sections.append("[Guidance omitted: total context limit reached.]")
            break
        truncated = len(content) > min(MAX_FILE_CHARS, remaining)
        content = content[:min(MAX_FILE_CHARS, remaining)]
        if truncated:
            content += "\n\n[Document truncated to fit the task guidance limit.]"
        seen.add(resolved)
        filenames.append(display_name)
        sections.append(f"### {display_name}\n\n{content.strip()}")
        consumed += len(content)
    rag_block = ""
    if include_rag and repository_id and query.strip():
        from .rag import retrieve_excerpts
        rag_block, rag_files = retrieve_excerpts(repository_id, root, query, MAX_RAG_CHARS,
                                                 exclude_paths={n for n in filenames})
        filenames.extend(f"{f} (retrieved)" for f in rag_files)
    if not sections and not rag_block:
        return "", []
    block = (
        "## Mandatory project guidance\n\n"
        "The Orchestrator has read and supplied the guidance below. Read it before changing files. "
        "Use the applicable rules, report the guidance filenames in your completion summary, and inspect "
        "the repository's existing patterns before creating files.\n\n"
        + "\n\n".join(sections)
    )
    if rag_block:
        block += "\n\n" + rag_block
    return block, filenames
