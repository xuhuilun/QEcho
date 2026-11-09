"""Local-first command-line entry point."""

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "qecho.app:app",
        host=os.getenv("QECHO_HOST", "127.0.0.1"),
        port=int(os.getenv("QECHO_PORT", "8765")),
        workers=1,
        # Webhook request lines can contain private account metadata in some bridges.
        access_log=False,
    )


if __name__ == "__main__":
    main()
