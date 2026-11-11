from __future__ import annotations

import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .git_ops import changed_paths, commit_changes, create_worktree, run, task_diff

ACTIVE_STATUSES = ("planning", "assigned", "running", "validating", "recovering", "awaiting_pr_approval", "publishing")


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


def _clean_targets_in_worktree(worktree_path: Path, dry_run: bool = False) -> int:
    """Removes compiler target/ artifact directories inside a worktree while preserving all source files."""
    freed = 0
    if not worktree_path.exists():
        return 0
    for root_dir, dirs, _ in os.walk(worktree_path, topdown=True):
        if "target" in dirs:
            target_dir = Path(root_dir) / "target"
            sz = _tree_size(target_dir)
            freed += sz
            if not dry_run:
                shutil.rmtree(target_dir, ignore_errors=True)
            dirs.remove("target")  # Don't recurse into target
    return freed


def is_branch_merged_or_pushed(repo_path: Path, branch: str | None, default_branch: str = "main") -> bool:
    """Checks if a task branch is already pushed to origin or merged into a main/feature branch."""
    if not branch or not repo_path.is_dir():
        return False

    # 1. Check if pushed to origin
    has_remote = run(["git", "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch}"], cwd=repo_path).returncode == 0
    if has_remote:
        return True

    # 2. Check if merged into main, master, or current branch
    for target in (default_branch, "main", "master", "feature/patient-reception-workflow"):
        ref_exists = run(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{target}"], cwd=repo_path).returncode == 0
        if ref_exists:
            is_ancestor = run(["git", "merge-base", "--is-ancestor", branch, target], cwd=repo_path).returncode == 0
            if is_ancestor:
                return True
    return False


def prune_worktrees(max_age_hours: float = 24.0, dry_run: bool = False,
                    clean_unmerged_targets_only: bool = True) -> dict:
    """Ultra-safe worktree cleanup:
    1. Active tasks are strictly preserved (never touched).
    2. Finished tasks that are MERGED or PUSHED: fully removed via git worktree remove.
    3. Finished tasks that are NOT YET MERGED/PUSHED: keep worktree and all source files intact,
       only clean compiler artifact 'target/' directories to save space safely.
    """
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
            SELECT id, repository_id, branch, worktree, status, finished_at, updated_at, created_at, pr_url
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

            is_safe_to_fully_remove = False
            # Check PR url
            if row["pr_url"]:
                is_safe_to_fully_remove = True
            elif repo_path:
                is_safe_to_fully_remove = is_branch_merged_or_pushed(repo_path, branch, default_branch)

            # Failed tasks with no commits produced can also be fully removed safely
            if row["status"] in ("failed", "cancelled") and not branch:
                is_safe_to_fully_remove = True

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

        if item["can_fully_remove"] or not clean_unmerged_targets_only:
            # Full removal
            size = _tree_size(wt_path)
            freed_bytes += size
            fully_removed.append({"task_id": task_id, "path": str(wt_path), "bytes": size})

            if not dry_run:
                if repo_path and repo_path.is_dir():
                    run(["git", "worktree", "remove", "--force", str(wt_path)], cwd=repo_path)
                    affected_repos.add(repo_path)

                if wt_path.exists():
                    shutil.rmtree(wt_path, ignore_errors=True)

                with db.transaction() as con:
                    con.execute("UPDATE tasks SET worktree=NULL WHERE id=?", (task_id,))
        else:
            # Ultra-safe: Unmerged branch -> Clean compiler target/ only, keep source code 100%
            target_freed = _clean_targets_in_worktree(wt_path, dry_run=dry_run)
            if target_freed > 0:
                freed_bytes += target_freed
                target_cleaned.append({"task_id": task_id, "path": str(wt_path), "bytes": target_freed})

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
        "policy": "ultra_safe (merged/pushed -> full remove, unmerged -> clean target/ only)",
    }


class GitWorktreeManager:
    """Creates task branches and isolated worktrees; never cleans or mutates the source checkout."""

    def prepare(self, repository: dict, task_id: str, base_ref: str, title: str) -> tuple[str, str]:
        return create_worktree(repository, task_id, base_ref, title)

    def changed_paths(self, worktree: str, base_branch: str | None = None) -> list[str]:
        return changed_paths(worktree, base_branch=base_branch)

    def commit(self, worktree: str, paths: list[str], task_id: str, title: str, base_branch: str | None = None) -> str:
        return commit_changes(worktree, paths, task_id, title, base_branch=base_branch)

    def diff(self, worktree: str, commit: str) -> dict:
        return task_diff(worktree, commit)

    def prune_old(self, max_age_hours: float = 24.0, dry_run: bool = False,
                  clean_unmerged_targets_only: bool = True) -> dict:
        return prune_worktrees(max_age_hours=max_age_hours, dry_run=dry_run,
                               clean_unmerged_targets_only=clean_unmerged_targets_only)
