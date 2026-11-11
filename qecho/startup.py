"""One-shot automatic startup from a file, with an immediate owner stop barrier."""

from __future__ import annotations

import asyncio
import logging
import math
from contextlib import suppress
from typing import TYPE_CHECKING

from .adapters import BridgeError
from .simple import SimpleService

if TYPE_CHECKING:
    from .config import FileConfig

LOGGER = logging.getLogger("qecho")


class ConfiguredAutomation:
    """Serialize login checks and startup without delaying application readiness.

    A successful startup is never rearmed. The owner must restart the process to
    resume a file-controlled run after stopping it.
    """

    def __init__(self, simple: SimpleService, cfg: FileConfig, retry_seconds: float = 3) -> None:
        if not math.isfinite(retry_seconds) or retry_seconds <= 0:
            raise ValueError("retry_seconds must be positive and finite")
        self.simple = simple
        self.cfg = cfg
        self.retry_seconds = retry_seconds
        self.state = "waiting" if cfg.agent.auto_start else "disabled"
        self.error = ""
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._stopped = False
        self._closed = False
        self._contacts = self.simple.store.contact_revisions()

    def begin(self) -> asyncio.Task[None] | None:
        """Schedule at most one run; no bridge request blocks server startup."""
        if self._stopped or self._closed:
            return None
        if not self.cfg.agent.auto_start:
            self.state = "disabled"
            return None
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="qecho-configured-startup")
        return self._task

    def stop(self) -> dict:
        """Commit the pause before returning, independently of the async lock."""
        self._stopped = True
        self.state = "stopped"
        self.error = ""
        try:
            return self.simple.stop()
        finally:
            if self._task is not None and not self._task.done():
                self._task.cancel()

    async def sync(self) -> dict:
        """Allow read-only QQ synchronization without racing startup revisions."""
        async with self._lock:
            if self._closed:
                raise ValueError("服务正在关闭，请重新启动 QEcho")
            if not self._stopped and self.state in {"waiting", "starting"}:
                return await self._sync_pending()
            return await self.simple.sync()

    def _check_contacts(self) -> None:
        if self.simple.store.contact_revisions() != self._contacts:
            raise ValueError("联系人权限或接管状态已变化，请核对后重启")

    def _accept_binding(self, previous_account: str) -> None:
        if self._stopped:
            return
        expected = self._contacts
        if self.simple.store.get_settings()["qq_self_id"] != previous_account:
            # sync_account() disables every existing contact and increments each
            # revision once on binding. No other change may refresh this baseline.
            expected = {contact_id: revision + 1 for contact_id, revision in expected.items()}
        current = self.simple.store.contact_revisions()
        if current != expected:
            raise ValueError("联系人权限或接管状态已变化，请核对后重启")
        self._contacts = current

    async def _sync_pending(self) -> dict:
        self._check_contacts()
        account = self.simple.store.get_settings()["qq_self_id"]
        try:
            result = await self.simple.sync()
        except BridgeError:
            # Binding may already have committed before a later HTTP call fails.
            self._accept_binding(account)
            raise
        self._accept_binding(account)
        return result

    def _fail(self, message: str) -> None:
        self.simple.stop()
        self.state = "failed"
        self.error = message
        LOGGER.warning("%s", message)

    async def _run(self) -> None:
        try:
            while not self._stopped:
                try:
                    self.state = "waiting"
                    async with self._lock:
                        if self._stopped:
                            return
                        current = await self._sync_pending()
                        # A bridge implementation can suppress task cancellation.
                        # Never begin another action after the owner's stop.
                        if self._stopped:
                            return
                        self.state = "starting"
                        self.error = ""
                        try:
                            await self.simple.start(
                                list(self.cfg.agent.configured_friends),
                                current["prompt"],
                                expected_revision=current["revision"],
                                keep_current_prompt=True,
                            )
                        except BridgeError:
                            if not self._stopped:
                                self._check_contacts()
                            raise
                        if self._stopped:
                            self.simple.stop()
                            return
                        self.state = "running"
                        self.error = ""
                        return
                except BridgeError as exc:
                    if self._stopped:
                        return
                    if not exc.retryable:
                        self._fail("QQ 桥接配置无效，请核对服务地址和访问令牌后重启。")
                        return
                    self.simple.stop()
                    self.state = "waiting"
                    self.error = "QQ 桥接尚未就绪，正在等待登录或连接恢复。"
                except ValueError:
                    if self._stopped:
                        return
                    self._fail(
                        "无法按文件配置开始代聊，请核对模型配置、好友范围、QQ 账号和接管状态。"
                    )
                    return
                except Exception:
                    if self._stopped:
                        return
                    self._fail("自动启动失败，请检查配置和本地运行环境。")
                    return
                await asyncio.sleep(self.retry_seconds)
        except asyncio.CancelledError:
            # Cancellation is permanent, including cancellation by the caller of
            # begin(); it must not leave a later begin() able to rearm this run.
            if not self._stopped:
                self._stopped = True
                self.state = "stopped"
                self.error = ""
                self.simple.stop()
            raise

    async def shutdown(self) -> None:
        """Drain the startup task before the HTTP clients and store are closed."""
        self._closed = True
        self._stopped = True
        task = self._task
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if self.state != "disabled":
            self.state = "stopped"
        self.error = ""
