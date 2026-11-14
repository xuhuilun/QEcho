import os
from pathlib import Path

root = Path(SPECPATH).parent.parent
analysis = Analysis(
    [str(root / "packaging/windows/entrypoint.py")],
    pathex=[str(root)],
    datas=[
        (str(root / "qecho/static"), "qecho/static"),
        (str(root / "qecho.example.toml"), "."),
        (os.environ["QECHO_BUILD_VERSION_FILE"], "."),
    ],
    hiddenimports=["uvicorn.loops.asyncio", "uvicorn.protocols.http.h11_impl", "uvicorn.lifespan.on"],
)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name="QEcho", console=False, upx=False)
collect = COLLECT(exe, analysis.binaries, analysis.datas, name="QEcho", upx=False)
