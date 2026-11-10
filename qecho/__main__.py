"""Discover the project TOML configuration, or keep the legacy web workflow."""

import argparse
import os
from pathlib import Path

import uvicorn

from .config import ConfigError, load_config


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="QEcho 本地 QQ 代聊服务")
    parser.add_argument("--config", metavar="PATH", help="指定集中 TOML 配置文件")
    parser.add_argument("--check-config", action="store_true", help="只校验配置，不启动服务")
    args = parser.parse_args(argv)
    if args.config == "":
        parser.exit(2, "配置路径不能为空（invalid_config_path）。\n")
    path = args.config if args.config is not None else os.getenv("QECHO_CONFIG")
    if path is None or path == "":
        discovered = Path.cwd() / "qecho.toml"
        path = discovered if discovered.exists() else None
    file_config = None
    if path is not None:
        try:
            file_config = load_config(path)
        except ConfigError as exc:
            parser.exit(2, f"配置错误：{exc}\n")
    if args.check_config:
        if file_config is None:
            parser.exit(2, "未找到 qecho.toml，请使用 --config PATH 指定配置文件。\n")
        print("配置检查通过；未启动服务或连接 QQ。")
        return
    if file_config is not None:
        from .app import create_app

        uvicorn.run(
            create_app(file_config=file_config),
            host=file_config.server.host,
            port=file_config.server.port,
            workers=1,
            access_log=False,
        )
        return
    try:
        port = int(os.getenv("QECHO_PORT", "8765"))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        parser.exit(2, "QECHO_PORT: invalid_port\n")
    uvicorn.run(
        "qecho.app:app",
        host=os.getenv("QECHO_HOST", "127.0.0.1"),
        port=port,
        workers=1,
        # Webhook request lines can contain private account metadata in some bridges.
        access_log=False,
    )


if __name__ == "__main__":
    main()
