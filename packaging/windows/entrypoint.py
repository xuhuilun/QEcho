"""Entry point for the frozen Windows application."""

import sys
from pathlib import Path

from qecho import __version__
from qecho.windows_client import main

if __name__ == "__main__":
    version = __version__
    if getattr(sys, "frozen", False):
        version = (Path(sys._MEIPASS) / "version.txt").read_text(encoding="utf-8").strip()
    raise SystemExit(main(version=version))
