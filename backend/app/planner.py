from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class PlannedTask:
    title: str
    objective: str
    file_globs: list[str]
    required_capabilities: list[str]


class TaskPlanner:
    """Turns an operator's bounded task into an execution charter; it does not invent agent roles."""

    def plan(self, title: str, objective: str, file_globs: list[str], capabilities: list[str],
             acceptance_criteria: list[str] | None = None, guidance: str = "",
             guidance_files: list[str] | None = None) -> PlannedTask:
        paths = [str(Path(p)) for p in file_globs]
        criteria = acceptance_criteria or []
        charter = (
            f"Objective: {objective.strip()}\n\n"
            + ("Acceptance criteria:\n" + "\n".join(f"- {item}" for item in criteria) + "\n\n" if criteria else "")
            + "Execution checklist:\n"
            "1. Read the Mandatory project guidance supplied below before editing, then inspect relevant existing implementation.\n"
            "2. Agree with the declared contract before changing code; do not edit outside the allowed paths.\n"
            "3. Implement the smallest complete change and add or update focused tests using synthetic data only.\n"
            "4. Check each acceptance criterion and report pass/fail evidence. Run relevant checks when the sandbox permits them; report exact results and blockers. The Orchestrator runs its own validation after the agent.\n"
            "5. Never copy, move, or delete shared local dependency caches to work around sandbox boundaries.\n"
            "6. Do not push, merge, force-push, delete branches, or connect to production databases or systems.\n\n"
            f"Allowed file globs: {', '.join(paths)}\n"
            f"Required capabilities: {', '.join(capabilities) if capabilities else '(none)'}\n"
            + ("\n" + guidance + "\n" if guidance else "")
            + ("\nGuidance files supplied: " + ", ".join(guidance_files) + "\n" if guidance_files else "")
            + "Completion report must list guidance files read.\n"
        )
        return PlannedTask(title.strip(), charter, paths, sorted(set(capabilities)))
