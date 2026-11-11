from __future__ import annotations

from .engine import AgentExecutionEngine
from .github_pr import GitHubPullRequestManager
from .registry import AgentRegistry
from .reporting import ReportingManager
from .scheduler import TaskScheduler
from .validation import ValidationEngine
from .worktrees import GitWorktreeManager


class Orchestrator:
    """Composition root for scheduling, execution, validation, reporting and safe GitHub publication."""

    def __init__(self):
        self.registry = AgentRegistry()
        self.worktrees = GitWorktreeManager()
        self.validation = ValidationEngine()
        self.execution = AgentExecutionEngine(self.registry, self.worktrees, self.validation)
        self.scheduler = TaskScheduler(self.execution)
        self.github = GitHubPullRequestManager()
        self.reporting = ReportingManager()

    async def start(self):
        await self.scheduler.start()

    async def stop(self):
        await self.scheduler.stop()
