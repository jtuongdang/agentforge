from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


@dataclass(frozen=True)
class Failure:
    category: str
    retryable: bool
    retry_after_seconds: float | None = None
    reason: str = ""


class RecoveryManager:
    """Classify worker failures and apply a small, bounded retry budget."""

    MAX_RETRY_AFTER_SECONDS = 60.0
    MAX_BACKOFF_SECONDS = 30.0

    @classmethod
    def classify(cls, exit_code: int | None, output: list[str] | None = None,
                 validation: dict | None = None, error: str = "") -> Failure:
        text = "\n".join([error, *(output or [])]).lower()
        if validation and validation.get("blocked"):
            return Failure("blocked", False, reason=str(validation.get("reason", "safety gate")))
        if any(token in text for token in ("401", "403", "unauthorized", "authentication failed", "invalid api key", "login required")):
            return Failure("auth", False, reason="Provider authentication needs attention")
        retry_after = cls._retry_after(text)
        if any(token in text for token in ("429", "rate limit", "rate_limit", "too many requests", "quota exceeded")):
            return Failure("rate_limit", True, retry_after, "Provider rate limit")
        if exit_code == 124 or any(token in text for token in ("timed out", "timeout", "deadline exceeded")):
            return Failure("timeout", True, retry_after, "Worker exceeded its time limit")
        if any(token in text for token in ("connection reset", "connection refused", "network error", "temporarily unavailable", "502", "503", "504")):
            return Failure("network", True, retry_after, "Transient provider/network failure")
        if validation and not validation.get("passed", False):
            return Failure("build", True, retry_after, "Validation failed; repair may be attempted")
        if exit_code not in (None, 0):
            return Failure("worker_exit", True, retry_after, f"Worker exited with status {exit_code}")
        return Failure("unknown", False, reason="No retryable failure was identified")

    @classmethod
    def backoff_seconds(cls, attempt: int, retry_after_seconds: float | None = None) -> float:
        exponential = min(cls.MAX_BACKOFF_SECONDS, 2.0 ** max(0, attempt))
        requested = min(cls.MAX_RETRY_AFTER_SECONDS, max(0.0, retry_after_seconds or 0.0))
        return max(exponential, requested)

    @staticmethod
    def _retry_after(text: str) -> float | None:
        match = re.search(r"retry[- ]after\s*[:=]?\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
        if match:
            return float(match.group(1))
        match = re.search(r"retry[- ]after\s*[:=]?\s*([a-z]{3},[^\n]+)", text, re.IGNORECASE)
        if match:
            try:
                target = parsedate_to_datetime(match.group(1).strip())
                if target.tzinfo is None:
                    target = target.replace(tzinfo=timezone.utc)
                return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                return None
        return None

    @staticmethod
    def should_retry(attempt: int, max_retries: int, blocked: bool) -> bool:
        return not blocked and attempt < max_retries
