from __future__ import annotations

from .git_ops import push_and_open_draft


class GitHubPullRequestManager:
    def create_draft_after_approval(self, repository: dict, worktree: str, branch: str,
                                    base_branch: str, title: str, body: str) -> str:
        return push_and_open_draft(repository, worktree, branch, base_branch, title, body)
