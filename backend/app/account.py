"""AgentForge user sessions: browser OIDC handoff, native keychain, profile ownership."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import sqlite3
import sys
import time
import webbrowser
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import httpx

from . import db, providers
from .account_sync import connection_snapshot, github_account
from .license import device_hash
from .oauth_loopback import LoopbackLogin

TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
SERVICE = "AgentForge.Account"


class AccountError(RuntimeError):
    pass


class AccountSessionExpired(AccountError):
    pass


class AccountService:
    def __init__(self):
        self.user: dict | None = None
        self._access = ""
        self._refresh = ""
        self._until = 0.0
        self._login_task: asyncio.Task | None = None
        self._stage = "signed_out"
        self._message = ""
        self._restored = False
        self._sync_metadata = False
        self._session_lock = asyncio.Lock()
        self.transitioning = False

    @staticmethod
    def required() -> bool:
        configured = os.getenv("CLINIC_ORCHESTRATOR_ACCOUNT_REQUIRED")
        if configured is not None:
            return configured.lower() in {"1", "true", "yes"}
        return bool(os.getenv("CLINIC_ORCHESTRATOR_ACCOUNT_HUB_URL") or os.getenv("CLINIC_ORCHESTRATOR_LICENSE_HUB_URL") or getattr(sys, "frozen", False) or globals().get("__compiled__"))

    @staticmethod
    def origin() -> str:
        value = (os.getenv("CLINIC_ORCHESTRATOR_ACCOUNT_HUB_URL") or os.getenv("CLINIC_ORCHESTRATOR_LICENSE_HUB_URL", "")).strip().rstrip("/")
        if not value:
            raise AccountError("Chưa cấu hình địa chỉ Hub cho đăng nhập Google.")
        try:
            parsed = urlsplit(value)
            development = os.getenv("CLINIC_ORCHESTRATOR_ACCOUNT_DEVELOPMENT") == "true" and not (getattr(sys, "frozen", False) or globals().get("__compiled__"))
            local = development and re.fullmatch(r"http://127\.0\.0\.1(?::[0-9]{1,5})?", value)
            if (parsed.scheme != "https" and not local) or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
                raise ValueError()
            _ = parsed.port
        except ValueError:
            raise AccountError("Hub phải là HTTPS origin; chỉ source Development được dùng HTTP loopback.") from None
        return value

    def _slot(self) -> str:
        return hashlib.sha256((self.origin() + "|" + str(db.DB_PATH.resolve())).encode()).hexdigest()

    @staticmethod
    def _setting(key: str) -> str | None:
        with db.connect() as con:
            row = con.execute("SELECT value FROM system_settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    @staticmethod
    def _set_setting(key: str, value: str) -> None:
        with db.transaction() as con:
            con.execute("INSERT INTO system_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP", (key, value))

    def _owns_profile(self, user_id: str | None = None) -> bool:
        return (self._setting("account.owner_user_id") == (user_id or (self.user or {}).get("user_id"))
                and self._setting("account.owner_hub") == self.origin())

    def dispatch_allowed(self) -> bool:
        if self.transitioning:
            return False
        if not self.required():
            return True
        try:
            return bool(self.user and self._access and time.monotonic() < self._until and self._owns_profile())
        except (AccountError, sqlite3.Error):
            return False

    def status(self) -> dict:
        try:
            origin = self.origin()
        except AccountError as exc:
            return {"configured": False, "required": self.required(), "authenticated": False,
                    "stage": "not_configured", "message": str(exc), "user": None, "profile_claim_required": False,
                    "metadata_sync": False, "ai_identity_allowed": not self.required()}
        authenticated = bool(self.user and self._access and time.monotonic() < self._until)
        return {"configured": True, "required": self.required(), "hub": origin, "authenticated": authenticated,
                "stage": self._stage, "message": self._message, "user": self.user,
                "profile_claim_required": bool(authenticated and not self._owns_profile()),
                "metadata_sync": self._sync_metadata, "ai_identity_allowed": self.dispatch_allowed()}

    async def _request(self, path: str, body: dict | None = None, access: str = "") -> dict:
        headers = {"Accept": "application/json"}
        if access:
            headers["Authorization"] = "Bearer " + access
        try:
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=httpx.Timeout(12, connect=3)) as client:
                async with client.stream("GET" if body is None else "POST", self.origin() + path, json=body, headers=headers) as response:
                    if response.status_code == 401:
                        raise AccountSessionExpired("Phiên Hub đã hết hiệu lực. Vui lòng đăng nhập Google lại.")
                    if response.status_code != 200:
                        raise AccountError(f"Hub từ chối yêu cầu (HTTP {response.status_code}).")
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > 32768:
                            raise AccountError("Phản hồi Hub vượt giới hạn.")
                    value = json.loads(content)
                    if not isinstance(value, dict):
                        raise ValueError()
                    return value
        except AccountError:
            raise
        except Exception:
            raise AccountError("Không thể xác minh phiên với Hub. Dữ liệu local vẫn được giữ.") from None

    async def start_login(self, sync_metadata: bool = False) -> dict:
        async with self._session_lock:
            return self._start_login(sync_metadata)

    def _start_login(self, sync_metadata: bool) -> dict:
        self.origin()
        if self._login_task and not self._login_task.done():
            raise AccountError("Đang có giao dịch đăng nhập. Hoàn tất hoặc hủy trước.")
        if self.user:
            raise AccountError("Đăng xuất tài khoản hiện tại trước khi đăng nhập lại.")
        if not providers.credential_store_status()["available"]:
            raise AccountError("OS keychain không khả dụng; đăng nhập đã bị chặn.")
        self._restored = True
        self._sync_metadata = sync_metadata
        self._stage = "opening_browser"; self._message = "Đang mở trình duyệt đăng nhập Google."
        self._login_task = asyncio.create_task(self._login())
        return self.status()

    async def _login(self) -> None:
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        listener = LoopbackLogin(state)
        try:
            callback = await listener.start()
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            device = await asyncio.to_thread(device_hash)
            started = await self._request("/auth/desktop/start", {"device_hash": device, "code_challenge": challenge,
                "callback_uri": callback, "state": state})
            url = started.get("login_url", "")
            parsed = urlsplit(url); base = urlsplit(self.origin()); query = parse_qs(parsed.query)
            if (parsed.scheme, parsed.netloc, parsed.path) != (base.scheme, base.netloc, "/auth/desktop/login") or parsed.fragment or set(query) != {"transaction_id"} or len(query["transaction_id"]) != 1 or not TOKEN.fullmatch(query["transaction_id"][0]):
                raise AccountError("Hub trả địa chỉ đăng nhập không hợp lệ.")
            if not await asyncio.to_thread(webbrowser.open, url):
                raise AccountError("Không mở được trình duyệt hệ thống.")
            self._stage = "awaiting_browser"; self._message = "Hoàn tất Google trong trình duyệt, rồi quay lại app."
            code = await listener.wait()
            tokens = await self._request("/auth/desktop/exchange", {"code": code, "code_verifier": verifier, "device_hash": device})
            async with self._session_lock:
                await self._accept(tokens, device)
                await self._automatic_sync()
        except asyncio.CancelledError:
            self._stage = "cancelled"; self._message = "Đã hủy đăng nhập."
            raise
        except asyncio.TimeoutError:
            self._stage = "expired"; self._message = "Giao dịch hết hạn. Bấm đăng nhập để bắt đầu lượt mới."
        except Exception as exc:
            self._stage = "error"
            self._message = (str(exc) if isinstance(exc, AccountError) else
                "Cần admin duyệt liên kết hồ sơ hoặc giao dịch không còn hợp lệ. Vui lòng thử lại.")
        finally:
            await listener.close()

    async def _accept(self, tokens: dict, device: str) -> None:
        access, refresh, user_id = tokens.get("access_token"), tokens.get("refresh_token"), tokens.get("user_id")
        expiry = tokens.get("expires_in")
        if not isinstance(access, str) or not TOKEN.fullmatch(access) or not isinstance(refresh, str) or not TOKEN.fullmatch(refresh) or not isinstance(user_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", user_id) or tokens.get("device_hash") != device or type(expiry) is not int or not 1 <= expiry <= 900:
            raise AccountError("Hub trả phiên đăng nhập không hợp lệ.")
        profile = await self._request("/auth/me", access=access)
        if profile.get("user_id") != user_id or profile.get("device_hash") != device or profile.get("google_verified") is not True:
            raise AccountError("Không xác minh được danh tính Google của phiên.")
        owner = self._setting("account.owner_user_id")
        if owner is not None and not self._owns_profile(user_id):
            await self._request("/auth/logout", {}, access)
            raise AccountError("Profile dữ liệu này thuộc tài khoản/Hub khác. Hãy dùng profile riêng; dữ liệu không được tự gộp.")
        secret = json.dumps({"refresh_token": refresh, "user_id": user_id, "device_hash": device,
                             "sync_metadata": self._sync_metadata})
        try:
            await asyncio.to_thread(providers._keyring().set_password, SERVICE, self._slot(), secret)
        except Exception:
            await self._request("/auth/logout", {}, access)
            raise AccountError("Không lưu được phiên vào OS keychain; phiên mới đã bị thu hồi.") from None
        # Keep only the allowlisted profile fields, never arbitrary server properties.
        self.user = {"user_id": user_id, "display_name": str(profile.get("display_name", ""))[:120],
                     "email": str(profile.get("google_email", ""))[:254]}
        self._access, self._refresh, self._until = access, refresh, time.monotonic() + expiry
        self._set_setting("account.signed_out", "false")
        self._stage = "signed_in"; self._message = "Google đã xác minh."
        if owner is None:
            self._stage = "profile_claim_required"; self._message = "Xác nhận gắn profile local với tài khoản này trước khi chạy AI."

    def claim_profile(self) -> dict:
        if not self.user or time.monotonic() >= self._until:
            raise AccountError("Cần phiên Google hợp lệ để nhận profile.")
        if self._setting("account.owner_user_id") is not None:
            raise AccountError("Profile đã có chủ sở hữu; không chuyển tài khoản bằng thao tác này.")
        backup_dir = db.DB_PATH.parent / "backups"; backup_dir.mkdir(parents=True, exist_ok=True)
        target = backup_dir / f"before-account-claim-{time.time_ns()}.sqlite3"
        with sqlite3.connect(db.DB_PATH) as source, sqlite3.connect(target) as destination:
            source.backup(destination)
        if os.name == "posix":
            target.chmod(0o600)
        with db.transaction() as con:
            con.execute("BEGIN IMMEDIATE")
            if con.execute("SELECT 1 FROM system_settings WHERE key='account.owner_user_id'").fetchone():
                raise AccountError("Profile vừa được nhận bởi tài khoản khác.")
            con.executemany("INSERT INTO system_settings(key,value) VALUES(?,?)", [
                ("account.owner_user_id", self.user["user_id"]), ("account.owner_hub", self.origin())])
        self._stage = "signed_in"; self._message = "Đã sao lưu SQLite và gắn profile với tài khoản Google."
        return self.status()

    async def sync_connections(self, agents: list[dict]) -> dict:
        async with self._session_lock:
            return await self._sync_connections(agents)

    async def _sync_connections(self, agents: list[dict]) -> dict:
        if not self.user or not self._access or time.monotonic() >= self._until or not self._owns_profile():
            raise AccountError("Cần đăng nhập và nhận profile trước khi đồng bộ.")
        github = await asyncio.to_thread(github_account)
        snapshot = connection_snapshot(agents, github)
        await self._request("/auth/connections", snapshot, self._access)
        self._message = "Đã đồng bộ metadata kết nối vào admin; trạng thái do Desktop báo cáo."
        return self.status()

    async def confirm_profile(self) -> dict:
        async with self._session_lock:
            await asyncio.to_thread(self.claim_profile)
            await self._automatic_sync()
        return self.status()

    async def _automatic_sync(self) -> None:
        if not self._sync_metadata or not self.user or not self._owns_profile():
            return
        try:
            from .registry import AgentRegistry
            agents = await asyncio.to_thread(AgentRegistry().list)
            await self._sync_connections(agents)
        except Exception:
            self._message = "Google đã xác minh; chưa đồng bộ được metadata kết nối. Có thể thử lại."

    async def cancel_login(self) -> dict:
        if self._login_task and not self._login_task.done():
            self._login_task.cancel()
            await asyncio.gather(self._login_task, return_exceptions=True)
        return self.status()

    async def logout(self) -> dict:
        async with self._session_lock:
            return await self._logout()

    async def _logout(self) -> dict:
        await self.cancel_login()
        self._set_setting("account.signed_out", "true")
        access = self._access
        self._access = self._refresh = ""; self._until = 0; self.user = None
        self._restored = True; self._stage = "signed_out"; self._message = "Đã đăng xuất AgentForge."
        try:
            await asyncio.to_thread(providers._keyring().delete_password, SERVICE, self._slot())
        except Exception:
            self._message += " Keychain chưa xóa được; app đã đánh dấu không khôi phục phiên này."
        if access:
            try:
                await self._request("/auth/logout", {}, access)
            except AccountError:
                self._message += " Hub chưa nhận thu hồi; không tự đăng nhập lại trên máy này."
        return self.status()

    async def restore_or_refresh(self) -> None:
        async with self._session_lock:
            if not self._restored:
                self._restored = True
                if self._setting("account.signed_out") == "true":
                    return
                try:
                    raw = await asyncio.to_thread(providers._keyring().get_password, SERVICE, self._slot())
                    saved = json.loads(raw) if raw else {}
                    value = saved.get("refresh_token", "")
                    if not isinstance(value, str) or not TOKEN.fullmatch(value):
                        return
                    self._refresh = value; self._sync_metadata = saved.get("sync_metadata") is True
                except Exception:
                    return
            if not self._refresh or self._login_task and not self._login_task.done() or time.monotonic() < self._until - 60:
                return
            device = await asyncio.to_thread(device_hash)
            tokens = await self._request("/auth/refresh", {"refresh_token": self._refresh, "device_hash": device})
            try:
                await self._accept(tokens, device)
            except Exception:
                self._access = self._refresh = ""; self._until = 0; self.user = None
                self._set_setting("account.signed_out", "true")
                raise
            await self._automatic_sync()

    async def refresh_loop(self) -> None:
        while True:
            try:
                self.origin()
                await self.restore_or_refresh()
            except AccountSessionExpired:
                self._access = self._refresh = ""; self._until = 0; self.user = None
                self._set_setting("account.signed_out", "true")
                self._stage = "signed_out"; self._message = "Phiên bị thu hồi hoặc hết hiệu lực; cần đăng nhập lại."
            except AccountError:
                self._message = "Chưa xác minh lại được phiên với Hub; quyền AI phụ thuộc phiên còn hiệu lực."
            except Exception:
                self._message = "Chưa khôi phục được phiên an toàn. Dữ liệu local vẫn được giữ."
            await asyncio.sleep(30)


account_service = AccountService()
