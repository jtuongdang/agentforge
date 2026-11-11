"""Portable, credential-redacted project checkpoints.

Checkpoint archives contain Orchestrator history and Git refs. Uncommitted content
is excluded unless the operator explicitly opts in after reviewing the preview.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit

from . import db
from .storage import ACTIVE_STATUSES
from .validation import SECRET_PATTERNS, check_secrets

FORMAT = "devagent.project-checkpoint"
FORMAT_VERSION = 1
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_GIT_OBJECT_BYTES = 4 * 1024 * 1024 * 1024
GIT_TIMEOUT_SECONDS = 180
_SECRET_BYTE_PATTERNS = [re.compile(pattern.pattern.encode("ascii"), pattern.flags & ~re.UNICODE)
                         for pattern in SECRET_PATTERNS]


class CheckpointError(ValueError):
    pass


def _git(root: Path, *args: str, timeout: int = GIT_TIMEOUT_SECONDS) -> bytes:
    result = subprocess.run(["git", *args], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=timeout, check=False)
    if result.returncode:
        message = result.stderr.decode("utf-8", "replace").strip()[-1000:]
        raise CheckpointError(f"Git command failed ({args[0]}): {message or result.returncode}")
    return result.stdout


def _repo_status(root: Path) -> list[tuple[str, str]]:
    output = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    fields = output.split(b"\0")
    changes: list[tuple[str, str]] = []
    index = 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if not field:
            continue
        if len(field) < 4:
            raise CheckpointError("Git returned an unreadable status entry")
        status = field[:2].decode("ascii", "replace")
        path = os.fsdecode(field[3:])
        if "R" in status or "C" in status:
            if index >= len(fields) or not fields[index]:
                raise CheckpointError("Git returned an incomplete rename entry")
            old_path = path
            path = os.fsdecode(fields[index])
            index += 1
            changes.append((old_path, "delete"))
        kind = "delete" if "D" in status else "file"
        changes.append((path, kind))
    return changes


def _safe_relative(path: str) -> str:
    normalized = path.replace("\\", "/")
    parsed = PurePosixPath(normalized)
    if parsed.is_absolute() or not parsed.parts or any(part in {"", ".", "..", ".git"} for part in parsed.parts):
        raise CheckpointError("A Git change path was unsafe for export")
    return parsed.as_posix()


def _scan_changes(root: Path) -> tuple[list[tuple[str, str]], list[str], list[str], str]:
    changes = _repo_status(root)
    file_paths = [path for path, kind in changes if kind == "file"]
    unscanned: list[str] = []
    fingerprint = hashlib.sha256()
    for relative, kind in changes:
        fingerprint.update(kind.encode("ascii") + b"\0" + os.fsencode(relative) + b"\0")
        if kind == "delete":
            continue
        candidate = root / relative
        try:
            stat_result = candidate.lstat()
            fingerprint.update(f"{stat_result.st_mode}:{stat_result.st_size}:{stat_result.st_mtime_ns}".encode())
            if candidate.is_file() and not candidate.is_symlink() and stat_result.st_size <= 2_000_000:
                with candidate.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(256 * 1024), b""):
                        fingerprint.update(chunk)
        except OSError:
            fingerprint.update(b"unreadable")
    for relative in file_paths:
        candidate = root / relative
        if candidate.is_symlink():
            continue
        if candidate.is_file() and candidate.stat().st_size > 2_000_000:
            unscanned.append(relative)
    secrets = check_secrets(str(root), file_paths)
    return changes, secrets, unscanned, fingerprint.hexdigest()


def _read_exact(stream, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = stream.read(min(1024 * 1024, size - len(chunks)))
        if not chunk:
            raise CheckpointError("Git object stream ended unexpectedly")
        chunks.extend(chunk)
    return bytes(chunks)


def _scan_git_objects_for_secrets(root: Path) -> None:
    """Scan reachable Git blobs before exporting history, with a bounded byte budget."""
    listing = subprocess.run(["git", "rev-list", "--objects", "--all"], cwd=root, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, timeout=GIT_TIMEOUT_SECONDS, check=False)
    if listing.returncode:
        raise CheckpointError(f"Could not enumerate Git objects for {root.name}")
    if len(listing.stdout) > 128 * 1024 * 1024:
        raise CheckpointError(f"Git object index is too large to credential-scan safely: {root.name}")
    object_ids = [line.split(b" ", 1)[0] for line in listing.stdout.splitlines() if line]
    if not object_ids:
        raise CheckpointError(f"Repository contains no reachable Git objects: {root.name}")
    process = subprocess.Popen(["git", "cat-file", "--batch"], cwd=root, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert process.stdin is not None and process.stdout is not None
    writer_error: list[Exception] = []

    def send_ids() -> None:
        try:
            assert process.stdin is not None
            for object_id in object_ids:
                process.stdin.write(object_id + b"\n")
            process.stdin.close()
        except (BrokenPipeError, OSError) as exc:
            writer_error.append(exc)

    writer = threading.Thread(target=send_ids, daemon=True)
    writer.start()
    total_blob_bytes = 0
    carry = b""
    try:
        for _ in object_ids:
            header = process.stdout.readline().rstrip(b"\n")
            parts = header.split()
            if len(parts) != 3 or parts[1] == b"missing":
                raise CheckpointError("Git object stream contains a missing object")
            object_type = parts[1]
            try:
                size = int(parts[2])
            except ValueError as exc:
                raise CheckpointError("Git returned an invalid object size") from exc
            if object_type == b"blob":
                total_blob_bytes += size
                if total_blob_bytes > MAX_GIT_OBJECT_BYTES:
                    raise CheckpointError("Git history exceeds the 4 GiB secret-scan budget")
                remaining = size
                carry = b""
                while remaining:
                    chunk = _read_exact(process.stdout, min(1024 * 1024, remaining))
                    scan = carry + chunk
                    if any(pattern.search(scan) for pattern in _SECRET_BYTE_PATTERNS):
                        raise CheckpointError(f"Possible credential found in reachable Git history: {root.name}")
                    carry = scan[-256:]
                    remaining -= len(chunk)
            else:
                _read_exact(process.stdout, size)
            if _read_exact(process.stdout, 1) != b"\n":
                raise CheckpointError("Git object stream framing was invalid")
    except Exception:
        process.kill()
        process.wait()
        writer.join(timeout=1)
        raise
    writer.join(timeout=GIT_TIMEOUT_SECONDS)
    if writer.is_alive():
        process.kill()
        process.wait()
        raise CheckpointError("Git object scan exceeded its time limit")
    if writer_error:
        raise CheckpointError("Could not stream Git objects for secret scanning")
    if process.wait(timeout=5) != 0:
        raise CheckpointError("Git object scan failed")


def checkpoint_preview() -> dict:
    """Return repository/change metadata without reading file contents."""
    with db.connect() as con:
        active_marks = ",".join("?" for _ in ACTIVE_STATUSES)
        active = con.execute(f"SELECT COUNT(*) FROM tasks WHERE status IN ({active_marks})", ACTIVE_STATUSES).fetchone()[0]
        repos = con.execute("SELECT id,name,path,remote,default_branch FROM repositories WHERE enabled=1 ORDER BY name").fetchall()
        tasks = con.execute("SELECT id,repository_id,worktree,branch FROM tasks WHERE worktree IS NOT NULL").fetchall()
    task_worktrees: dict[str, list[dict]] = {}
    for task in tasks:
        task_worktrees.setdefault(task["repository_id"], []).append(
            {"task_id": task["id"], "path": task["worktree"], "branch": task["branch"]})
    result = []
    for repo in repos:
        root = Path(repo["path"]).expanduser().resolve()
        entry = {"id": repo["id"], "name": repo["name"], "default_branch": repo["default_branch"],
                 "remote": _redact_remote(repo["remote"]), "path": str(root), "available": root.is_dir(),
                 "dirty_paths": [], "changes": [], "worktrees": []}
        if root.is_dir():
            try:
                changes, secret_paths, unscanned, fingerprint = _scan_changes(root)
                refs = _git(root, "for-each-ref", "--format=%(refname) %(objectname)",
                            "refs/heads", "refs/tags").decode("utf-8", "replace").splitlines()
                entry["dirty_paths"] = sorted({path for path, _ in changes})
                entry["changes"] = [{"path": path, "kind": kind} for path, kind in changes]
                entry["secret_paths"] = sorted(set(secret_paths))
                entry["secret_scan_incomplete_paths"] = sorted(set(unscanned))
                entry["fingerprint"] = fingerprint
                entry["refs"] = refs
            except (OSError, CheckpointError):
                entry["scan_error"] = "Repository status could not be read safely."
            for worktree in task_worktrees.get(repo["id"], []):
                path = Path(worktree["path"]).expanduser().resolve()
                item = {"task_id": worktree["task_id"], "path": str(path), "available": path.is_dir(),
                        "dirty_paths": [], "changes": []}
                if path.is_dir():
                    try:
                        changes, secret_paths, unscanned, fingerprint = _scan_changes(path)
                        refs = _git(path, "for-each-ref", "--format=%(refname) %(objectname)",
                                    "refs/heads", "refs/tags").decode("utf-8", "replace").splitlines()
                        item["dirty_paths"] = sorted({name for name, _ in changes})
                        item["changes"] = [{"path": name, "kind": kind} for name, kind in changes]
                        item["secret_paths"] = sorted(set(secret_paths))
                        item["secret_scan_incomplete_paths"] = sorted(set(unscanned))
                        item["fingerprint"] = fingerprint
                        item["refs"] = refs
                    except (OSError, CheckpointError):
                        item["scan_error"] = "Task worktree status could not be read safely."
                entry["worktrees"].append(item)
        result.append(entry)
    return {"format": FORMAT, "format_version": FORMAT_VERSION, "active_tasks": active,
            "repositories": result, "uncommitted_included_by_default": False,
            "credential_policy": "Application credentials and executable argv are removed; dirty files are secret-scanned."}


def _redact_remote(remote: str | None) -> str | None:
    if not remote:
        return None
    value = remote.strip()
    if "://" not in value:
        # Only accept ordinary SCP-style SSH remotes; never persist a user or ssh option.
        match = re.fullmatch(r"(?:(?P<user>[A-Za-z0-9._-]+)@)?(?P<host>[A-Za-z0-9.-]+):(?P<path>[A-Za-z0-9._/-]+)", value)
        if not match:
            return None
        user = "git@" if match.group("user") == "git" else ""
        return f"{user}{match.group('host')}:{match.group('path')}"
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in {"https", "http", "ssh"} or not parts.hostname:
        return None
    host = parts.hostname
    if port:
        host = f"{host}:{port}"
    return urlunsplit((parts.scheme.lower(), host, parts.path, "", ""))


def _scrub_snapshot_database(path: Path) -> None:
    with closing(sqlite3.connect(path)) as con:
        con.execute("PRAGMA foreign_keys=ON")
        local_paths = [row[0] for row in con.execute("SELECT path FROM repositories").fetchall()]
        local_paths.extend(row[0] for row in con.execute("SELECT worktree FROM tasks WHERE worktree IS NOT NULL").fetchall())
        local_paths.extend([str(db.DB_PATH.parent), str(db.DATA_ROOT), str(Path.home())])
        # Paths and credentials are machine-local. Repositories are disabled until remapped.
        for row in con.execute("SELECT id,remote FROM repositories").fetchall():
            con.execute("UPDATE repositories SET path=?,remote=?,enabled=0,validation_command='[]' WHERE id=?",
                        (f"portable://{row[0]}", _redact_remote(row[1]), row[0]))
        con.execute("UPDATE agents SET command='[]',enabled=0,available=0")
        con.execute("UPDATE agents SET availability_note='Reconfigure the executable and credentials after restore.'")
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='provider_connections'").fetchone():
            con.execute("UPDATE provider_connections SET secret_ref=NULL,state='unavailable',models='[]',checked_at=NULL")
        con.execute("DELETE FROM system_settings WHERE key NOT LIKE 'fleet.%'")
        con.execute("DELETE FROM credential_cooldowns")
        con.execute("DELETE FROM lead_lease")
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='lead_sessions'").fetchone():
            con.execute("UPDATE lead_sessions SET state='revoked',revoked_at=COALESCE(revoked_at,CURRENT_TIMESTAMP)")
        active_marks = ",".join("?" for _ in ACTIVE_STATUSES)
        con.execute(f"UPDATE tasks SET status='blocked',worktree=NULL,finished_at=CURRENT_TIMESTAMP,"
                    f"updated_at=CURRENT_TIMESTAMP,error='Restored checkpoint: inspect the worktree and reconcile before retry.' "
                    f"WHERE status IN ({active_marks})", ACTIVE_STATUSES)
        con.execute("UPDATE tasks SET worktree=NULL")
        con.execute("UPDATE runs SET state='interrupted',finished_at=COALESCE(finished_at,CURRENT_TIMESTAMP),"
                    "exit_code=COALESCE(exit_code,125) WHERE state='running'")
        con.execute("UPDATE task_writer_leases SET state='orphaned',updated_at=CURRENT_TIMESTAMP WHERE state='active'")
        tables = [row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            quoted_table = '"' + table.replace('"', '""') + '"'
            text_columns = [row[1] for row in con.execute(f"PRAGMA table_info({quoted_table})") if row[2].upper() in {"TEXT", ""}]
            for column in text_columns:
                quoted_column = '"' + column.replace('"', '""') + '"'
                for local_path in sorted({item for item in local_paths if item}, key=len, reverse=True):
                    con.execute(f"UPDATE {quoted_table} SET {quoted_column}=replace({quoted_column},?,?) "
                                f"WHERE typeof({quoted_column})='text' AND instr({quoted_column},?)>0",
                                (local_path, "[LOCAL_PATH]", local_path))
        con.commit()
        for table in tables:
            quoted_table = '"' + table.replace('"', '""') + '"'
            text_columns = [row[1] for row in con.execute(f"PRAGMA table_info({quoted_table})") if row[2].upper() in {"TEXT", ""}]
            for column in text_columns:
                quoted_column = '"' + column.replace('"', '""') + '"'
                rows = con.execute(f"SELECT {quoted_column} FROM {quoted_table} WHERE typeof({quoted_column})='text'")
                for (value,) in rows:
                    if any(pattern.search(value) for pattern in SECRET_PATTERNS):
                        raise CheckpointError(f"Possible credential found in checkpoint database table {table}")
    with closing(sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)) as con:
        result = con.execute("PRAGMA integrity_check").fetchone()
    if not result or result[0] != "ok":
        raise CheckpointError("Sanitized SQLite snapshot failed its integrity check")


def _make_database_snapshot(destination: Path, source: sqlite3.Connection | None = None) -> None:
    if not db.DB_PATH.is_file():
        raise CheckpointError("Orchestrator database is missing")
    if source is None:
        with closing(sqlite3.connect(db.DB_PATH, timeout=20)) as connection, closing(sqlite3.connect(destination)) as target:
            connection.backup(target)
    else:
        with closing(sqlite3.connect(destination)) as target:
            source.backup(target)
    _scrub_snapshot_database(destination)


def _add_repository_bundle(root: Path, bundle: Path) -> dict:
    ref_command = ("for-each-ref", "--format=%(refname) %(objectname)", "refs/heads", "refs/tags")
    refs = _git(root, *ref_command).decode("utf-8", "replace").splitlines()
    if not refs:
        raise CheckpointError(f"Repository has no local branch or tag refs: {root.name}")
    result = subprocess.run(["git", "bundle", "create", str(bundle), "--all"], cwd=root,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=GIT_TIMEOUT_SECONDS, check=False)
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip()[-1000:]
        raise CheckpointError(f"Could not bundle repository {root.name}: {detail}")
    if bundle.stat().st_size > MAX_ARCHIVE_BYTES:
        raise CheckpointError(f"Repository bundle exceeds the {MAX_ARCHIVE_BYTES // (1024**3)} GiB checkpoint limit")
    verify = subprocess.run(["git", "bundle", "verify", str(bundle)], cwd=root, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=GIT_TIMEOUT_SECONDS, check=False)
    if verify.returncode:
        raise CheckpointError(f"Git bundle verification failed for {root.name}")
    if _git(root, *ref_command).decode("utf-8", "replace").splitlines() != refs:
        raise CheckpointError(f"Repository refs changed during checkpoint export: {root.name}")
    return {"size_bytes": bundle.stat().st_size, "sha256": _sha256(bundle),
            "refs": [line for line in refs if line.strip()]}


def _ensure_source_state_unchanged(before: dict) -> None:
    after = checkpoint_preview()
    if after["active_tasks"]:
        raise CheckpointError("A task started during export. Checkpoint discarded; retry after tasks stop.")
    old_repos = {repo["id"]: repo for repo in before["repositories"]}
    new_repos = {repo["id"]: repo for repo in after["repositories"]}
    if old_repos.keys() != new_repos.keys():
        raise CheckpointError("Registered repositories changed during export. Checkpoint discarded.")
    for repo_id, old in old_repos.items():
        new = new_repos[repo_id]
        if (old.get("fingerprint") != new.get("fingerprint") or old.get("refs") != new.get("refs")
                or old.get("scan_error") != new.get("scan_error")
                or old.get("changes") != new.get("changes")):
            raise CheckpointError(f"Repository files changed during export: {old['name']}. Checkpoint discarded.")
        old_worktrees = {item["task_id"]: item for item in old["worktrees"]}
        new_worktrees = {item["task_id"]: item for item in new["worktrees"]}
        if old_worktrees.keys() != new_worktrees.keys():
            raise CheckpointError(f"Task worktrees changed during export: {old['name']}. Checkpoint discarded.")
        for task_id, old_worktree in old_worktrees.items():
            new_worktree = new_worktrees[task_id]
            if (old_worktree.get("fingerprint") != new_worktree.get("fingerprint")
                    or old_worktree.get("refs") != new_worktree.get("refs")):
                raise CheckpointError(f"Task worktree changed during export: {task_id}. Checkpoint discarded.")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _expected_checkpoint_entries(manifest: dict) -> set[str]:
    repositories = manifest.get("repositories")
    if not isinstance(repositories, list) or len(repositories) > 1000:
        raise CheckpointError("Checkpoint manifest contains an invalid repository list")
    included = manifest.get("uncommitted_included")
    if not isinstance(included, bool):
        raise CheckpointError("Checkpoint manifest has an invalid uncommitted flag")
    expected = {"manifest.json", "database.sqlite3"}
    repo_ids: set[str] = set()
    task_ids: set[str] = set()

    def add_changes(changes, prefix: str) -> None:
        if not isinstance(changes, list):
            raise CheckpointError("Checkpoint manifest contains invalid change metadata")
        for change in changes:
            if not isinstance(change, dict) or change.get("kind") not in {"file", "delete"}:
                raise CheckpointError("Checkpoint manifest contains an invalid change entry")
            relative = _safe_relative(change.get("path", ""))
            if included and change["kind"] == "file":
                expected.add(f"{prefix}/{relative}")

    for repo in repositories:
        if not isinstance(repo, dict):
            raise CheckpointError("Checkpoint manifest contains an invalid repository entry")
        repo_id = repo.get("id")
        if not isinstance(repo_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", repo_id) or repo_id in repo_ids:
            raise CheckpointError("Checkpoint manifest contains an invalid or duplicate repository id")
        repo_ids.add(repo_id)
        bundle_path = f"repositories/{repo_id}/repository.bundle"
        if repo.get("bundle_path") != bundle_path:
            raise CheckpointError("Checkpoint manifest contains an invalid repository bundle path")
        expected.add(bundle_path)
        add_changes(repo.get("changes", []), f"repositories/{repo_id}/working-tree")
        worktrees = repo.get("worktrees", [])
        if not isinstance(worktrees, list) or len(worktrees) > 10000:
            raise CheckpointError("Checkpoint manifest contains an invalid worktree list")
        for worktree in worktrees:
            task_id = worktree.get("task_id") if isinstance(worktree, dict) else None
            if (not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task_id)
                    or task_id in task_ids):
                raise CheckpointError("Checkpoint manifest contains an invalid or duplicate worktree task id")
            task_ids.add(task_id)
            add_changes(worktree.get("changes", []), f"repositories/{repo_id}/worktrees/{task_id}")
    return expected


def create_checkpoint(destination: Path, include_uncommitted: bool = False) -> dict:
    """Create an atomic .devagent archive; never overwrite an existing file."""
    destination = destination.expanduser().resolve()
    if destination.suffix != ".devagent":
        raise CheckpointError("Checkpoint file must use the .devagent extension")
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite existing checkpoint: {destination}")
    preview = checkpoint_preview()
    if preview["active_tasks"]:
        raise CheckpointError("Stop active tasks and retry so the checkpoint has a consistent run state.")
    for repo in preview["repositories"]:
        if not repo["available"]:
            raise CheckpointError(f"Repository is unavailable: {repo['name']}")
        if include_uncommitted and repo.get("scan_error"):
            raise CheckpointError(f"Repository changes could not be inspected: {repo['name']}")
        if include_uncommitted and repo.get("secret_paths"):
            raise CheckpointError(f"Possible secrets in dirty repository files: {repo['name']}")
        if include_uncommitted and repo.get("secret_scan_incomplete_paths"):
            raise CheckpointError(f"Some dirty files are too large for the secret scan: {repo['name']}")
        for worktree in repo["worktrees"]:
            if include_uncommitted and (worktree.get("scan_error") or worktree.get("secret_paths")
                                        or worktree.get("secret_scan_incomplete_paths")):
                raise CheckpointError(f"Task worktree failed the status/secret check: {repo['name']} / {worktree['task_id']}")
    dirty = any(repo["dirty_paths"] or any(item["dirty_paths"] for item in repo["worktrees"])
                for repo in preview["repositories"])
    if dirty and not include_uncommitted:
        # The preview names these omitted paths; commit or explicitly opt in before export.
        omitted = sorted({name for repo in preview["repositories"] for name in repo["dirty_paths"]} |
                         {name for repo in preview["repositories"] for item in repo["worktrees"]
                          for name in item["dirty_paths"]})
        omitted.extend(f"{repo['name']}: uncommitted state unavailable" for repo in preview["repositories"]
                       if repo.get("scan_error"))
    else:
        omitted = []

    if not db.DB_PATH.is_file():
        raise CheckpointError("Orchestrator database is missing")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix="devagent-checkpoint-", dir=destination.parent))
    staged = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    source_db = sqlite3.connect(db.DB_PATH, timeout=20)
    source_data_version = source_db.execute("PRAGMA data_version").fetchone()[0]
    try:
        database = temp_root / "database.sqlite3"
        _make_database_snapshot(database, source_db)
        manifest = {"format": FORMAT, "format_version": FORMAT_VERSION,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "database": {"path": "database.sqlite3", "size_bytes": database.stat().st_size,
                                 "sha256": _sha256(database)},
                    "repositories": [], "uncommitted_included": bool(include_uncommitted),
                    "omitted_uncommitted_paths": omitted,
                    "credential_policy": "Executable argv, API tokens, local paths and lead leases are removed or revoked.",
                    "warnings": ["Reconfigure agent executables and credentials after restore.",
                                 "Repositories are disabled until the operator remaps their paths.",
                                 "Derived RAG indexes are omitted; rebuild them from restored repositories."]}
        for repo in preview["repositories"]:
            root = Path(repo["path"]).expanduser().resolve()
            bundle = temp_root / f"repo-{repo['id']}.bundle"
            _scan_git_objects_for_secrets(root)
            bundle_meta = _add_repository_bundle(root, bundle)
            rel_bundle = f"repositories/{repo['id']}/repository.bundle"
            repo_info = {"id": repo["id"], "name": repo["name"], "remote": repo["remote"],
                         "default_branch": repo["default_branch"], "bundle_path": rel_bundle,
                         "bundle_sha256": bundle_meta["sha256"], "bundle_size_bytes": bundle_meta["size_bytes"],
                         "refs": bundle_meta["refs"], "dirty_paths": repo["dirty_paths"],
                         "changes": repo["changes"],
                         "worktrees": [{"task_id": item["task_id"], "branch": item.get("branch"),
                                        "available": item["available"],
                                        "dirty_paths": item["dirty_paths"], "changes": item["changes"]}
                                       for item in repo["worktrees"]]}
            manifest["repositories"].append(repo_info)
        manifest_path = temp_root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            archive.write(manifest_path, "manifest.json")
            archive.write(database, "database.sqlite3")
            for repo in manifest["repositories"]:
                bundle = temp_root / f"repo-{repo['id']}.bundle"
                archive.write(bundle, repo["bundle_path"])
            if include_uncommitted:
                _add_uncommitted_files(archive, preview)
        if staged.stat().st_size > MAX_ARCHIVE_BYTES:
            raise CheckpointError(f"Checkpoint exceeds the {MAX_ARCHIVE_BYTES // (1024**3)} GiB size limit")
        verify_checkpoint(staged)
        if source_db.execute("PRAGMA data_version").fetchone()[0] != source_data_version:
            raise CheckpointError("Application data changed during export. Checkpoint discarded; retry when writes stop.")
        _ensure_source_state_unchanged(preview)
        os.link(staged, destination)
        staged.unlink()
        return {"path": str(destination), "size_bytes": destination.stat().st_size,
                "sha256": _sha256(destination), "repository_count": len(preview["repositories"]),
                "uncommitted_included": bool(include_uncommitted), "omitted_uncommitted_paths": omitted}
    except Exception:
        staged.unlink(missing_ok=True)
        raise
    finally:
        source_db.close()
        shutil.rmtree(temp_root, ignore_errors=True)


def _add_uncommitted_files(archive: zipfile.ZipFile, preview: dict) -> None:
    for repo in preview["repositories"]:
        root = Path(repo["path"]).expanduser().resolve()
        _add_tree_overlay(archive, root, f"repositories/{repo['id']}/working-tree")
        for worktree in repo["worktrees"]:
            if worktree["available"]:
                path = Path(worktree["path"]).expanduser().resolve()
                _add_tree_overlay(archive, path, f"repositories/{repo['id']}/worktrees/{worktree['task_id']}")


def _add_tree_overlay(archive: zipfile.ZipFile, root: Path, prefix: str) -> None:
    for relative, kind in _repo_status(root):
        relative = _safe_relative(relative)
        target = root / relative
        if kind == "delete":
            continue
        if target.is_symlink():
            raise CheckpointError(f"Uncommitted symbolic links are not exportable: {relative}")
        resolved = target.resolve()
        if not resolved.is_relative_to(root):
            raise CheckpointError(f"Uncommitted path escapes repository root: {relative}")
        if not target.is_file():
            raise CheckpointError(f"Uncommitted path is not a regular file: {relative}")
        if target.stat().st_size > MAX_FILE_BYTES:
            raise CheckpointError(f"Uncommitted file exceeds the {MAX_FILE_BYTES // (1024**2)} MiB limit: {relative}")
        archive_name = f"{prefix}/{relative}"
        archive.write(target, archive_name)


def verify_checkpoint(path: Path) -> dict:
    """Verify the archive layout, database integrity and bundled Git object stores."""
    path = path.expanduser().resolve()
    if not path.is_file() or path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise CheckpointError("Checkpoint is missing or exceeds the archive size limit")
    with zipfile.ZipFile(path, "r") as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)) or len(names) > 100_000 or sum(info.file_size for info in infos) > MAX_ARCHIVE_BYTES:
            raise CheckpointError("Checkpoint contains duplicate or excessive archive entries")
        for info in infos:
            name = info.filename
            relative = PurePosixPath(name)
            mode = (info.external_attr >> 16) & 0xFFFF
            if ("\\" in name or relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts)
                    or (mode & 0o170000) == 0o120000):
                raise CheckpointError("Checkpoint contains an unsafe archive path")
        try:
            manifest_info = next((info for info in infos if info.filename == "manifest.json"), None)
            if manifest_info is None:
                raise CheckpointError("Checkpoint manifest is missing")
            if manifest_info.file_size > 32 * 1024 * 1024:
                raise CheckpointError("Checkpoint manifest exceeds the size limit")
            manifest = json.loads(archive.read(manifest_info))
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CheckpointError("Checkpoint manifest is missing or invalid") from exc
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT or manifest.get("format_version") != FORMAT_VERSION:
            raise CheckpointError("Unsupported checkpoint format version")
        expected_entries = _expected_checkpoint_entries(manifest)
        if set(names) != expected_entries:
            raise CheckpointError("Checkpoint entries do not match the manifest")
        db_meta = manifest.get("database", {})
        if not isinstance(db_meta, dict) or db_meta.get("path") != "database.sqlite3":
            raise CheckpointError("Checkpoint database path is invalid")
        db_info = next((info for info in infos if info.filename == "database.sqlite3"), None)
        if db_info is None:
            raise CheckpointError("Checkpoint database is missing")
        db_digest = hashlib.sha256()
        db_size = 0
        if shutil.disk_usage(path.parent).free < db_info.file_size + 32 * 1024**2:
            raise CheckpointError("Not enough free disk space to verify this checkpoint safely")
        with tempfile.NamedTemporaryFile(prefix="devagent-checkpoint-verify-", suffix=".sqlite3",
                                         dir=path.parent, delete=False) as stream:
            db_path = Path(stream.name)
        try:
            with db_path.open("wb") as target, archive.open(db_info, "r") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    target.write(chunk)
                    db_digest.update(chunk)
                    db_size += len(chunk)
            with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as con:
                integrity = con.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise CheckpointError("Checkpoint SQLite database failed integrity_check")
        finally:
            db_path.unlink(missing_ok=True)
        if db_size != db_meta.get("size_bytes") or db_digest.hexdigest() != db_meta.get("sha256"):
            raise CheckpointError("Checkpoint database checksum mismatch")
        repo_summaries = []
        for repo in manifest.get("repositories", []):
            bundle_name = repo.get("bundle_path", "")
            bundle_info = next((info for info in infos if info.filename == bundle_name), None)
            if bundle_info is None:
                raise CheckpointError("Checkpoint is missing a repository bundle")
            digest = hashlib.sha256()
            size = 0
            with archive.open(bundle_info, "r") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                    size += len(chunk)
            if size != repo.get("bundle_size_bytes") or digest.hexdigest() != repo.get("bundle_sha256"):
                raise CheckpointError("Repository bundle checksum mismatch")
            repo_summaries.append({"id": repo.get("id"), "name": repo.get("name"), "refs": len(repo.get("refs", []))})
    return {"format": FORMAT, "format_version": FORMAT_VERSION,
            "created_at": manifest.get("created_at"), "database_size_bytes": db_size,
            "repositories": repo_summaries, "uncommitted_included": bool(manifest.get("uncommitted_included"))}


def _checkout_bundle(bundle: Path, destination: Path, branch: str, remote: str | None = None) -> None:
    git_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    git_env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                    "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"})
    result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "clone", "--no-checkout",
                             str(bundle), str(destination)], env=git_env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=GIT_TIMEOUT_SECONDS,
                            check=False)
    if result.returncode:
        raise CheckpointError(f"Could not restore repository bundle: {result.stderr.decode('utf-8', 'replace')[-1000:]}")
    check = subprocess.run(["git", "check-ref-format", "--branch", branch], cwd=destination, env=git_env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=False)
    if check.returncode:
        raise CheckpointError(f"Checkpoint contains an invalid branch name: {branch!r}")
    branch_ref = subprocess.run(["git", "rev-parse", "--verify", f"refs/heads/{branch}"], cwd=destination, env=git_env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=False)
    if branch_ref.returncode:
        branch_ref = subprocess.run(["git", "rev-parse", "--verify", f"refs/remotes/origin/{branch}"],
                                    cwd=destination, env=git_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=10, check=False)
    if branch_ref.returncode:
        raise CheckpointError(f"Checkpoint branch is missing from its bundle: {branch}")
    object_id = branch_ref.stdout.decode("ascii", "strict").strip()
    checkout = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "checkout", "--force", "--detach", object_id],
                              cwd=destination, env=git_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=GIT_TIMEOUT_SECONDS, check=False)
    if checkout.returncode:
        raise CheckpointError(f"Checkpoint branch is missing from its bundle: {branch}")
    if remote:
        set_remote = subprocess.run(["git", "remote", "set-url", "origin", remote], cwd=destination,
                                    env=git_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=10, check=False)
    else:
        set_remote = subprocess.run(["git", "remote", "remove", "origin"], cwd=destination,
                                    env=git_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=10, check=False)
    if set_remote.returncode:
        raise CheckpointError("Could not sanitize the restored Git remote configuration")


def _overlay_target(root: Path, relative: str) -> Path:
    relative = _safe_relative(relative)
    base = root.resolve()
    target = root / relative
    current = root
    for part in PurePosixPath(relative).parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise CheckpointError(f"Checkpoint overlay traverses a repository symlink: {relative}")
    if not target.resolve(strict=False).is_relative_to(base):
        raise CheckpointError(f"Checkpoint overlay escapes its repository: {relative}")
    return target


def _apply_overlay(archive: zipfile.ZipFile, prefix: str, root: Path,
                   changes: list[dict], include_uncommitted: bool) -> None:
    if not include_uncommitted:
        return
    for change in changes:
        relative = _safe_relative(change.get("path", ""))
        if change.get("kind") == "delete":
            target = _overlay_target(root, relative)
            if target.is_symlink() or (target.exists() and not target.is_file()):
                raise CheckpointError(f"Checkpoint cannot safely apply a deletion: {relative}")
            target.unlink(missing_ok=True)
    for info in archive.infolist():
        if not info.filename.startswith(prefix + "/"):
            continue
        relative = _safe_relative(info.filename[len(prefix) + 1:])
        if info.is_dir() or info.file_size > MAX_FILE_BYTES:
            raise CheckpointError(f"Checkpoint contains an invalid or oversized overlay file: {relative}")
        target = _overlay_target(root, relative)
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise CheckpointError(f"Checkpoint overlay would replace a non-file: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info, "r") as source, target.open("wb") as destination:
            shutil.copyfileobj(source, destination, length=256 * 1024)
        mode = (info.external_attr >> 16) & 0o777
        if mode:
            target.chmod(mode & 0o755)


def restore_checkpoint(path: Path, destination: Path) -> dict:
    """Restore a checkpoint into a new side-by-side profile; existing data is never replaced."""
    path = path.expanduser().resolve()
    destination = destination.expanduser()
    if not path.is_file() or path.suffix != ".devagent":
        raise CheckpointError("Choose a valid .devagent checkpoint file")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"Refusing to overwrite an existing restore destination: {destination}")
    parent = destination.parent.resolve()
    if not parent.is_dir() or destination.name in {"", ".", ".."}:
        raise CheckpointError("Restore destination must be a new folder inside an existing directory")
    destination = parent / destination.name
    summary = verify_checkpoint(path)
    stage = None
    try:
        with zipfile.ZipFile(path, "r") as archive:
            manifest = json.loads(archive.read("manifest.json"))
            db_size = archive.getinfo("database.sqlite3").file_size
            clone_space = sum(archive.getinfo(repo["bundle_path"]).file_size *
                              (1 + len(repo.get("worktrees", []))) for repo in manifest.get("repositories", []))
            overlay_space = sum(info.file_size for info in archive.infolist()
                                if "/working-tree/" in info.filename or "/worktrees/" in info.filename)
            required_space = db_size + clone_space + overlay_space + 256 * 1024**2
            if shutil.disk_usage(parent).free < required_space:
                raise CheckpointError("Not enough free disk space to restore this checkpoint safely")
            stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}.restore-", dir=parent))
            db_path = stage / "data" / "orchestrator.sqlite3"
            db_path.parent.mkdir(parents=True)
            with archive.open("database.sqlite3", "r") as source, db_path.open("xb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            with closing(sqlite3.connect(db_path)) as con:
                imported_schema = con.execute("SELECT type,name,sql FROM sqlite_master "
                                               "WHERE name NOT LIKE 'sqlite_%'").fetchall()
                if any(kind in {"trigger", "view"} or (kind == "table" and sql and
                       sql.lstrip().upper().startswith("CREATE VIRTUAL TABLE"))
                       for kind, _, sql in imported_schema):
                    raise CheckpointError("Checkpoint database contains unsupported executable schema objects")
            _scrub_snapshot_database(db_path)
            repositories_root = stage / "repositories"
            worktrees_root = stage / "worktrees"
            repositories_root.mkdir()
            worktrees_root.mkdir()
            restored_repositories: dict[str, Path] = {}
            restored_worktrees: dict[str, Path] = {}
            for repository in manifest.get("repositories", []):
                repo_id = repository.get("id")
                if not isinstance(repo_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", repo_id):
                    raise CheckpointError("Checkpoint contains an invalid repository id")
                if repo_id in restored_repositories:
                    raise CheckpointError("Checkpoint contains a duplicate repository id")
                bundle_info = next((item for item in archive.infolist()
                                    if item.filename == repository.get("bundle_path")), None)
                if bundle_info is None:
                    raise CheckpointError("Checkpoint is missing a repository bundle")
                bundle_path = stage / f"{repo_id}.bundle"
                with archive.open(bundle_info, "r") as source, bundle_path.open("xb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
                repo_path = repositories_root / repo_id
                safe_remote = _redact_remote(repository.get("remote"))
                _checkout_bundle(bundle_path, repo_path, repository.get("default_branch") or "main", safe_remote)
                _apply_overlay(archive, f"repositories/{repo_id}/working-tree", repo_path,
                               repository.get("changes", []), bool(manifest.get("uncommitted_included")))
                restored_repositories[repo_id] = repo_path
                for worktree in repository.get("worktrees", []):
                    task_id = worktree.get("task_id")
                    if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task_id):
                        raise CheckpointError("Checkpoint contains an invalid task id for a worktree")
                    if task_id in restored_worktrees:
                        raise CheckpointError("Checkpoint contains a duplicate worktree task id")
                    if not worktree.get("available"):
                        continue
                    branch = worktree.get("branch") or repository.get("default_branch") or "main"
                    worktree_path = worktrees_root / task_id
                    _checkout_bundle(bundle_path, worktree_path, branch, safe_remote)
                    _apply_overlay(archive, f"repositories/{repo_id}/worktrees/{task_id}", worktree_path,
                                   worktree.get("changes", []), bool(manifest.get("uncommitted_included")))
                    restored_worktrees[task_id] = worktree_path
                bundle_path.unlink()

        with closing(sqlite3.connect(db_path)) as con:
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("PRAGMA trusted_schema=OFF")
            schema_objects = con.execute("SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
            if any(kind in {"trigger", "view"} for kind, _ in schema_objects):
                raise CheckpointError("Checkpoint database contains unsupported executable schema objects")
            db_repositories = {row[0] for row in con.execute("SELECT id FROM repositories")}
            if db_repositories != set(restored_repositories):
                raise CheckpointError("Checkpoint database and repository manifest do not match")
            db_tasks = {row[0] for row in con.execute("SELECT id FROM tasks")}
            manifest_tasks = {item["task_id"] for repo in manifest.get("repositories", [])
                              for item in repo.get("worktrees", [])}
            if not manifest_tasks.issubset(db_tasks):
                raise CheckpointError("Checkpoint worktree manifest references a missing task")
            active_marks = ",".join("?" for _ in ACTIVE_STATUSES)
            con.execute(f"UPDATE tasks SET status='blocked',worktree=NULL,finished_at=CURRENT_TIMESTAMP,"
                        f"updated_at=CURRENT_TIMESTAMP,error='Restored checkpoint: inspect and reconcile before retry.' "
                        f"WHERE status IN ({active_marks})", ACTIVE_STATUSES)
            con.execute("UPDATE runs SET state='interrupted',finished_at=COALESCE(finished_at,CURRENT_TIMESTAMP),"
                        "exit_code=COALESCE(exit_code,125) WHERE state='running'")
            con.execute("UPDATE task_writer_leases SET state='orphaned',updated_at=CURRENT_TIMESTAMP WHERE state='active'")
            con.execute("DELETE FROM lead_lease")
            con.execute("UPDATE lead_sessions SET state='revoked',revoked_at=COALESCE(revoked_at,CURRENT_TIMESTAMP)")
            con.execute("UPDATE agents SET command='[]',enabled=0,available=0,"
                        "availability_note='Reconfigure the executable and credentials after restore.'")
            if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='provider_connections'").fetchone():
                con.execute("UPDATE provider_connections SET secret_ref=NULL,state='unavailable',models='[]',checked_at=NULL")
            for repo_id, repo_path in restored_repositories.items():
                con.execute("UPDATE repositories SET path=?,enabled=1,validation_command='[]' WHERE id=?",
                            (str((destination / repo_path.relative_to(stage)).resolve()), repo_id))
            for task_id, worktree_path in restored_worktrees.items():
                con.execute("UPDATE tasks SET status='blocked',worktree=?,updated_at=CURRENT_TIMESTAMP,"
                            "error='Restored checkpoint: inspect this worktree and reconcile before retry.' WHERE id=?",
                            (str((destination / worktree_path.relative_to(stage)).resolve()), task_id))
            unavailable_worktrees = {item["task_id"] for repo in manifest.get("repositories", [])
                                     for item in repo.get("worktrees", []) if not item.get("available")}
            for task_id in unavailable_worktrees:
                con.execute("UPDATE tasks SET status='blocked',worktree=NULL,updated_at=CURRENT_TIMESTAMP,"
                            "error='Restored checkpoint: original worktree was unavailable; reconcile manually before retry.' WHERE id=?",
                            (task_id,))
            con.commit()
            violations = con.execute("PRAGMA foreign_key_check").fetchall()
            integrity = con.execute("PRAGMA integrity_check").fetchone()
            if violations or not integrity or integrity[0] != "ok":
                raise CheckpointError("Restored project database failed integrity checks")
            for table in [name for kind, name in schema_objects if kind == "table"]:
                quoted_table = '"' + table.replace('"', '""') + '"'
                columns = [row[1] for row in con.execute(f"PRAGMA table_info({quoted_table})")
                           if row[2].upper() in {"TEXT", ""}]
                for column in columns:
                    quoted_column = '"' + column.replace('"', '""') + '"'
                    values = con.execute(f"SELECT {quoted_column} FROM {quoted_table} "
                                         f"WHERE typeof({quoted_column})='text'")
                    if any(pattern.search(value) for (value,) in values for pattern in SECRET_PATTERNS):
                        raise CheckpointError(f"Possible credential found in restored database table {table}")
        instructions = stage / "RESTORE-README.txt"
        instructions.write_text(
            "This is a restored, side-by-side DevAgent Orchestrator profile.\n"
            "The original application data was not changed. Repository paths were remapped under repositories/.\n"
            "Agent commands and provider credentials were removed; configure them again before running tasks.\n"
            "Derived code and documentation indexes were omitted; rebuild them from the restored repositories.\n"
            "In a shell, start the application with CLINIC_ORCHESTRATOR_DATA_DIR set to this profile's data/ folder.\n"
            "Previously active tasks are blocked and runs are interrupted. Inspect restored worktrees before retrying.\n",
            encoding="utf-8")
        final = stage.parent / destination.name
        if final.exists() or final.is_symlink():
            raise FileExistsError(f"Refusing to overwrite an existing restore destination: {final}")
        os.rename(stage, final)
        return {"path": str(final), "repository_count": len(restored_repositories),
                "worktree_count": len(restored_worktrees), "uncommitted_included": summary["uncommitted_included"],
                "database_path": str(final / "data" / "orchestrator.sqlite3")}
    except Exception:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
        raise
