from __future__ import annotations


class RecoveryManager:
    """Defines a small deterministic retry budget; blocked safety checks never retry automatically."""

    @staticmethod
    def should_retry(attempt: int, max_retries: int, blocked: bool) -> bool:
        return not blocked and attempt < max_retries
