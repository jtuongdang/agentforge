from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
import asyncio
from pathlib import Path
from .processes import (create_managed_process, process_group_options, terminate_process_tree,
                        wait_for_process_exit)

SECRET_PATTERNS = [
    # Match literal values assigned to credential-like keys. Do not treat normal
    # source expressions such as ``password = command.passwordCopy()`` as secrets.
    re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key)\b\s*[=:]\s*(?:\"[^\"\r\n]{8,}\"|'[^'\r\n]{8,}'|[A-Za-z0-9_+/=-]{16,})(?=\s*(?:[,;#]|$))"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
]
VALIDATION_OUTPUT_REDACTIONS = [
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key)(\s*[=:]\s*)[^\s'\"]{8,}"), r"\1\2[REDACTED]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
]


class ValidationEngine:
    def inspect_scope(self, paths: list[str], task_globs: list[str], agent_globs: list[str]) -> list[str]:
        return sorted(set(check_scope(paths, task_globs) + check_scope(paths, agent_globs)))

    def inspect_secrets(self, worktree: str, paths: list[str]) -> list[str]:
        return check_secrets(worktree, paths)

    async def validate(self, worktree: str, log, profile: str = "auto",
                       command: list[str] | None = None,
                       paths: list[str] | None = None) -> dict:
        if profile in {"custom", "node", "python", "go"}:
            if command:
                return await run_configured_validation(worktree, command, log)
            return {"passed": False, "blocked": True, "profile": profile, "command": None,
                    "exit_code": None, "summary": "This validation profile has no configured argv command."}
        documentation_suffixes = (".md", ".txt", ".rst")
        if paths and all(Path(p).suffix.lower() in documentation_suffixes for p in paths):
            log("validation", "All changed files are documentation; project build validation was skipped.")
            return {"passed": True, "blocked": False, "profile": "docs_skip", "command": None,
                    "exit_code": 0, "summary": "Documentation-only change passed the configured docs policy."}
        if profile in {"auto", "maven"} and (Path(worktree) / "pom.xml").is_file():
            return await run_maven_verify(worktree, log)
        return {"passed": False, "blocked": True, "profile": "unsupported", "command": None,
                "exit_code": None, "summary": "No validation profile is configured for this repository. "
                "Auto and Maven profiles support projects with a root pom.xml; configure a reviewed argv command for other stacks."}

    async def maven_verify(self, worktree: str, log) -> dict:
        """Compatibility entry point for callers that explicitly require Maven."""
        return await run_maven_verify(worktree, log)


def in_scope(path: str, globs: list[str]) -> bool:
    normalized = path.replace("\\", "/")
    if normalized.startswith("/") or ".." in normalized.split("/") or ".git" in normalized.split("/"):
        return False
    normalized = normalized.removeprefix("./")
    for pattern in globs:
        if pattern in {"**", "**/*"} or fnmatch.fnmatchcase(normalized, pattern):
            return True
        if pattern.startswith("**/") and fnmatch.fnmatchcase(normalized, pattern[3:]):
            return True
        optional_recursive = pattern
        while "/**/" in optional_recursive:
            optional_recursive = optional_recursive.replace("/**/", "/", 1)
            if fnmatch.fnmatchcase(normalized, optional_recursive):
                return True
    return False


def check_scope(paths: list[str], globs: list[str]) -> list[str]:
    return [path for path in paths if not in_scope(path, globs)]


def scopes_may_overlap(first: list[str], second: list[str]) -> bool:
    """Conservatively report overlap; false is returned only for clearly separate literal prefixes."""
    wildcard_chars = set("*?[{")

    def prefix(pattern: str) -> str:
        normalized = pattern.replace("\\", "/").removeprefix("./")
        index = next((i for i, char in enumerate(normalized) if char in wildcard_chars), len(normalized))
        return normalized[:index].rstrip("/")

    for left in first:
        left_prefix = prefix(left)
        for right in second:
            right_prefix = prefix(right)
            if not left_prefix or not right_prefix:
                return True
            if (left_prefix == right_prefix or left_prefix.startswith(right_prefix + "/")
                    or right_prefix.startswith(left_prefix + "/")):
                return True
    return False


def check_secrets(worktree: str, paths: list[str]) -> list[str]:
    hits: list[str] = []
    for relative in paths:
        path = Path(worktree) / relative
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeError, OSError):
            continue
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            hits.append(relative)
    return hits


def redact_validation_output(value: str) -> str:
    for pattern, replacement in VALIDATION_OUTPUT_REDACTIONS:
        value = pattern.sub(replacement, value)
    return value[:4000]


async def run_configured_validation(worktree: str, command: list[str], log) -> dict:
    if not command or not command[0].strip():
        return {"passed": False, "blocked": True, "profile": "custom", "command": None,
                "exit_code": None, "summary": "Custom validation command is empty."}
    log("validation", "Running the repository's configured validation command (argv, without a shell).")
    try:
        proc = await create_managed_process(*command, cwd=worktree,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=os.environ.copy(),
            **process_group_options())
    except OSError:
        return {"passed": False, "blocked": True, "profile": "custom", "command": "configured project command",
                "exit_code": None, "summary": "Could not start the configured validation executable."}
    chunks: list[str] = []
    captured_chars = 0
    truncated = False

    async def stream():
        nonlocal captured_chars, truncated
        assert proc.stdout is not None
        while line := await proc.stdout.readline():
            clean = redact_validation_output(line.decode("utf-8", errors="replace").rstrip())
            if clean and captured_chars < 300_000:
                chunks.append(clean)
                captured_chars += len(clean)
            elif clean:
                truncated = True
            if clean:
                log("build", clean[-1500:])

    reader = asyncio.create_task(stream())
    try:
        code = await asyncio.wait_for(wait_for_process_exit(proc), timeout=1200)
        await terminate_process_tree(proc, grace_seconds=0.25)
        await reader
    except asyncio.TimeoutError:
        await terminate_process_tree(proc)
        reader.cancel()
        return {"passed": False, "blocked": False, "profile": "custom", "command": "configured project command",
                "exit_code": 124, "summary": "Configured validation exceeded the 20 minute limit."}
    except asyncio.CancelledError:
        await terminate_process_tree(proc)
        reader.cancel()
        raise
    summary = "Configured project validation passed." if code == 0 else "Configured project validation failed."
    tail = "\n".join(chunks[-100:])
    if truncated:
        tail += "\n[validation output truncated]"
    return {"passed": code == 0, "blocked": False, "profile": "custom", "command": "configured project command",
            "exit_code": code, "summary": summary, "tail": tail}


async def run_maven_verify(worktree: str, log) -> dict:
    mvn = shutil.which("mvn")
    wrapper = Path(worktree) / "mvnw"
    if mvn:
        command = [mvn, "clean", "verify"]
    elif wrapper.is_file():
        command = [str(wrapper), "clean", "verify"]
    else:
        return {"passed": False, "blocked": True, "command": "mvn clean verify", "exit_code": None,
                "summary": "Maven and Maven Wrapper are unavailable on this machine."}
    log("validation", f"Running {' '.join(command[-2:])}")
    env = {**os.environ, "MAVEN_OPTS": os.getenv("MAVEN_OPTS", "-Xmx1g")}
    try:
        proc = await create_managed_process(*command, cwd=worktree,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env,
            **process_group_options())
        chunks: list[str] = []
        async def stream():
            assert proc.stdout is not None
            while line := await proc.stdout.readline():
                clean = line.decode("utf-8", errors="replace").rstrip()
                if clean:
                    clean = redact_validation_output(clean)
                    chunks.append(clean)
                    log("build", clean[-1500:])
        reader = asyncio.create_task(stream())
        try:
            code = await asyncio.wait_for(wait_for_process_exit(proc), timeout=1200)
            await terminate_process_tree(proc, grace_seconds=0.25)
            await reader
        except asyncio.TimeoutError:
            await terminate_process_tree(proc)
            reader.cancel()
            return {"passed": False, "blocked": False, "command": "mvn clean verify", "exit_code": 124,
                    "summary": "Maven validation exceeded the 20 minute limit."}
        except asyncio.CancelledError:
            await terminate_process_tree(proc)
            reader.cancel()
            raise
    except OSError as exc:
        return {"passed": False, "blocked": True, "command": "mvn clean verify", "exit_code": None,
                "summary": f"Could not start Maven: {exc}"}
    summary = "Maven clean verify passed." if code == 0 else "Maven clean verify failed."
    return {"passed": code == 0, "blocked": False, "command": "mvn clean verify", "exit_code": code,
            "summary": summary, "tail": "\n".join(chunks[-30:])}

