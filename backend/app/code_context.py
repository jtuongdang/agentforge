"""Revision- and worktree-scoped source retrieval for Python repositories."""
from __future__ import annotations

import ast
import hashlib
import os
import re
import sqlite3
from pathlib import Path

from . import db
from .git_ops import run
from .validation import check_secrets


CONTEXT_DB_PATH = Path(os.getenv("CLINIC_ORCHESTRATOR_RAG_DB", db.DATA_ROOT / "rag.sqlite3"))
SKIP_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules", "target", "dist",
             "build", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", "vendor", "generated"}
MAX_SOURCE_FILE_BYTES = 1_500_000
MAX_INDEX_FILES = 4_000
MAX_INDEX_BYTES = 40_000_000
MAX_SOURCE_CHUNK_CHARS = 1_600
MAX_CONTEXT_CHARS = 8_000
TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _connect() -> sqlite3.Connection:
    CONTEXT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(CONTEXT_DB_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS source_files(
            repo TEXT NOT NULL, revision TEXT NOT NULL, worktree TEXT NOT NULL, path TEXT NOT NULL,
            content_hash TEXT NOT NULL, size INTEGER NOT NULL, parser_status TEXT NOT NULL,
            indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(repo,revision,worktree,path)
        );
        CREATE TABLE IF NOT EXISTS source_symbols(
            repo TEXT NOT NULL, revision TEXT NOT NULL, worktree TEXT NOT NULL, path TEXT NOT NULL,
            qualified_name TEXT NOT NULL, kind TEXT NOT NULL, start_line INTEGER NOT NULL, end_line INTEGER NOT NULL,
            signature TEXT NOT NULL, docstring TEXT NOT NULL, content_hash TEXT NOT NULL,
            PRIMARY KEY(repo,revision,worktree,path,qualified_name,start_line)
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS source_chunks USING fts5(
            repo UNINDEXED, revision UNINDEXED, worktree UNINDEXED, path UNINDEXED,
            qualified_name, kind UNINDEXED, start_line UNINDEXED, end_line UNINDEXED,
            content_hash UNINDEXED, body, tokenize='unicode61 remove_diacritics 2'
        );
    """)
    return con


def _revision(root: Path, requested: str | None) -> str:
    result = run(["git", "rev-parse", "HEAD"], root)
    if result.returncode != 0:
        raise ValueError("Source indexing requires a Git worktree with a readable HEAD revision")
    actual = result.stdout.strip().lower()
    if requested and requested.lower() != actual:
        raise ValueError("Requested source revision does not match the selected worktree HEAD")
    if not re.fullmatch(r"[0-9a-f]{40,64}", actual):
        raise ValueError("Git returned an invalid source revision")
    return actual


def _source_files(root: Path, tracked_paths: set[str]):
    for current, dirs, names in os.walk(root, followlinks=False):
        base = Path(current)
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS and not (base / name).is_symlink()]
        for name in names:
            path = base / name
            relative = path.relative_to(root).as_posix()
            if relative not in tracked_paths or path.suffix.lower() not in {".py", ".md"} or path.is_symlink():
                continue
            try:
                resolved = path.resolve(strict=True)
                resolved.relative_to(root)
                if not resolved.is_file() or resolved.stat().st_size > MAX_SOURCE_FILE_BYTES:
                    continue
            except (OSError, ValueError, RuntimeError):
                continue
            yield path


def _source_chunks(text: str, suffix: str) -> list[dict]:
    lines = text.splitlines()
    chunks: list[dict] = []
    if suffix == ".py":
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return []

        def visit(nodes: list[ast.stmt], parents: tuple[str, ...] = ()) -> None:
            for node in nodes:
                if isinstance(node, ast.ClassDef):
                    qualified = ".".join((*parents, node.name))
                    _append_node(chunks, lines, node, qualified, "class", text)
                    visit(node.body, (*parents, node.name))
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    qualified = ".".join((*parents, node.name))
                    kind = "method" if parents else "function"
                    _append_node(chunks, lines, node, qualified, kind, text)
                    visit(node.body, (*parents, node.name))

        visit(tree.body)
        if not chunks and lines:
            chunks.extend(_line_chunks(lines, "<module>", "module"))
        return chunks

    heading = ""
    current: list[str] = []
    start = 1
    for number, line in enumerate(lines, 1):
        if line.startswith("#") and line.lstrip("#").startswith(" "):
            if current:
                chunks.extend(_line_chunks(current, heading or "<document>", "markdown", start))
            heading = line.lstrip("# ").strip()[:160]
            current = [line]
            start = number
        elif not line.strip() and current and sum(len(item) + 1 for item in current) >= MAX_SOURCE_CHUNK_CHARS:
            chunks.extend(_line_chunks(current, heading or "<document>", "markdown", start))
            current = []
            start = number + 1
        else:
            current.append(line)
        if sum(len(item) + 1 for item in current) >= MAX_SOURCE_CHUNK_CHARS:
            chunks.extend(_line_chunks(current, heading or "<document>", "markdown", start))
            current = []
            start = number + 1
    if current:
        chunks.extend(_line_chunks(current, heading or "<document>", "markdown", start))
    return chunks


def _append_node(chunks: list[dict], lines: list[str], node: ast.AST, qualified: str,
                 kind: str, text: str) -> None:
    start = min([node.lineno, *(item.lineno for item in getattr(node, "decorator_list", []))])
    end = getattr(node, "end_lineno", None) or node.lineno
    source = "\n".join(lines[start - 1:end])
    signature = ""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        try:
            arguments = ast.unparse(node.args)
        except (AttributeError, ValueError):
            arguments = ""
        signature = f"{'async ' if isinstance(node, ast.AsyncFunctionDef) else ''}def {qualified}({arguments})"
        if node.returns:
            signature += f" -> {ast.unparse(node.returns)}"
    elif isinstance(node, ast.ClassDef):
        signature = f"class {qualified}"
    doc = ast.get_docstring(node) or ""
    for part in _line_chunks(source.splitlines(), qualified, kind, start):
        part["signature"] = signature[:500]
        part["docstring"] = doc[:1000]
        chunks.append(part)


def _line_chunks(lines: list[str], name: str, kind: str, start_line: int = 1) -> list[dict]:
    result: list[dict] = []
    current: list[str] = []
    current_start = start_line
    size = 0
    for offset, line in enumerate(lines):
        if current and size + len(line) + 1 > MAX_SOURCE_CHUNK_CHARS:
            result.append({"qualified_name": name, "kind": kind, "start_line": current_start,
                           "end_line": current_start + len(current) - 1, "body": "\n".join(current)})
            current_start = start_line + offset
            current = []
            size = 0
        current.append(line)
        size += len(line) + 1
    if current:
        result.append({"qualified_name": name, "kind": kind, "start_line": current_start,
                       "end_line": current_start + len(current) - 1, "body": "\n".join(current)})
    return result


def index_worktree(repo_id: str, worktree: str | Path, revision: str | None = None) -> dict:
    """Index Python symbols and Markdown excerpts within one exact repository revision/worktree."""
    root = Path(worktree).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Source context root must be a directory")
    revision = _revision(root, revision)
    status = run(["git", "status", "--porcelain", "--untracked-files=all"], root)
    if status.returncode != 0:
        raise ValueError("Could not verify that the source worktree matches its pinned revision")
    if status.stdout.strip():
        raise ValueError("Source context requires a clean worktree matching its pinned revision")
    tree = run(["git", "ls-tree", "-r", "--name-only", "-z", revision], root)
    if tree.returncode != 0:
        raise ValueError("Could not list tracked source files at the pinned revision")
    tracked_paths = set(tree.stdout.split("\0"))
    root_key = root.as_posix()
    stats = {"revision": revision, "indexed": 0, "unchanged": 0, "removed": 0,
             "chunks": 0, "symbols": 0, "parse_errors": 0, "skipped_secrets": 0, "truncated": False}
    files = []
    total_bytes = 0
    for candidate in _source_files(root, tracked_paths):
        try:
            size = candidate.stat().st_size
        except OSError:
            continue
        if len(files) >= MAX_INDEX_FILES or total_bytes + size > MAX_INDEX_BYTES:
            stats["truncated"] = True
            break
        files.append(candidate)
        total_bytes += size
    safe_files = []
    for path in files:
        relative = path.relative_to(root).as_posix()
        if check_secrets(str(root), [relative]):
            stats["skipped_secrets"] += 1
        else:
            safe_files.append(path)

    con = _connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        # A stable worktree can only describe its current HEAD. Retire stale revisions for this worktree.
        stale = con.execute("SELECT DISTINCT revision FROM source_files WHERE repo=? AND worktree=? AND revision<>?",
                            (repo_id, root_key, revision)).fetchall()
        if stale:
            stats["removed"] += con.execute("SELECT COUNT(*) FROM source_files WHERE repo=? AND worktree=? AND revision<>?",
                                            (repo_id, root_key, revision)).fetchone()[0]
        for row in stale:
            con.execute("DELETE FROM source_chunks WHERE repo=? AND worktree=? AND revision=?",
                        (repo_id, root_key, row["revision"]))
            con.execute("DELETE FROM source_symbols WHERE repo=? AND worktree=? AND revision=?",
                        (repo_id, root_key, row["revision"]))
            con.execute("DELETE FROM source_files WHERE repo=? AND worktree=? AND revision=?",
                        (repo_id, root_key, row["revision"]))
        known = {row["path"]: row["content_hash"] for row in con.execute(
            "SELECT path,content_hash FROM source_files WHERE repo=? AND revision=? AND worktree=?",
            (repo_id, revision, root_key))}
        seen: set[str] = set()
        for path in safe_files:
            relative = path.relative_to(root).as_posix()
            seen.add(relative)
            try:
                raw = path.read_bytes()
                content = raw.decode("utf-8")
            except (OSError, UnicodeError):
                continue
            digest = hashlib.sha256(raw).hexdigest()
            if known.get(relative) == digest:
                stats["unchanged"] += 1
                continue
            suffix = path.suffix.lower()
            pieces = _source_chunks(content, suffix)
            parse_error = suffix == ".py" and bool(content.strip()) and not pieces
            status = "parse_error" if parse_error else "indexed"
            con.execute("DELETE FROM source_chunks WHERE repo=? AND revision=? AND worktree=? AND path=?",
                        (repo_id, revision, root_key, relative))
            con.execute("DELETE FROM source_symbols WHERE repo=? AND revision=? AND worktree=? AND path=?",
                        (repo_id, revision, root_key, relative))
            if not parse_error:
                con.executemany("""INSERT INTO source_chunks(repo,revision,worktree,path,qualified_name,kind,
                    start_line,end_line,content_hash,body) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    [(repo_id, revision, root_key, relative, piece["qualified_name"], piece["kind"],
                      piece["start_line"], piece["end_line"], digest, piece["body"]) for piece in pieces])
                con.executemany("""INSERT OR REPLACE INTO source_symbols(repo,revision,worktree,path,
                    qualified_name,kind,start_line,end_line,signature,docstring,content_hash)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    [(repo_id, revision, root_key, relative, piece["qualified_name"], piece["kind"],
                      piece["start_line"], piece["end_line"], piece.get("signature", ""),
                      piece.get("docstring", ""), digest) for piece in pieces
                     if piece["kind"] in {"class", "function", "method"}])
            con.execute("""INSERT OR REPLACE INTO source_files(repo,revision,worktree,path,content_hash,size,parser_status)
                VALUES(?,?,?,?,?,?,?)""", (repo_id, revision, root_key, relative, digest,
                                                  path.stat().st_size, status))
            stats["indexed"] += 1
            stats["chunks"] += len(pieces) if not parse_error else 0
            stats["symbols"] += sum(1 for piece in pieces if piece["kind"] in {"class", "function", "method"})
            stats["parse_errors"] += int(parse_error)
        for relative in (set(known) - seen) if not stats["truncated"] else ():
            con.execute("DELETE FROM source_chunks WHERE repo=? AND revision=? AND worktree=? AND path=?",
                        (repo_id, revision, root_key, relative))
            con.execute("DELETE FROM source_symbols WHERE repo=? AND revision=? AND worktree=? AND path=?",
                        (repo_id, revision, root_key, relative))
            con.execute("DELETE FROM source_files WHERE repo=? AND revision=? AND worktree=? AND path=?",
                        (repo_id, revision, root_key, relative))
            stats["removed"] += 1
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return stats


def _fts_query(query: str, max_terms: int = 24) -> str:
    terms = []
    for raw in TOKEN_RE.findall(query.lower()):
        terms.extend(raw.split("_") if "_" in raw else [raw])
    unique = list(dict.fromkeys(term for term in terms if len(term) >= 2 and not term.isdigit()))[:max_terms]
    return " OR ".join(f'"{term}"' for term in unique)


def search(repo_id: str, worktree: str | Path, query: str, revision: str | None = None,
           max_chars: int = MAX_CONTEXT_CHARS, limit: int = 8,
           exclude_paths: set[str] | None = None) -> dict:
    root = Path(worktree).expanduser().resolve(strict=True)
    revision = _revision(root, revision)
    stats = index_worktree(repo_id, root, revision)
    match = _fts_query(query)
    if not match:
        return {"revision": revision, "worktree": root.as_posix(), "stats": stats,
                "context": "", "citations": [], "symbols": []}
    con = _connect()
    try:
        rows = con.execute("""SELECT path,qualified_name,kind,start_line,end_line,content_hash,body,
            bm25(source_chunks,0.0,0.0,0.0,0.0,2.0,0.0,0.0,0.0,0.0,1.0) AS score
            FROM source_chunks WHERE source_chunks MATCH ? AND repo=? AND revision=? AND worktree=?
            ORDER BY score LIMIT ?""", (match, repo_id, revision, root.as_posix(), max(1, min(limit * 5, 60)))).fetchall()
        token = query.strip().replace(" ", "_").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        symbols = con.execute("""SELECT path,qualified_name,kind,start_line,end_line,signature,docstring,content_hash
            FROM source_symbols WHERE repo=? AND revision=? AND worktree=?
            AND (qualified_name LIKE ? ESCAPE '\\' OR signature LIKE ? ESCAPE '\\')
            ORDER BY CASE WHEN lower(qualified_name)=lower(?) THEN 0 ELSE 1 END,start_line LIMIT 6""",
            (repo_id, revision, root.as_posix(), f"%{token}%", f"%{token}%", query.strip().replace(" ", "_"))).fetchall()
    finally:
        con.close()
    sections: list[str] = []
    citations = []
    used = 0
    for row in rows:
        if exclude_paths and row["path"] in exclude_paths:
            continue
        try:
            source = (root / row["path"]).resolve(strict=True)
            source.relative_to(root)
            current_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        except (OSError, ValueError, RuntimeError):
            continue
        if current_hash != row["content_hash"]:
            continue
        body = row["body"]
        title = f"{row['path']}:{row['start_line']}-{row['end_line']} [{revision[:12]}]"
        section = f"#### {title} · {row['qualified_name']}\n\n{body.strip()}"
        remaining = max(0, min(max_chars, MAX_CONTEXT_CHARS) - used)
        if remaining <= 0:
            break
        section = section[:remaining]
        sections.append(section)
        used += len(section)
        citations.append({"path": row["path"], "start_line": row["start_line"], "end_line": row["end_line"],
                          "revision": revision, "worktree": root.as_posix(), "content_hash": row["content_hash"],
                          "kind": row["kind"], "symbol": row["qualified_name"]})
    symbol_hits = [{"path": row["path"], "qualified_name": row["qualified_name"], "kind": row["kind"],
                    "start_line": row["start_line"], "end_line": row["end_line"],
                    "signature": row["signature"], "docstring": row["docstring"],
                    "revision": revision, "content_hash": row["content_hash"]} for row in symbols]
    context = "## Revision-pinned source excerpts\n\n" + "\n\n".join(sections) if sections else ""
    return {"revision": revision, "worktree": root.as_posix(), "stats": stats,
            "context": context[:max_chars], "citations": citations, "symbols": symbol_hits}
