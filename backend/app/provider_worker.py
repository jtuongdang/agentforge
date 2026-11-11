"""Explicitly selected BYOK workers with a bounded, filesystem-only tool surface."""
from __future__ import annotations

import asyncio
import json
import os
import stat
import tempfile
import time
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import httpx

from . import db, providers
from .validation import SECRET_PATTERNS, in_scope

REQUEST_TIMEOUT_SECONDS = 60
MAX_RESPONSE_BYTES = 2_000_000
MAX_REQUEST_BYTES = 4_000_000
MAX_FILE_BYTES = 256_000
MAX_TOTAL_READ_BYTES = 2_000_000
MAX_LIST_WALK_ENTRIES = 10_000
MAX_TOOL_CALLS = 60
MAX_TURNS = 24
MAX_RUNTIME_SECONDS = 30 * 60
_SENSITIVE_NAMES = {"credentials.json", "secrets.json", "service-account.json", "id_rsa", "id_ed25519"}
_SENSITIVE_SUFFIXES = {".pem", ".p12", ".pfx", ".key", ".keystore"}

_TOOL_PARAMETERS = {
    "list_files": {
        "type": "object", "properties": {"directory": {"type": "string"}}, "additionalProperties": False,
    },
    "read_file": {
        "type": "object", "properties": {"path": {"type": "string"}},
        "required": ["path"], "additionalProperties": False,
    },
    "write_file": {
        "type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"], "additionalProperties": False,
    },
}
_TOOL_DESCRIPTIONS = {
    "list_files": "List readable, non-secret files inside the task's allowed file globs. No shell or network access.",
    "read_file": "Read one UTF-8 text file inside both task and agent file scopes. Secret-bearing and oversized files are blocked.",
    "write_file": "Atomically replace or create one UTF-8 text file inside both task and agent file scopes. No other filesystem writes are available.",
}
_OPENAI_OLLAMA_TOOLS = [
    {"type": "function", "function": {"name": name, "description": _TOOL_DESCRIPTIONS[name],
        "parameters": schema}}
    for name, schema in _TOOL_PARAMETERS.items()
]
_ANTHROPIC_TOOLS = [
    {"name": name, "description": _TOOL_DESCRIPTIONS[name], "input_schema": schema}
    for name, schema in _TOOL_PARAMETERS.items()
]
_GEMINI_TOOLS = [{"functionDeclarations": [
    {"name": name, "description": _TOOL_DESCRIPTIONS[name], "parameters": schema}
    for name, schema in _TOOL_PARAMETERS.items()
]}]


class ProviderWorkerError(RuntimeError):
    pass


class _ScopedFiles:
    def __init__(self, root: str, task_globs: list[str], agent_globs: list[str]):
        self.root = Path(root).resolve(strict=True)
        self.task_globs = task_globs
        self.agent_globs = agent_globs
        self.read_bytes = 0
        self.write_count = 0
        self.list_walk_entries = 0

    def _path(self, raw: str, require_scope: bool = True) -> tuple[str, Path]:
        if not isinstance(raw, str) or not raw or len(raw) > 1000 or "\\" in raw or "\x00" in raw:
            raise ProviderWorkerError("File path is invalid")
        relative = PurePosixPath(raw)
        parts = relative.parts
        if relative.is_absolute() or any(part in {"", ".", "..", ".git"} for part in parts):
            raise ProviderWorkerError("File path is outside the allowed worktree")
        normalized = relative.as_posix()
        if self._sensitive_name(normalized):
            raise ProviderWorkerError("Credential and private-key files are not available to provider workers")
        if require_scope and (not in_scope(normalized, self.task_globs) or not in_scope(normalized, self.agent_globs)):
            raise ProviderWorkerError("File path is outside the task or agent file scope")
        target = self.root.joinpath(*parts)
        current = self.root
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise ProviderWorkerError("Symbolic links are not available to provider workers")
        resolved = target.resolve(strict=False)
        if not resolved.is_relative_to(self.root):
            raise ProviderWorkerError("File path resolves outside the task worktree")
        return normalized, target

    @staticmethod
    def _sensitive_name(path: str) -> bool:
        name = path.rsplit("/", 1)[-1].lower()
        return (name in _SENSITIVE_NAMES or name == ".env" or name.startswith(".env.")
                or Path(name).suffix in _SENSITIVE_SUFFIXES)

    @staticmethod
    def _contains_secret(text: str) -> bool:
        return any(pattern.search(text) for pattern in SECRET_PATTERNS)

    def list_files(self, directory: str = "") -> dict:
        if directory:
            _, base = self._path(directory, require_scope=False)
            if not base.is_dir():
                raise ProviderWorkerError("Directory does not exist")
        else:
            base = self.root
        result = []
        pending = [base]
        truncated = False
        while pending:
            if self.list_walk_entries >= MAX_LIST_WALK_ENTRIES:
                truncated = True
                break
            current = pending.pop()
            self.list_walk_entries += 1
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        if self.list_walk_entries >= MAX_LIST_WALK_ENTRIES:
                            truncated = True
                            pending.clear()
                            break
                        self.list_walk_entries += 1
                        try:
                            if entry.is_symlink():
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                if entry.name != ".git":
                                    pending.append(Path(entry.path))
                                continue
                            if not entry.is_file(follow_symlinks=False):
                                continue
                        except OSError:
                            continue
                        path = Path(entry.path)
                        relative = path.relative_to(self.root).as_posix()
                        if self._sensitive_name(relative):
                            continue
                        if in_scope(relative, self.task_globs) and in_scope(relative, self.agent_globs):
                            result.append(relative)
                            if len(result) >= 500:
                                truncated = True
                                pending.clear()
                                break
            except OSError:
                continue
        return {"files": sorted(result), "truncated": truncated}

    def read_file(self, raw_path: str) -> dict:
        relative, path = self._path(raw_path)
        try:
            info = path.stat()
        except OSError:
            raise ProviderWorkerError("File could not be read") from None
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
            raise ProviderWorkerError("Only regular text files up to 256 KB can be read")
        if self.read_bytes + info.st_size > MAX_TOTAL_READ_BYTES:
            raise ProviderWorkerError("Provider worker reached the 2 MB total file-read limit")
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise ProviderWorkerError("File is unreadable or not UTF-8 text") from None
        if self._contains_secret(content):
            raise ProviderWorkerError("Secret scanner blocked this file from provider access")
        self.read_bytes += len(content.encode("utf-8"))
        return {"path": relative, "content": content}

    def write_file(self, raw_path: str, content: str) -> dict:
        relative, path = self._path(raw_path)
        if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_FILE_BYTES:
            raise ProviderWorkerError("File content must be UTF-8 text no larger than 256 KB")
        if self._contains_secret(content):
            raise ProviderWorkerError("Secret scanner blocked a provider-generated credential-like value")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and (path.is_symlink() or not path.is_file()):
            raise ProviderWorkerError("Only regular files can be replaced")
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                             prefix=".provider-write-", delete=False) as stream:
                temp_path = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp_path, mode)
            os.replace(temp_path, path)
        except OSError:
            if temp_path:
                temp_path.unlink(missing_ok=True)
            raise ProviderWorkerError("Atomic file write failed") from None
        self.write_count += 1
        return {"path": relative, "written": True}

    def call(self, name: str, arguments: dict) -> dict:
        if not isinstance(arguments, dict):
            raise ProviderWorkerError("Tool arguments must be a JSON object")
        if name == "list_files":
            if set(arguments) - {"directory"}:
                raise ProviderWorkerError("Unknown list_files argument")
            return self.list_files(arguments.get("directory", ""))
        if name == "read_file":
            if set(arguments) != {"path"}:
                raise ProviderWorkerError("read_file requires only a path")
            return self.read_file(arguments["path"])
        if name == "write_file":
            if set(arguments) != {"path", "content"}:
                raise ProviderWorkerError("write_file requires only a path and content")
            return self.write_file(arguments["path"], arguments["content"])
        raise ProviderWorkerError("Unknown provider tool")


def _provider_request_details(connection_id: str) -> tuple[dict, str | None]:
    current = providers._raw_connection(connection_id)
    if current is None:
        raise ProviderWorkerError("Provider connection was removed")
    current = dict(current)
    if current["state"] != "valid" or not current["model"] or current["model"] not in json.loads(current["models"]):
        raise ProviderWorkerError("Provider worker is not verified with a supported model")
    api_key = None
    if current["provider"] != "ollama":
        if not current["secret_ref"]:
            raise ProviderWorkerError("Provider credential is unavailable")
        try:
            api_key = providers._keyring().get_password(providers.SERVICE_NAME, current["secret_ref"])
        except Exception:
            raise ProviderWorkerError("Provider credential store could not be read") from None
        if not api_key:
            raise ProviderWorkerError("Provider credential is missing from the OS credential store")
    return current, api_key


def _request_url(provider: str, endpoint: str, model: str) -> str:
    if provider == "openai":
        return "https://api.openai.com/v1/chat/completions"
    if provider == "anthropic":
        return "https://api.anthropic.com/v1/messages"
    if provider == "gemini":
        return f"https://generativelanguage.googleapis.com/v1beta/models/{quote(model, safe='')}:generateContent"
    base = (endpoint or "http://127.0.0.1:11434").rstrip("/")
    providers._ollama_url(base)
    return base + "/api/chat"


async def _post_json(client: httpx.AsyncClient, url: str, headers: dict[str, str], payload: dict) -> dict:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_REQUEST_BYTES:
        raise ProviderWorkerError("Provider request exceeded the 4 MB safety limit")
    try:
        async with client.stream("POST", url, headers=headers, content=body) as response:
            if response.status_code >= 300:
                raise ProviderWorkerError(f"Provider request failed with HTTP {response.status_code}")
            chunks = bytearray()
            async for chunk in response.aiter_bytes():
                chunks.extend(chunk)
                if len(chunks) > MAX_RESPONSE_BYTES:
                    raise ProviderWorkerError("Provider response exceeded the 2 MB safety limit")
    except ProviderWorkerError:
        raise
    except httpx.TimeoutException:
        raise ProviderWorkerError("Provider request timed out") from None
    except httpx.HTTPError as exc:
        raise ProviderWorkerError(f"Provider request failed ({type(exc).__name__})") from None
    try:
        result = json.loads(chunks)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ProviderWorkerError("Provider returned an invalid response") from None
    if not isinstance(result, dict):
        raise ProviderWorkerError("Provider returned an invalid response")
    return result


def _tool_output(files: _ScopedFiles, name: str, arguments: object, on_tool) -> str:
    try:
        parsed = arguments if isinstance(arguments, dict) else json.loads(arguments) if isinstance(arguments, str) else None
        result = files.call(name, parsed)
    except (ProviderWorkerError, json.JSONDecodeError) as exc:
        result = {"error": str(exc)}
    path = result.get("path") or (arguments.get("path") if isinstance(arguments, dict) else None)
    if on_tool:
        on_tool(name, path if isinstance(path, str) else None, bool(result.get("written")))
    return json.dumps(result, ensure_ascii=False)[:MAX_FILE_BYTES]


def _function_calls(provider: str, response: dict) -> tuple[list[dict], object, bool]:
    if provider in {"openai", "ollama"}:
        message = response.get("choices", [{}])[0].get("message", {})
        calls = []
        for call in message.get("tool_calls", []) or []:
            fn = call.get("function", {})
            calls.append({"id": call.get("id", ""), "name": fn.get("name"), "arguments": fn.get("arguments", "{}")})
        return calls, message, not calls
    if provider == "anthropic":
        content = response.get("content", [])
        calls = [{"id": item.get("id", ""), "name": item.get("name"), "arguments": item.get("input", {})}
                 for item in content if isinstance(item, dict) and item.get("type") == "tool_use"]
        return calls, content, response.get("stop_reason") != "tool_use"
    candidates = response.get("candidates", [])
    if not candidates:
        raise ProviderWorkerError("Gemini returned no candidate")
    content = candidates[0].get("content", {})
    parts = content.get("parts", []) if isinstance(content, dict) else []
    calls = []
    for part in parts:
        call = part.get("functionCall") if isinstance(part, dict) else None
        if isinstance(call, dict):
            calls.append({"id": call.get("id", ""), "name": call.get("name"), "arguments": call.get("args", {})})
    return calls, content, not calls


def _append_turn(provider: str, history: list, model_turn: object, results: list[tuple[dict, str]]) -> None:
    if provider in {"openai", "ollama"}:
        history.append(model_turn)
        for call, output in results:
            history.append({"role": "tool", "tool_call_id": call["id"], "content": output})
    elif provider == "anthropic":
        history.append({"role": "assistant", "content": model_turn})
        if results:
            history.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": call["id"], "content": output}
                for call, output in results
            ]})
    else:
        history.append(model_turn)
        if results:
            history.append({"role": "user", "parts": [
                {"functionResponse": {"name": call["name"], "id": call["id"],
                                       "response": {"result": json.loads(output)}}}
                for call, output in results
            ]})


async def execute(connection_id: str, prompt: str, worktree: str, task_globs: list[str], agent_globs: list[str],
                  on_tool=None) -> int:
    """Run one explicitly selected provider worker; return a subprocess-like exit code."""
    if len(prompt) > 500_000:
        raise ProviderWorkerError("Provider prompt exceeded the 500 KB safety limit")
    if not task_globs or not agent_globs:
        raise ProviderWorkerError("Provider worker requires non-empty task and agent file scopes")
    connection, api_key = _provider_request_details(connection_id)
    provider, model = connection["provider"], connection["model"]
    url = _request_url(provider, connection["endpoint"], model)
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if provider == "openai":
        headers["Authorization"] = f"Bearer {api_key}"
    elif provider == "anthropic":
        headers.update({"x-api-key": api_key or "", "anthropic-version": "2023-06-01"})
    elif provider == "gemini":
        headers["x-goog-api-key"] = api_key or ""
    files = _ScopedFiles(worktree, task_globs, agent_globs)
    system = (
        "You are a coding worker in an isolated Git worktree. Use only the supplied list_files, read_file, and write_file tools. "
        "There is no shell, command execution, network, or unrestricted filesystem tool. Read project guidance and relevant source "
        "before editing. Make only changes within the declared task scope. Never request or write credentials, secrets, or private keys. "
        "The Orchestrator runs repository validation after you finish. When implementation is complete, stop calling tools and give a concise summary."
    )
    if provider in {"openai", "ollama"}:
        history: list = ([{"role": "system", "content": system}, {"role": "user", "content": prompt}]
                         if provider == "openai" else [{"role": "system", "content": system},
                                                       {"role": "user", "content": prompt}])
        tool_specs = _OPENAI_OLLAMA_TOOLS
    elif provider == "anthropic":
        history = [{"role": "user", "content": prompt}]
        tool_specs = _ANTHROPIC_TOOLS
    else:
        history = [{"role": "user", "parts": [{"text": prompt}]}]
        tool_specs = _GEMINI_TOOLS
    client = httpx.AsyncClient(timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS), follow_redirects=False,
                               trust_env=provider != "ollama")
    deadline = time.monotonic() + MAX_RUNTIME_SECONDS
    total_tool_calls = 0
    try:
        async with client:
            for _ in range(MAX_TURNS):
                if time.monotonic() >= deadline:
                    raise ProviderWorkerError("Provider worker exceeded the 30 minute runtime limit")
                if provider == "openai":
                    payload = {"model": model, "messages": history, "tools": tool_specs,
                               "tool_choice": "auto", "parallel_tool_calls": False}
                    response = await _post_json(client, url, headers, payload)
                elif provider == "ollama":
                    payload = {"model": model, "messages": history, "tools": tool_specs, "stream": False}
                    response = await _post_json(client, url, headers, payload)
                elif provider == "anthropic":
                    payload = {"model": model, "max_tokens": 8192, "system": system,
                               "messages": history, "tools": tool_specs, "tool_choice": {"type": "auto"}}
                    response = await _post_json(client, url, headers, payload)
                else:
                    payload = {"systemInstruction": {"parts": [{"text": system}]}, "contents": history,
                               "tools": tool_specs, "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}}}
                    response = await _post_json(client, url, headers, payload)
                calls, model_turn, _finished = _function_calls(provider, response)
                if not calls:
                    return 0 if files.write_count else 65
                results = []
                for call in calls:
                    total_tool_calls += 1
                    if total_tool_calls > MAX_TOOL_CALLS:
                        raise ProviderWorkerError("Provider worker exceeded the 60 tool-call limit")
                    name = call.get("name")
                    if not isinstance(name, str):
                        raise ProviderWorkerError("Provider returned an invalid tool call")
                    output = _tool_output(files, name, call.get("arguments", {}), on_tool)
                    results.append((call, output))
                _append_turn(provider, history, model_turn, results)
    finally:
        await client.aclose()
    return 124
