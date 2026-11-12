"""Bounded, local-only official CLI login jobs. Never retain raw CLI output."""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import time
import webbrowser

from . import providers
from .processes import create_managed_process, terminate_process_tree


class AgentLogin:
    TTL_SECONDS = 300

    def __init__(self):
        self.job: dict | None = None
        self.task: asyncio.Task | None = None
        self._starting = False
        self._cancel_generation = 0

    @staticmethod
    def command(provider: str) -> list[str]:
        commands = {
            "codex": ["codex", "-c", 'cli_auth_credentials_store="keyring"', "login"],
            "copilot": ["copilot", "login", "--web-flow"],
            "github": ["gh", "auth", "login", "--hostname", "github.com", "--web", "--skip-ssh-key"],
        }
        if provider not in commands:
            raise ValueError("This provider has no supported automatic CLI login flow")
        command = commands[provider].copy()
        executable = shutil.which(command[0])
        if not executable:
            raise ValueError("Install the official CLI before starting login")
        command[0] = executable
        return command

    def status(self) -> dict:
        if self.job and time.monotonic() - self.job["started"] >= self.TTL_SECONDS + 30:
            return {"state": "idle"}
        return {key: value for key, value in (self.job or {"state": "idle"}).items() if key != "started"}

    async def start(self, provider: str, acknowledge_storage_risk: bool = False) -> dict:
        if self._starting:
            raise ValueError("A login is already starting")
        self._starting = True
        try:
            return await self._start(provider, acknowledge_storage_risk)
        finally:
            self._starting = False

    async def _start(self, provider: str, acknowledge_storage_risk: bool) -> dict:
        generation = self._cancel_generation
        if self.task and not self.task.done():
            raise ValueError("A login is already running; finish or cancel it first")
        command = self.command(provider)
        if not providers.credential_store_status()["available"]:
            raise ValueError("OS credential store is unavailable; automatic login is disabled")
        # These CLIs can fall back to their own plaintext store if native storage fails.
        if provider in {"copilot", "github"} and not acknowledge_storage_risk:
            raise ValueError("Acknowledge the CLI credential-storage fallback before continuing")
        overrides = [name for name in ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN") if os.getenv(name)]
        if provider in {"copilot", "github"} and overrides:
            raise ValueError("Environment credentials override browser login: " + ", ".join(overrides))
        # Capability probe is local help, never a paid model request.
        if provider == "copilot":
            def supports_web_flow():
                result = subprocess.run([command[0], "login", "--help"], capture_output=True,
                                        timeout=5, check=False)
                return result.returncode == 0 and b"--web-flow" in result.stdout
            if not await asyncio.to_thread(supports_web_flow):
                raise ValueError("Upgrade Copilot CLI to a version supporting browser login")
        if generation != self._cancel_generation:
            raise ValueError("Login was cancelled while checking CLI capabilities")
        self.job = {"provider": provider, "state": "running", "started": time.monotonic(),
                    "message": "Complete sign-in in the system browser. No model request is sent."}
        self.task = asyncio.create_task(self._run(command, self.job))
        return self.status()

    async def _run(self, command: list[str], job: dict) -> None:
        process = None
        try:
            process = await create_managed_process(*command, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                cwd=os.path.expanduser("~"))
            # gh's browser flow asks for Enter after displaying the device code.
            if job["provider"] == "github":
                process.stdin.write(b"\n")
                await process.stdin.drain()
            process.stdin.close()
            async def consume():
                tail = ""
                while chunk := await process.stdout.read(1024):
                    # Only the fixed-format GitHub one-time code is exposed, never raw URLs/logs.
                    tail = (tail + chunk.decode("utf-8", errors="replace"))[-2048:]
                    if job["provider"] == "github" and not job.get("device_code"):
                        match = re.search(r"(?:one-time code|code:)\s*:?\s*([A-Z0-9]{4}-[A-Z0-9]{4})\b", tail)
                        if match:
                            job["device_code"] = match[1]
                            job["verification_url"] = "https://github.com/login/device"
                            await asyncio.to_thread(webbrowser.open, job["verification_url"])
                return await process.wait()
            result = await asyncio.wait_for(consume(), timeout=self.TTL_SECONDS)
            job.update(state="completed" if result == 0 else "failed",
                message="CLI sign-in completed. Verify the connection separately before running tasks."
                if result == 0 else "CLI sign-in failed. Check browser authorization and CLI credential storage.")
        except asyncio.CancelledError:
            job.update(state="cancelled", message="Login cancelled.")
            raise
        except asyncio.TimeoutError:
            job.update(state="expired", message="Login expired. Start a new sign-in.")
        except Exception:
            job.update(state="failed", message="Could not complete official CLI sign-in.")
        finally:
            job.pop("device_code", None)
            job.pop("verification_url", None)
            if process:
                await terminate_process_tree(process, grace_seconds=0.1)

    async def cancel(self) -> dict:
        self._cancel_generation += 1
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.job and self.job["state"] == "running":
            self.job.update(state="cancelled", message="Login cancelled.")
            self.job.pop("device_code", None)
            self.job.pop("verification_url", None)
        return self.status()


agent_login = AgentLogin()
