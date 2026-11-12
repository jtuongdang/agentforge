"""A one-use OAuth handoff listener; never accepts session tokens in URLs."""
from __future__ import annotations

import asyncio
import hmac
import re
from urllib.parse import parse_qs, urlsplit


class LoopbackLogin:
    def __init__(self, state: str):
        self.state = state
        self.server: asyncio.Server | None = None
        self.result: asyncio.Future | None = None
        self.writers: set[asyncio.StreamWriter] = set()
        self.port = 0

    async def start(self) -> str:
        self.result = asyncio.get_running_loop().create_future()
        self.server = await asyncio.start_server(self._receive, "127.0.0.1", 0, limit=8192, backlog=16)
        self.port = self.server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{self.port}/oauth/callback"

    async def wait(self, timeout: float = 300) -> str:
        return await asyncio.wait_for(self.result, timeout=timeout)

    async def close(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        if self.result and not self.result.done():
            self.result.cancel()
        for writer in tuple(self.writers):
            writer.close()

    async def _receive(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.writers.add(writer)
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=3)
            if len(raw) > 4096:
                raise ValueError()
            lines = raw.decode("ascii").split("\r\n")
            method, target, protocol = lines[0].split(" ")
            hosts = [line.split(":", 1)[1].strip() for line in lines[1:] if line.lower().startswith("host:")]
            if method != "GET" or protocol not in {"HTTP/1.0", "HTTP/1.1"} or hosts != [f"127.0.0.1:{self.port}"]:
                raise ValueError()
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or parsed.fragment:
                raise ValueError()
            if parsed.path == "/oauth/done" and not parsed.query:
                await self._reply(writer, 200, "Đã nhận kết quả đăng nhập. Quay lại AgentForge để xem trạng thái.")
                return
            if parsed.path != "/oauth/callback":
                raise ValueError()
            query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=3)
            if set(query) not in ({"state", "code"}, {"state", "error"}) or any(len(value) != 1 for value in query.values()):
                raise ValueError()
            if not hmac.compare_digest(query["state"][0], self.state) or self.result.done():
                raise ValueError()
            if "code" in query:
                code = query["code"][0]
                if not re.fullmatch(r"[A-Za-z0-9_-]{43}", code):
                    raise ValueError()
                self.result.set_result(code)
            else:
                error = query["error"][0]
                if error not in {"approval_required", "account_disabled"}:
                    error = "sign_in_failed"
                self.result.set_exception(ValueError(error))
            # Remove the one-use code from the browser's current address bar.
            await self._reply(writer, 303, "Quay lại AgentForge.", location="/oauth/done")
        except (ValueError, UnicodeError, asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            await self._reply(writer, 400, "Yêu cầu đăng nhập không hợp lệ.")
        except (ConnectionError, OSError):
            pass
        finally:
            self.writers.discard(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, ConnectionError):
                pass

    @staticmethod
    async def _reply(writer: asyncio.StreamWriter, status: int, message: str, location: str | None = None) -> None:
        body = message.encode("utf-8")
        headers = [f"HTTP/1.1 {status} {'See Other' if status == 303 else 'OK' if status == 200 else 'Bad Request'}",
                   "Content-Type: text/plain; charset=utf-8", f"Content-Length: {len(body)}", "Cache-Control: no-store",
                   "Referrer-Policy: no-referrer", "Content-Security-Policy: default-src 'none'; frame-ancestors 'none'",
                   "Connection: close"]
        if location:
            headers.append("Location: " + location)
        writer.write(("\r\n".join(headers) + "\r\n\r\n").encode("ascii") + body)
        await writer.drain()
