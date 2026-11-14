"""Run the existing local HTTP service for the desktop client."""

import asyncio
import socket
from collections.abc import Callable
from threading import Event

import uvicorn

from .app import create_app
from .config import FileConfig


def run_service(config: FileConfig, stop: Event, ready: Callable[[str], None], *, port=None):
    asyncio.run(_serve(config, stop, ready, port=port))


async def _serve(config, stop, ready, *, port):
    if stop.is_set():
        return
    host = config.server.host
    if host == "localhost":
        host = "127.0.0.1"
    if host not in {"127.0.0.1", "::1"}:
        raise ValueError("Windows 客户端的 server.host 须使用 127.0.0.1、localhost 或 ::1。")
    port = config.server.port if port is None else port
    family = socket.AF_INET6 if host == "::1" else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as listener:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((host, port))
        except OSError:
            raise ValueError(
                f"本地端口 {port} 无法使用。请核对已有 QEcho 服务和端口配置。"
            ) from None

        app = create_app(file_config=config, allow_auto_start=lambda: not stop.is_set())
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                lifespan="on",
                http="h11",
                ws="none",
                loop="asyncio",
                log_config=None,
                access_log=False,
                timeout_graceful_shutdown=30,
            )
        )
        async def serve():
            try:
                await server.serve(sockets=[listener])
            except SystemExit:
                raise ValueError(
                    "本地服务启动失败。请检查日志和数据目录是否被其他实例使用。"
                ) from None

        serving = asyncio.create_task(serve())
        while not server.started and not serving.done():
            await asyncio.sleep(0.05)
        if not server.started:
            await serving
            raise ValueError("本地服务启动失败。请检查日志和数据目录是否被其他实例使用。")
        try:
            if not stop.is_set():
                address = f"[{host}]" if family == socket.AF_INET6 else host
                ready(f"http://{address}:{listener.getsockname()[1]}")
            while not stop.is_set() and not serving.done():
                await asyncio.sleep(0.1)
        finally:
            try:
                app.state.startup.stop()
            finally:
                server.should_exit = True
                await serving
