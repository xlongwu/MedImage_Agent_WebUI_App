# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

repo_root = Path(SPECPATH).resolve().parents[1]
entry = repo_root / "src" / "backend" / "app" / "desktop_backend_entry.py"

# ── Collect OpenSSL DLLs from the current Python environment ─────────────
# Conda environments store OpenSSL DLLs in <prefix>/Library/bin while
# standard Python stores them alongside the _ssl.pyd extension module.
_ssl_binaries = []
_prefix = Path(getattr(sys, "base_prefix", sys.prefix))
_library_bin = _prefix / "Library" / "bin"
if _library_bin.is_dir():
    for _dll_name in ("libssl-3-x64.dll", "libcrypto-3-x64.dll"):
        _dll_path = _library_bin / _dll_name
        if _dll_path.is_file():
            _ssl_binaries.append((str(_dll_path), "."))

# Runtime data files collected by reviewed optional backends.
_datas = []
_agent_skill_root = repo_root / "src" / "backend" / "app" / "agent_skills"
for _skill_id in (
    "planning_evidence_review.v1",
    "result_explanation.v1",
    "recovery_review.v1",
):
    for _resource_name in ("manifest.json", "SKILL.md"):
        _resource_path = _agent_skill_root / _skill_id / _resource_name
        if not _resource_path.is_file():
            raise RuntimeError(f"Required Agent Skill resource is missing: {_resource_path}")
        _datas.append((str(_resource_path), f"src/backend/app/agent_skills/{_skill_id}"))
_acpc_reference_dir = repo_root / "src" / "backend" / "app" / "native_preproc" / "resources" / "acpc_reference"
for _reference_file in ("avg152T1.nii", "spm12_avg152_t1_ras.json"):
    _reference_path = _acpc_reference_dir / _reference_file
    if not _reference_path.is_file():
        raise RuntimeError(f"Required ACPC reference resource is missing: {_reference_path}")
    _datas.append((str(_reference_path), "src/backend/app/native_preproc/resources/acpc_reference"))

# Native preprocessing only imports these reviewed SciPy surfaces.  Collecting
# every SciPy submodule pulls test, plotting, astronomy, and optional-array
# integrations into the desktop sidecar, which makes the CuPy package build
# needlessly slow and can introduce unrelated optional runtime dependencies.
_scipy_hiddenimports = [
    "scipy",
    "scipy.ndimage",
    "scipy.optimize",
    "scipy.signal",
]
_scipy_binaries = collect_dynamic_libs("scipy")

# The CuPy CUDA 12 wheel provides Python bindings but, on Windows, expects the
# CUDA runtime DLLs to be available at launch.  A GPU-enabled desktop sidecar
# therefore takes only the reviewed runtime DLL set from the explicitly
# configured CUDA toolkit and ships the toolkit EULA with those binaries.
_cuda_runtime_names = (
    "cudart64_12.dll",
    "cublas64_12.dll",
    "cublasLt64_12.dll",
    "cufft64_11.dll",
    "nvrtc64_120_0.dll",
    "nvrtc-builtins64_120.dll",
    "nvJitLink_120_0.dll",
)
_cuda_roots = []
for _name, _value in os.environ.items():
    if _name.upper().startswith("CUDA_PATH") and _value:
        _candidate = Path(_value)
        if _candidate.is_dir() and _candidate not in _cuda_roots:
            _cuda_roots.append(_candidate)
_cuda_binaries = []
for _cuda_root in _cuda_roots:
    for _dll_name in _cuda_runtime_names:
        _dll_path = _cuda_root / "bin" / _dll_name
        if _dll_path.is_file():
            _cuda_binaries.append((str(_dll_path), "."))
    _cuda_eula = _cuda_root / "EULA.txt"
    if _cuda_eula.is_file():
        _datas.append((str(_cuda_eula), "licenses/cuda"))

# CuPy is an optional, reviewed GPU acceleration dependency.  A CPU-only
# build remains valid when it is absent; a GPU-enabled sidecar collects its
# Python modules and CUDA-facing dynamic libraries from the pinned build env.
try:
    import cupy  # noqa: F401
except ImportError:
    _cupy_hiddenimports = []
    _cupy_binaries = []
    _cupy_datas = []
else:
    # CuPy resolves several CUDA support modules lazily during import and
    # kernel compilation.  Bundle its complete module namespace from the
    # pinned, minimal build environment; this is more reliable than a fragile
    # hand-maintained hidden-import list.
    _cupy_hiddenimports = (
        collect_submodules("cupy")
        + collect_submodules("cupy_backends")
        + collect_submodules("fastrlock")
        + ["fastrlock"]
    )
    _cupy_binaries = collect_dynamic_libs("cupy")
    # RawKernel/JIT compilation resolves these headers relative to the CuPy
    # package at runtime; shipping only extension modules would make device
    # enumeration succeed but the first numerical kernel fail.
    _cupy_datas = collect_data_files("cupy", includes=["_core/include/**/*"])

a = Analysis(
    [str(entry)],
    pathex=[str(repo_root)],
    binaries=[*_ssl_binaries, *_scipy_binaries, *_cupy_binaries, *_cuda_binaries],
    datas=[*_datas, *_cupy_datas],
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
        # CuPy imports the stdlib graphlib module lazily while building its
        # CUDA compiler dependency graph.  PyInstaller cannot discover that
        # import from the frozen backend entry point.
        "graphlib",
        "pydicom",
        "ssl",
        "_ssl",
    ] + _scipy_hiddenimports + _cupy_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "torch",
        "safetensors",
        # The backend sidecar has no Qt UI.  Conda environments may expose
        # both bindings, which PyInstaller refuses to freeze together.
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
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
    name="medimage-backend",
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
