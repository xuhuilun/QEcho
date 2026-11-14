"""Windows entry point for the local service and the installed-app updater."""

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from dataclasses import replace
from logging.handlers import RotatingFileHandler
from pathlib import Path
from queue import Empty, SimpleQueue
from tkinter import messagebox
from urllib.request import ProxyHandler, build_opener

from .config import ConfigError, load_config
from .runtime import InstanceLock
from .windows_service import run_service
from .windows_update import (
    UpdateError,
    check_for_update,
    discard_update,
    download_update,
    verify_installer_signature,
)
from .windows_window import WindowsWindow

LOGGER = logging.getLogger("qecho.windows")
UPDATE_INTERVAL_MS = 15 * 60 * 1000


def prepare_config(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "qecho.toml"
    if not target.exists():
        bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
        sample = (bundle / "qecho.example.toml").read_bytes()
        with target.open("xb") as output:
            output.write(sample)
    return target


def smoke_test(directory: Path, version: str) -> None:
    result = directory / "smoke-result.json"
    result.unlink(missing_ok=True)
    config = load_config(prepare_config(directory))
    if config.data_directory != directory / ".data":
        raise ValueError("Smoke test requires the isolated fixture data directory.")
    config = replace(config, agent=config.agent.model_copy(update={"auto_start": False}))
    stop = threading.Event()

    def verify(url):
        # Use direct loopback traffic, independent of the user's proxy settings.
        with build_opener(ProxyHandler({})).open(url + "/healthz", timeout=5) as response:
            if json.load(response) != {"status": "ok"}:
                raise RuntimeError("Unexpected health response.")
        with build_opener(ProxyHandler({})).open(url + "/static/app.js", timeout=5) as response:
            if b"/api/automation" not in response.read(256_000):
                raise RuntimeError("Web assets missing from the frozen executable.")
        stop.set()

    # The HTTP probes execute on a different thread from the server loop.
    errors = []
    probes = []

    def start_probe(url):
        def probe():
            try:
                verify(url)
            except Exception as exc:
                errors.append(exc)
                stop.set()

        worker = threading.Thread(target=probe)
        probes.append(worker)
        worker.start()

    run_service(config, stop, start_probe, port=0)
    for worker in probes:
        worker.join()
    if errors:
        raise errors[0]
    if not stop.is_set() or not (config.data_directory / "qecho.sqlite3").is_file():
        raise RuntimeError("The smoke test did not complete.")
    result.write_text(json.dumps({"version": version, "health": "ok"}), encoding="utf-8")


class DesktopClient:
    def __init__(self, root, config_path, version, *, after_update=False):
        self.root = root
        self.config_path = config_path
        self.version = version
        self.events = SimpleQueue()
        self.service = None
        self.service_stop = threading.Event()
        self.console_url = None
        self.closing = False
        self.updating = False
        self.checking = False
        self.notified_version = None
        self.install_pending = None
        self.window = WindowsWindow(
            root,
            version=version,
            on_open_config=self.open_config,
            on_start=self.start_service,
            on_stop=self.stop_service,
            on_open_console=self.open_console,
            on_check_updates=lambda: self.check_updates(interactive=True),
            on_close=self.close,
        )
        self.root.after(50, self.poll)
        if after_update:
            self.window.set_service_state(
                "更新完成。点击「启动服务」恢复代聊。",
                can_start=True,
                can_stop=False,
                can_open_console=False,
            )
        else:
            self.root.after(100, self.start_service)
        self.root.after(1000, self.scheduled_check)

    def poll(self):
        while True:
            try:
                callback, args = self.events.get_nowait()
            except Empty:
                break
            callback(*args)
        if not self.closing:
            self.root.after(50, self.poll)

    def post(self, callback, *args):
        self.events.put((callback, args))

    def open_config(self):
        try:
            notepad = Path(os.environ["SystemRoot"]) / "System32" / "notepad.exe"
            subprocess.Popen([str(notepad), str(self.config_path)])
        except (KeyError, OSError):
            self.window.show_error(f"请用文本编辑器打开配置文件：\n{self.config_path}")

    def open_console(self):
        if self.console_url:
            webbrowser.open(self.console_url)

    def start_service(self):
        if self.service is not None or self.closing or self.install_pending:
            return
        try:
            config = load_config(self.config_path)
        except ConfigError as exc:
            self.window.set_service_state(
                str(exc), can_start=True, can_stop=False, can_open_console=False
            )
            return
        self.service_stop.clear()
        self.window.set_service_state(
            "正在启动本地服务…", can_start=False, can_stop=True, can_open_console=False
        )

        def serve():
            error = ""
            try:
                run_service(
                    config, self.service_stop, lambda url: self.post(self.service_ready, url)
                )
            except ValueError as exc:
                error = str(exc)
            except Exception:
                LOGGER.exception("Local service failed")
                error = "本地服务出错，请查看数据目录中的 qecho.log。"
            self.post(self.service_stopped, error)

        self.service = threading.Thread(target=serve, name="qecho-service")
        self.service.start()

    def service_ready(self, url):
        if not self.service_stop.is_set():
            self.console_url = url
            self.window.set_service_state(
                f"服务已启动 · {url}", can_start=False, can_stop=True, can_open_console=True
            )

    def stop_service(self):
        if self.service is not None:
            self.service_stop.set()
            self.console_url = None
            self.window.set_service_state(
                "正在停止本地服务…", can_start=False, can_stop=False, can_open_console=False
            )

    def service_stopped(self, error):
        self.service = None
        self.console_url = None
        if error and self.install_pending:
            discard_update(self.install_pending)
            self.install_pending = None
            self.updating = False
            self.window.set_update_state("本地服务退出异常，请检查后再更新。")
        self.window.set_service_state(
            error or "本地服务已停止。",
            can_start=not self.closing,
            can_stop=False,
            can_open_console=False,
        )
        if self.install_pending:
            self.install_update()
        elif self.closing:
            self.root.destroy()

    def scheduled_check(self):
        self.check_updates(interactive=False)
        if not self.closing:
            self.root.after(UPDATE_INTERVAL_MS, self.scheduled_check)

    def check_updates(self, *, interactive):
        if self.checking or self.updating or self.closing:
            return
        self.checking = True
        self.window.set_update_state("正在检查更新…", checking=True)

        def check():
            try:
                update = check_for_update(self.version)
            except UpdateError as exc:
                self.post(self.update_checked, None, str(exc), interactive)
            except Exception:
                LOGGER.exception("Update check failed")
                self.post(self.update_checked, None, "检查更新失败，请稍后重试。", interactive)
            else:
                self.post(self.update_checked, update, "", interactive)

        threading.Thread(target=check, daemon=True, name="qecho-check-update").start()

    def update_checked(self, update, error, interactive):
        self.checking = False
        if self.closing:
            return
        if error or update is None:
            self.window.set_update_state(error or "未发现可安装的新版本。")
            return
        if self.notified_version == update.version and not interactive:
            self.window.set_update_state(f"可更新至 {update.version}。")
            return
        self.notified_version = update.version
        self.window.set_update_state(f"发现新版本 {update.version}。", checking=True)
        if self.window.confirm_update(update.version) and not self.closing:
            self.download(update)
        elif not self.closing:
            self.window.set_update_state(f"可更新至 {update.version}。")

    def download(self, update):
        self.updating = True
        self.window.set_update_state(f"正在下载并校验 {update.version}…", checking=True)

        def worker():
            try:
                path = download_update(update, Path(sys.executable))
            except UpdateError as exc:
                self.post(self.update_failed, str(exc))
            except Exception:
                LOGGER.exception("Update download failed")
                self.post(self.update_failed, "更新下载失败，请稍后重试。")
            else:
                self.post(self.update_downloaded, path)

        threading.Thread(target=worker, name="qecho-download-update").start()

    def update_failed(self, message):
        self.updating = False
        self.window.set_update_state(message)
        if not self.closing:
            self.window.show_error(message)

    def update_downloaded(self, path):
        self.install_pending = path
        self.window.set_update_state("已完成校验，正在准备安装…", checking=True)
        if self.service is not None:
            self.stop_service()
        else:
            self.install_update()

    def install_update(self):
        path, self.install_pending = self.install_pending, None
        try:
            verify_installer_signature(path, Path(sys.executable))
            subprocess.Popen(
                [
                    str(path),
                    "/VERYSILENT",
                    "/SUPPRESSMSGBOXES",
                    "/NORESTART",
                    "/SP-",
                    "/QECHOUPDATE",
                    f"/QECHOPID={os.getpid()}",
                ],
                cwd=Path(sys.executable).parent,
            )
        except UpdateError as exc:
            discard_update(path)
            self.update_failed(str(exc))
            return
        except OSError:
            discard_update(path)
            self.update_failed("无法启动更新安装程序。可以重新启动本地服务后再试。")
            return
        self.closing = True
        self.root.destroy()

    def close(self):
        if self.closing:
            return
        if self.updating:
            self.window.show_error("正在下载或安装更新，请等待当前更新完成。")
            return
        self.closing = True
        if self.service is None:
            self.root.destroy()
        else:
            self.stop_service()
            # Keep consuming the completion notification after the close request.
            self.wait_to_close()

    def wait_to_close(self):
        if self.service is not None:
            self.poll()
            if self.service is not None:
                self.root.after(50, self.wait_to_close)


def main(*, version: str) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--after-update", action="store_true")
    args = parser.parse_args()
    if args.smoke_test != (args.data_dir is not None):
        parser.error("--smoke-test and --data-dir must be used together")
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        # External system programs should use the standard Windows DLL search path.
        from ctypes import windll

        windll.kernel32.SetDllDirectoryW(None)
    directory = (
        args.data_dir.resolve() if args.smoke_test else Path(os.environ["LOCALAPPDATA"]) / "QEcho"
    )
    directory.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        handlers=[
            RotatingFileHandler(
                directory / "qecho.log", maxBytes=2_000_000, backupCount=1, encoding="utf-8"
            )
        ],
    )
    lock = InstanceLock(directory / "client.lock")
    try:
        lock.acquire()
        if args.smoke_test:
            smoke_test(directory, version)
        else:
            DesktopClient(
                tk.Tk(), prepare_config(directory), version, after_update=args.after_update
            ).root.mainloop()
    except Exception as exc:
        LOGGER.exception("QEcho client startup failed")
        if not args.smoke_test:
            messagebox.showerror(
                "QEcho", f"无法启动 QEcho。\n{exc}\n日志：{directory / 'qecho.log'}"
            )
        return 1
    finally:
        lock.release()
    return 0
