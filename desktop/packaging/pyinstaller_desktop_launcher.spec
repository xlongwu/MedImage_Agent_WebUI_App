# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

repo_root = Path(SPECPATH).resolve().parents[1]
entry = repo_root / "src" / "backend" / "app" / "desktop_launcher_entry.py"

datas = [
    (str(repo_root / "src" / "frontend" / "dist"), "frontend"),
    (str(repo_root / "examples"), "workspace_seed/examples"),
    (str(repo_root / "docs"), "workspace_seed/docs"),
    (str(repo_root / "matlab"), "workspace_seed/matlab"),
]

a = Analysis(
    [str(entry)],
    pathex=[str(repo_root)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "src.backend.app.main",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "torch",
        "safetensors",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="MedImage Agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=".",
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
