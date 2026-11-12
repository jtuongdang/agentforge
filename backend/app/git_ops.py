from __future__ import annotations

import re
import subprocess
import uuid
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

from . import db


def run(args: list[str], cwd: str | Path | None = None, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
                          timeout=timeout, check=False)


def require_ok(args: list[str], cwd: str | Path, timeout: int = 60) -> str:
    result = run(args, cwd, timeout)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Git command failed").strip()[-4000:])
    return result.stdout.strip()


def register_repository(name: str, path: str, default_branch: str, validation_profile: str = "auto",
                        validation_command: list[str] | None = None) -> dict:
    root = Path(path).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Repository path is not a directory")
    top = Path(require_ok(["git", "rev-parse", "--show-toplevel"], root)).resolve()
    remote = run(["git", "remote", "get-url", "origin"], top)
    branch = default_branch.strip() or "main"
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", branch) or branch.startswith("-") or ".." in branch:
        raise ValueError("Invalid default branch name")
    branch_exists = any(run(["git", "rev-parse", "--verify", f"{ref}^{{commit}}"], top).returncode == 0
                        for ref in (f"refs/remotes/origin/{branch}", f"refs/heads/{branch}"))
    if not branch_exists:
        raise ValueError(f"Default branch '{branch}' was not found locally; fetch it before registering this repository")
    repo_id = str(uuid.uuid4())
    with db.transaction() as con:
        existing = con.execute("SELECT id FROM repositories WHERE path=?", (str(top),)).fetchone()
        if existing:
            raise ValueError("This Git repository is already registered")
        con.execute("""INSERT INTO repositories(id,name,path,remote,default_branch,validation_profile,validation_command)
            VALUES(?,?,?,?,?,?,?)""",
                    (repo_id, name.strip(), str(top), remote.stdout.strip() if remote.returncode == 0 else None,
                     branch, validation_profile, db.dumps(validation_command or [])))
        row = con.execute("SELECT * FROM repositories WHERE id=?", (repo_id,)).fetchone()
    return _decode_repository(dict(row))


def repository(repo_id: str) -> dict:
    with db.connect() as con:
        row = con.execute("SELECT * FROM repositories WHERE id=? AND enabled=1", (repo_id,)).fetchone()
    if row is None:
        raise ValueError("Repository is not registered or disabled")
    return _decode_repository(dict(row))


def _decode_repository(repo: dict) -> dict:
    repo["validation_command"] = db.json.loads(repo.get("validation_command") or "[]")
    repo["enabled"] = bool(repo.get("enabled", True))
    return repo


def validate_repository_base(repo: dict, base_branch: str | None = None) -> str:
    root = Path(repo["path"])
    branch = (base_branch or repo["default_branch"]).strip()
    if not branch or run(["git", "check-ref-format", "--branch", branch], root).returncode != 0:
        raise ValueError(f"Base branch '{branch}' is not a valid Git branch name")
    try:
        return resolve_base_commit(root, branch)
    except (OSError, RuntimeError) as exc:
        raise ValueError(str(exc)) from exc


def resolve_base_commit(root: Path, branch: str) -> str:
    if re.fullmatch(r"[0-9a-fA-F]{40,64}", branch):
        pinned = run(["git", "rev-parse", "--verify", f"{branch}^{{commit}}"], root)
        if pinned.returncode == 0:
            return pinned.stdout.strip()
        raise RuntimeError("Pinned base commit is no longer available in this repository")
    local_ref = f"refs/heads/{branch}"
    remote_ref = f"refs/remotes/origin/{branch}"
    refs: dict[str, str] = {}
    for key, ref in (("local", local_ref), ("remote", remote_ref)):
        result = run(["git", "rev-parse", "--verify", f"{ref}^{{commit}}"], root)
        if result.returncode == 0:
            refs[key] = result.stdout.strip()
    if not refs:
        raise RuntimeError(f"Base branch '{branch}' is not available locally; fetch it before running tasks")
    if "local" not in refs:
        return refs["remote"]
    if "remote" not in refs or refs["local"] == refs["remote"]:
        return refs["local"]
    compare = run(["git", "rev-list", "--left-right", "--count",
                   f"{local_ref}...{remote_ref}"], root)
    if compare.returncode != 0:
        raise RuntimeError(f"Could not compare local and origin refs for '{branch}'")
    try:
        local_only, remote_only = (int(value) for value in compare.stdout.split())
    except (TypeError, ValueError):
        raise RuntimeError(f"Could not read local/origin divergence for '{branch}'")
    if local_only and remote_only:
        raise RuntimeError(f"Local and origin branches '{branch}' have diverged. Reconcile them, then retry.")
    return refs["local"] if local_only else refs["remote"]


def list_repository_branches(repo: dict) -> list[dict]:
    root = Path(repo["path"])
    result = run(["git", "for-each-ref", "--format=%(refname:short)",
                  "refs/heads", "refs/remotes/origin"], root)
    if result.returncode != 0:
        raise RuntimeError("Could not list repository branches")
    local: set[str] = set()
    remote: set[str] = set()
    for raw in result.stdout.splitlines():
        name = raw.strip()
        if name.startswith("origin/"):
            name = name.removeprefix("origin/")
            if name and name != "HEAD":
                remote.add(name)
        elif name:
            local.add(name)
    names = local | remote
    return [{"name": name, "local": name in local, "remote": name in remote}
            for name in sorted(names)]


def create_worktree(repo: dict, task_id: str, base_ref: str, title: str) -> tuple[str, str]:
    root = Path(repo["path"])
    # Resolve the requested branch as a commit before creating a branch. Local dirt is never copied.
    base = resolve_base_commit(root, base_ref)
    slug = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", slug.lower()).strip("-")[:52].rstrip("-") or "task"
    root_branch = f"orchestrator/{slug}"
    worktree = db.DATA_ROOT / "worktrees" / repo["id"] / task_id
    if worktree.exists() or worktree.is_symlink():
        if worktree.is_symlink() or not worktree.is_dir():
            raise RuntimeError("Existing task worktree path is not a managed directory")
        worktree_root = run(["git", "rev-parse", "--show-toplevel"], worktree)
        branch_result = run(["git", "branch", "--show-current"], worktree)
        registered = run(["git", "worktree", "list", "--porcelain"], root)
        registered_path = f"worktree {worktree.resolve()}"
        if (registered.returncode != 0 or registered_path not in registered.stdout.splitlines()
                or worktree_root.returncode != 0 or Path(worktree_root.stdout.strip()).resolve() != worktree.resolve()
                or branch_result.returncode != 0 or not branch_result.stdout.strip().startswith("orchestrator/")):
            raise RuntimeError("Existing task worktree failed ownership checks")
        return branch_result.stdout.strip(), str(worktree)

    branch = root_branch
    suffix = 2
    while any(run(["git", "show-ref", "--verify", "--quiet", ref], root).returncode == 0
              for ref in (f"refs/heads/{branch}", f"refs/remotes/origin/{branch}")):
        branch = f"{root_branch}-{suffix}"
        suffix += 1
    worktree.parent.mkdir(parents=True, exist_ok=True)
    require_ok(["git", "worktree", "add", "-b", branch, str(worktree), base], root, timeout=120)
    return branch, str(worktree)


def changed_paths(worktree: str, base_branch: str | None = None) -> list[str]:
    """Return the union of all task commits and current index/worktree changes."""
    values: set[str] = set()
    if base_branch:
        committed = run(["git", "diff", f"{base_branch}...HEAD", "--name-only", "-z"], worktree)
        if committed.returncode != 0:
            raise RuntimeError("Could not inspect committed task changes against the base branch")
        values.update(p for p in committed.stdout.split("\0") if p)

    for args in (["git", "diff", "HEAD", "--name-only", "-z"],
                 ["git", "diff", "--cached", "--name-only", "-z"],
                 ["git", "ls-files", "--others", "--exclude-standard", "-z"]):
        result = run(args, worktree)
        if result.returncode != 0:
            raise RuntimeError("Could not inspect all task changes")
        values.update(p for p in result.stdout.split("\0") if p)
    return sorted(values)


def task_diff(worktree: str, commit: str, max_chars: int = 250_000,
              base_branch: str | None = None, offset: int = 0) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        raise ValueError("Task commit reference is invalid")
    args = ["git", "diff", "--no-ext-diff", "--no-color", "--unified=40"]
    if base_branch:
        args.append(f"{base_branch}...{commit}")
    else:
        args.extend([f"{commit}^", commit])
    result = run(args, worktree, timeout=30)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Could not read task diff").strip()[-1000:])
    value = result.stdout
    offset = max(0, min(offset, len(value)))
    end = min(len(value), offset + max_chars)
    return {"diff": value[offset:end], "truncated": end < len(value), "offset": offset,
            "next_offset": end if end < len(value) else None, "total_chars": len(value)}


def validated_revision_is_current(worktree: str, validated_head: str | None) -> bool:
    """Reject review/publication when the worktree changed after validation."""
    if not validated_head:
        return False
    head = run(["git", "rev-parse", "HEAD"], worktree)
    if head.returncode != 0 or head.stdout.strip() != validated_head:
        return False
    status = run(["git", "status", "--porcelain", "--untracked-files=all"], worktree)
    return status.returncode == 0 and not status.stdout.strip()


def git_author(worktree: str) -> tuple[str, str]:
    """Use repo-local identity first, then the user's global Git identity, then a safe fallback."""
    def configured(scope: str, key: str) -> str | None:
        result = run(["git", "config", scope, "--get", key], worktree)
        value = result.stdout.strip()
        return value if result.returncode == 0 and value else None

    name = configured("--local", "user.name") or configured("--global", "user.name")
    email = configured("--local", "user.email") or configured("--global", "user.email")
    return (name or "DevAgent Orchestrator", email or "orchestrator@localhost")


def is_github_remote(remote: str | None) -> bool:
    if not remote:
        return False
    value = remote.strip()
    host = urlparse(value).hostname
    if host is None:
        scp_style = re.match(r"^(?:[^@/]+@)?([^:/]+):.+$", value)
        host = scp_style.group(1) if scp_style else None
    return (host or "").lower() == "github.com"


def commit_changes(worktree: str, paths: list[str], task_id: str, title: str, base_branch: str | None = None) -> str:
    if not paths:
        raise RuntimeError("Agent completed without producing a change")
    
    # Check if working tree has unstaged or staged changes to commit
    status_check = run(["git", "status", "--porcelain"], worktree)
    has_uncommitted = bool(status_check.stdout.strip())
    
    if has_uncommitted:
        require_ok(["git", "add", "--", *paths], worktree)
        safe_title = re.sub(r"[\r\n]+", " ", title).strip()[:60]
        author_name, author_email = git_author(worktree)
        result = run(["git", "-c", f"user.name={author_name}", "-c", f"user.email={author_email}",
                      "commit", "-m", f"task: {safe_title} ({task_id[:8]})"], worktree, timeout=120)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout).strip()[-3000:])
    else:
        # Agent already committed its changes directly; verify HEAD differs from base
        head_commit = require_ok(["git", "rev-parse", "HEAD"], worktree)
        if base_branch:
            ancestor = run(["git", "merge-base", "--is-ancestor", base_branch, head_commit], worktree)
            if ancestor.returncode != 0:
                raise RuntimeError("Agent completed without producing a change")
    return require_ok(["git", "rev-parse", "HEAD"], worktree)


def push_and_open_draft(repo: dict, worktree: str, branch: str, base_branch: str,
                        title: str, body: str) -> str:
    if not repo.get("remote"):
        raise RuntimeError("Repository has no origin remote configured")
    if not is_github_remote(repo.get("remote")):
        raise RuntimeError("Draft PR publishing is currently supported only for GitHub remotes; no push was attempted")
    base_check = run(["git", "ls-remote", "--exit-code", "--heads", "origin",
                      f"refs/heads/{base_branch}"], worktree, timeout=30)
    if base_check.returncode != 0:
        raise RuntimeError(f"Base branch '{base_branch}' is not present on origin. Push that branch before approving a Draft PR; no task branch was pushed.")
    require_ok(["git", "push", "--set-upstream", "origin", branch], worktree, timeout=300)
    result = run(["gh", "pr", "create", "--draft", "--base", base_branch,
                  "--head", branch, "--title", title[:240], "--body", body], worktree, timeout=180)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip()[-3000:])
    return result.stdout.strip().splitlines()[-1]


def check_repository_sync(repo: dict, target_branch: str | None = None) -> dict:
    root = Path(repo["path"])
    current = run(["git", "branch", "--show-current"], root).stdout.strip()
    branch = target_branch or current or repo.get("default_branch", "main")

    ahead = 0
    behind = 0
    remote_ref = f"origin/{branch}"
    compare = run(["git", "rev-list", "--left-right", "--count", f"{branch}...{remote_ref}"], root)
    if compare.returncode == 0:
        try:
            parts = compare.stdout.strip().split()
            if len(parts) == 2:
                ahead, behind = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            pass

    unmerged = []
    unmerged_res = run(["git", "branch", "--no-merged", branch], root)
    if unmerged_res.returncode == 0:
        for line in unmerged_res.stdout.splitlines():
            b = line.strip().removeprefix("* ").removeprefix("+ ").strip()
            if b and b.startswith("orchestrator/"):
                unmerged.append(b)

    return {
        "repository_id": repo["id"],
        "repository_name": repo["name"],
        "current_branch": current,
        "target_branch": branch,
        "remote": repo.get("remote"),
        "ahead_of_remote": ahead,
        "behind_remote": behind,
        "unmerged_task_branches_count": len(unmerged),
        "unmerged_task_branches": unmerged[:50],
    }


