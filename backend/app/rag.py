"""Local, dependency-free retrieval over repository documentation (SQLite FTS5 + BM25).

Documents are split into heading-aware chunks and indexed incrementally (by mtime/size),
so agents and the leader can pull only the relevant excerpts instead of whole files.
"""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

from . import db

RAG_DB_PATH = Path(os.getenv("CLINIC_ORCHESTRATOR_RAG_DB", db.ROOT / "data" / "rag.sqlite3"))
SKIP_DIRS = {".git", "target", "node_modules", ".idea", ".venv", "dist", "build", "data"}
MAX_FILE_BYTES = 1_500_000
CHUNK_CHARS = 1_800
TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _connect() -> sqlite3.Connection:
    RAG_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(RAG_DB_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS files(repo TEXT, path TEXT, mtime REAL, size INTEGER, PRIMARY KEY(repo, path))")
    con.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
        repo UNINDEXED, path UNINDEXED, heading, body, tokenize='unicode61 remove_diacritics 2')""")
    return con


def _markdown_files(root: Path):
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            if name.lower().endswith(".md"):
                path = Path(base) / name
                try:
                    if path.stat().st_size <= MAX_FILE_BYTES:
                        yield path
                except OSError:
                    continue


def _chunks(text: str) -> list[tuple[str, str]]:
    """Split on markdown headings, then on paragraph boundaries to <= CHUNK_CHARS."""
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in text.splitlines():
        if re.match(r"^#{1,4}\s", line):
            sections.append((line.lstrip("# ").strip(), []))
        else:
            sections[-1][1].append(line)
    result: list[tuple[str, str]] = []
    for heading, lines in sections:
        body = "\n".join(lines).strip()
        if not body:
            continue
        current = ""
        for paragraph in re.split(r"\n\s*\n", body):
            if current and len(current) + len(paragraph) > CHUNK_CHARS:
                result.append((heading, current.strip()))
                current = ""
            while len(paragraph) > CHUNK_CHARS:
                result.append((heading, paragraph[:CHUNK_CHARS]))
                paragraph = paragraph[CHUNK_CHARS:]
            current += paragraph + "\n\n"
        if current.strip():
            result.append((heading, current.strip()))
    return result


def index_repository(repo_id: str, repo_root: str | Path) -> dict:
    """Incrementally (re)index Markdown docs; removes entries for deleted files."""
    root = Path(repo_root).expanduser().resolve()
    stats = {"indexed": 0, "unchanged": 0, "removed": 0, "chunks": 0}
    con = _connect()
    try:
        known = {r["path"]: (r["mtime"], r["size"]) for r in
                 con.execute("SELECT path,mtime,size FROM files WHERE repo=?", (repo_id,))}
        seen: set[str] = set()
        for path in _markdown_files(root):
            rel = path.relative_to(root).as_posix()
            seen.add(rel)
            st = path.stat()
            if known.get(rel) == (st.st_mtime, st.st_size):
                stats["unchanged"] += 1
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            con.execute("DELETE FROM chunks WHERE repo=? AND path=?", (repo_id, rel))
            pieces = _chunks(text)
            con.executemany("INSERT INTO chunks(repo,path,heading,body) VALUES(?,?,?,?)",
                            [(repo_id, rel, h, b) for h, b in pieces])
            con.execute("INSERT OR REPLACE INTO files VALUES(?,?,?,?)", (repo_id, rel, st.st_mtime, st.st_size))
            stats["indexed"] += 1
            stats["chunks"] += len(pieces)
        for rel in set(known) - seen:
            con.execute("DELETE FROM chunks WHERE repo=? AND path=?", (repo_id, rel))
            con.execute("DELETE FROM files WHERE repo=? AND path=?", (repo_id, rel))
            stats["removed"] += 1
        con.commit()
    finally:
        con.close()
    return stats


def _fts_query(text: str, max_terms: int = 24) -> str:
    terms: list[str] = []
    for raw in TOKEN_RE.findall(text.lower()):
        # keep identifiers like AUDIT_READ matchable as "audit read" (FTS tokenizes on underscores)
        for token in (raw.split("_") if "_" in raw else [raw]):
            if len(token) >= 3 and not token.isdigit() and token not in terms:
                terms.append(token)
    return " OR ".join(f'"{t}"' for t in terms[:max_terms])


# Authoritative decision/contract docs rank above proposals, the giant task board and legacy pilot notes.
PATH_WEIGHTS = (("decision", 1.4), ("approval", 1.4), ("contract", 1.3), ("owner", 1.2), ("conventions", 1.1),
                ("proposal", 0.8), ("task-board", 0.6), ("pilot", 0.5), ("hien-trang", 0.9))


def _path_weight(path: str) -> float:
    name = path.lower()
    weight = 1.0
    for marker, factor in PATH_WEIGHTS:
        if marker in name:
            weight *= factor
    return weight


OUT_OF_SCOPE_HEADINGS = ("không thuộc", "ngoài phạm vi", "out of scope", "non-goal")


def search(repo_id: str, query: str, limit: int = 6, exclude_paths: set[str] | None = None) -> list[dict]:
    match = _fts_query(query)
    if not match:
        return []
    con = _connect()
    try:
        rows = con.execute(
            """SELECT path, heading, body, bm25(chunks, 0.0, 0.0, 3.0, 1.0) AS score
               FROM chunks WHERE chunks MATCH ? AND repo=? ORDER BY score LIMIT ?""",
            (match, repo_id, limit * 8)).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()
    best: dict[tuple[str, str], dict] = {}
    for row in rows:
        if exclude_paths and row["path"] in exclude_paths:
            continue
        score = -row["score"] * _path_weight(row["path"])
        if any(m in (row["heading"] or "").lower() for m in OUT_OF_SCOPE_HEADINGS):
            score *= 0.4  # sections that only say what the doc does NOT cover
        key = (row["path"], row["heading"])  # one result per section: no duplicate chunks
        if key not in best or score > best[key]["score"]:
            best[key] = {"path": row["path"], "heading": row["heading"], "text": row["body"],
                         "score": round(score, 3)}
    return sorted(best.values(), key=lambda h: h["score"], reverse=True)[:limit]


def retrieve_excerpts(repo_id: str, repo_root: str | Path, query: str,
                      max_chars: int = 8_000, limit: int = 8,
                      exclude_paths: set[str] | None = None) -> tuple[str, list[str]]:
    """Refresh the index (cheap, incremental) and format the best excerpts within a char budget."""
    try:
        index_repository(repo_id, repo_root)
        hits = search(repo_id, query, limit=limit, exclude_paths=exclude_paths)
    except Exception:
        return "", []
    sections, files, used = [], [], 0
    for hit in hits:
        title = f"{hit['path']}" + (f" › {hit['heading']}" if hit["heading"] else "")
        text = hit["text"][: max(0, min(CHUNK_CHARS, max_chars - used))]
        if not text:
            break
        sections.append(f"#### {title}\n\n{text.strip()}")
        used += len(text)
        if hit["path"] not in files:
            files.append(hit["path"])
    if not sections:
        return "", []
    block = ("## Relevant documentation excerpts (retrieved)\n\n"
             "Top-ranked passages from the repository docs for this task. Read the full file when an excerpt is "
             "insufficient.\n\n" + "\n\n".join(sections))
    return block, files
