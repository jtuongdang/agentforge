from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .git_ops import changed_paths, commit_changes, create_worktree, run, task_diff

ACTIVE_STATUSES = ("planning", "assigned", "running", "validating", "recovering", "handoff_ready",
                   "awaiting_pr_approval", "publishing")


def _tree_size(root: Path) -> int:
    if not root.exists():
        return 0
    total = 0
    for root_dir, _, files in os.walk(root):
        for f in files:
            fp = os.path.join(root_dir, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def is_worktree_safe_to_remove(worktree_path: Path, base_branch: str | None) -> bool:
    """Only remove a clean worktree after its HEAD is integrated into the selected base."""
    if not worktree_path.is_dir() or not base_branch:
        return False
    status = run(["git", "status", "--porcelain", "--untracked-files=all", "--ignored=matching"], cwd=worktree_path)
    if status.returncode != 0 or status.stdout.strip():
        return False
    merged = run(["git", "merge-base", "--is-ancestor", "HEAD", base_branch], cwd=worktree_path)
    return merged.returncode == 0


def prune_worktrees(max_age_hours: float = 24.0, dry_run: bool = False,
                    clean_unmerged_targets_only: bool = True) -> dict:
    """Remove only old, clean worktrees whose task commit is integrated into their selected base."""
    now = datetime.now(timezone.utc)
    now_epoch = time.time()
    max_age_seconds = max_age_hours * 3600

    fully_removed = []
    target_cleaned = []
    freed_bytes = 0

    with db.connect() as con:
        active_marks = ",".join("?" for _ in ACTIVE_STATUSES)
        active_rows = con.execute(f"SELECT id FROM tasks WHERE status IN ({active_marks})", ACTIVE_STATUSES).fetchall()
        active_ids = {row["id"] for row in active_rows}

        candidate_rows = con.execute(f"""
            SELECT id, repository_id, branch, base_branch, worktree, status, finished_at, updated_at, created_at
            FROM tasks
            WHERE status NOT IN ({active_marks}) AND worktree IS NOT NULL
        """, ACTIVE_STATUSES).fetchall()

        repo_rows = con.execute("SELECT id, path, default_branch FROM repositories").fetchall()
        repos = {row["id"]: {"path": Path(row["path"]), "default_branch": row["default_branch"]} for row in repo_rows}

    # Evaluate task worktrees
    worktrees_to_process: list[dict] = []
    for row in candidate_rows:
        task_id = row["id"]
        wt_str = row["worktree"]
        if not wt_str:
            continue
        wt_path = Path(wt_str)
        if not wt_path.exists():
            continue
        managed_root = (Path(db.ROOT) / "data" / "worktrees").resolve()
        try:
            resolved_worktree = wt_path.resolve(strict=True)
            relative_worktree = resolved_worktree.relative_to(managed_root)
        except (OSError, ValueError):
            continue
        if len(relative_worktree.parts) != 2 or relative_worktree.parts[0] != row["repository_id"]:
            continue
        wt_path = resolved_worktree

        ts_str = row["finished_at"] or row["updated_at"] or row["created_at"]
        age_ok = False
        if ts_str:
            try:
                ts_clean = ts_str.replace("Z", "+00:00")
                dt = datetime.fromisoformat(ts_clean)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if (now - dt).total_seconds() >= max_age_seconds:
                    age_ok = True
            except Exception:
                pass

        if not age_ok:
            try:
                if (now_epoch - wt_path.stat().st_mtime) >= max_age_seconds:
                    age_ok = True
            except OSError:
                pass

        if age_ok:
            repo_info = repos.get(row["repository_id"], {})
            repo_path = repo_info.get("path")
            default_branch = repo_info.get("default_branch", "main")
            branch = row["branch"]

            # A PR URL or remote-tracking ref does not prove merge or preservation of local edits.
            base_branch = row["base_branch"] or default_branch
            is_safe_to_fully_remove = is_worktree_safe_to_remove(wt_path, base_branch)

            worktrees_to_process.append({
                "task_id": task_id,
                "repo_id": row["repository_id"],
                "repo_path": repo_path,
                "worktree_path": wt_path,
                "can_fully_remove": is_safe_to_fully_remove,
            })

    affected_repos: set[Path] = set()

    for item in worktrees_to_process:
        wt_path = item["worktree_path"]
        task_id = item["task_id"]
        repo_path = item["repo_path"]

        if item["can_fully_remove"]:
            # Full removal
            size = _tree_size(wt_path)
            freed_bytes += size
            fully_removed.append({"task_id": task_id, "path": str(wt_path), "bytes": size})

            if not dry_run:
                if not repo_path or not repo_path.is_dir():
                    continue
                removed = run(["git", "worktree", "remove", str(wt_path)], cwd=repo_path)
                if removed.returncode != 0:
                    continue
                affected_repos.add(repo_path)

                with db.transaction() as con:
                    con.execute("UPDATE tasks SET worktree=NULL WHERE id=?", (task_id,))
        # Dirty, ignored, unmerged and handoff worktrees are retained for operator review.

    if not dry_run:
        for repo_path in affected_repos:
            run(["git", "worktree", "prune"], cwd=repo_path)

    # Count remaining physical worktrees
    base_wt_root = Path(db.ROOT) / "data" / "worktrees"
    remaining = 0
    if base_wt_root.exists():
        for repo_dir in base_wt_root.iterdir():
            if repo_dir.is_dir():
                for sub in repo_dir.iterdir():
                    if sub.is_dir():
                        remaining += 1

    return {
        "fully_removed_count": len(fully_removed),
        "target_cleaned_count": len(target_cleaned),
        "total_cleaned_count": len(fully_removed) + len(target_cleaned),
        "freed_bytes": freed_bytes,
        "freed_mb": round(freed_bytes / (1024 * 1024), 2),
        "remaining_count": remaining,
        "max_age_hours": max_age_hours,
        "dry_run": dry_run,
        "policy": "preview by default; remove only clean worktrees integrated into their selected base; retain all other worktrees",
    }


class GitWorktreeManager:
    """Creates task branches and isolated worktrees; never cleans or mutates the source checkout."""

    def prepare(self, repository: dict, task_id: str, base_ref: str, title: str) -> tuple[str, str]:
        return create_worktree(repository, task_id, base_ref, title)

    def changed_paths(self, worktree: str, base_branch: str | None = None) -> list[str]:
        return changed_paths(worktree, base_branch=base_branch)

    def commit(self, worktree: str, paths: list[str], task_id: str, title: str, base_branch: str | None = None) -> str:
        return commit_changes(worktree, paths, task_id, title, base_branch=base_branch)

    def diff(self, worktree: str, commit: str, base_branch: str | None = None) -> dict:
        return task_diff(worktree, commit, base_branch=base_branch)

    def prune_old(self, max_age_hours: float = 24.0, dry_run: bool = False,
                  clean_unmerged_targets_only: bool = True) -> dict:
        return prune_worktrees(max_age_hours=max_age_hours, dry_run=dry_run,
                               clean_unmerged_targets_only=clean_unmerged_targets_only)
