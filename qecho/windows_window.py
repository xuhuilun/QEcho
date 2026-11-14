"""Tk widgets for the Windows client. All methods run on Tk's main thread."""

import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk


class WindowsWindow:
    def __init__(
        self,
        root: tk.Tk,
        *,
        version: str,
        on_open_config: Callable[[], None],
        on_start: Callable[[], None],
        on_stop: Callable[[], None],
        on_open_console: Callable[[], None],
        on_check_updates: Callable[[], None],
        on_close: Callable[[], None],
    ) -> None:
        self.root = root
        self._service_status = tk.StringVar(root)
        self._update_status = tk.StringVar(root)

        root.title("QEcho")
        root.minsize(440, 250)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        root.protocol("WM_DELETE_WINDOW", on_close)

        content = ttk.Frame(root, padding=24)
        content.grid(sticky="nsew")
        content.columnconfigure(0, weight=1)
        ttk.Label(content, text=f"QEcho  {version}").grid(sticky="w", pady=(0, 14))

        service = ttk.LabelFrame(content, text="本地服务", padding=12)
        service.grid(sticky="ew")
        service.columnconfigure(0, weight=1)
        ttk.Label(service, textvariable=self._service_status, wraplength=360, justify="left").grid(
            sticky="w", pady=(0, 10)
        )

        service_buttons = ttk.Frame(service)
        service_buttons.grid(sticky="w")
        self._start = ttk.Button(service_buttons, text="启动服务", command=on_start)
        self._stop = ttk.Button(service_buttons, text="停止服务", command=on_stop)
        self._console = ttk.Button(service_buttons, text="打开控制台", command=on_open_console)
        self._start.grid(row=0, column=0, padx=(0, 6))
        self._stop.grid(row=0, column=1, padx=(0, 6))
        self._console.grid(row=0, column=2)

        actions = ttk.Frame(content)
        actions.grid(sticky="ew", pady=(14, 0))
        ttk.Button(actions, text="打开配置文件", command=on_open_config).grid(
            row=0, column=0, padx=(0, 6)
        )
        self._check_updates = ttk.Button(actions, text="检查更新", command=on_check_updates)
        self._check_updates.grid(row=0, column=1)
        ttk.Label(content, textvariable=self._update_status, wraplength=380, justify="left").grid(
            sticky="w", pady=(8, 0)
        )

        self.set_service_state(
            "服务尚未启动。",
            can_start=True,
            can_stop=False,
            can_open_console=False,
        )

    def set_service_state(
        self,
        message: str,
        *,
        can_start: bool,
        can_stop: bool,
        can_open_console: bool,
    ) -> None:
        self._service_status.set(message)
        self._start.configure(state="normal" if can_start else "disabled")
        self._stop.configure(state="normal" if can_stop else "disabled")
        self._console.configure(state="normal" if can_open_console else "disabled")

    def set_update_state(self, message: str, *, checking: bool = False) -> None:
        self._update_status.set(message)
        self._check_updates.configure(state="disabled" if checking else "normal")

    def show_error(self, message: str) -> None:
        messagebox.showerror("QEcho", message, parent=self.root)

    def confirm_update(self, version: str) -> bool:
        return messagebox.askyesno(
            "QEcho 更新",
            f"发现新版本 {version}。现在下载并安装更新吗？\n\n"
            "安装前会停止本地服务。安装后 QEcho 会重新打开，点击「启动服务」即可恢复。",
            parent=self.root,
            default=messagebox.NO,
        )
