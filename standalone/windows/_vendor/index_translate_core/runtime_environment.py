"""Locate optional private Windows wheel tools without a system SDK install.

Environment changes affect only this Python process and its children. Explicit
CC/CUDA_PATH/CUDA_HOME settings are preserved. This adapter changes no wheel
files and neither installs packages nor runs a compiler.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import sysconfig
import threading
from pathlib import Path

_lock = threading.Lock()


def _triton_directory() -> Path | None:
    try:
        spec = importlib.util.find_spec("triton")
        if spec is None or not spec.origin:
            return None
        return Path(spec.origin).resolve().parent
    except (ImportError, AttributeError, ValueError, OSError):
        return None


def _python_development_files() -> dict:
    include = Path(sysconfig.get_path("include"))
    version = sysconfig.get_python_version().replace(".", "")
    if sysconfig.get_config_var("Py_GIL_DISABLED"):
        version += "t"
    library = None
    for root in (sys.exec_prefix, sys.base_exec_prefix, str(Path(sys.executable).parent)):
        candidate = Path(root) / "libs" / ("python" + version + ".lib")
        if candidate.is_file():
            library = candidate.resolve()
            break
    return {"python_include": str(include.resolve()),
            "python_header_available": (include / "Python.h").is_file(),
            "python_library": str(library) if library else None,
            "python_library_available": library is not None}


def prepare_runtime_environment() -> dict:
    """Select complete shipped tools before importing FLA/Triton dispatchers.

Triton Windows searches ``sysconfig.platlib/triton`` for TinyCC and CUDA. The
product isolates its wheel files in ``index-translate-speed-deps/triton``;
find the actual imported package so moved/Unicode paths keep working.
Missing optional tools are reported; ordinary reference inference is allowed.
"""
    if sys.platform != "win32":
        return {"enabled": False, "reason": "private Windows tools are not required on this platform"}
    with _lock:
        root = _triton_directory()
        if root is None:
            return {"enabled": False, "reason": "optional Triton package is not installed"}
        result = {"enabled": True, "triton_directory": str(root), "changes": []}
        tcc = root / "runtime/tcc/tcc.exe"
        # A .exe without its runtime DLL and headers is not a complete TinyCC.
        complete_tcc = all((tcc.parent / name).is_file() for name in (
            "tcc.exe", "libtcc.dll", "include/stddef.h", "lib/libtcc1.a",
            "lib/kernel32.def", "lib/msvcrt.def"))
        result["bundled_c_compiler_available"] = complete_tcc
        if complete_tcc and not os.environ.get("CC"):
            os.environ["CC"] = str(tcc)
            result["changes"].append("CC")
        result["c_compiler"] = os.environ.get("CC")
        cuda = root / "backends/nvidia"
        complete_cuda = all((cuda / name).is_file() for name in (
            "bin/ptxas.exe", "include/cuda.h", "lib/x64/cuda.lib"))
        result["bundled_cuda_tools_available"] = complete_cuda
        if complete_cuda and not (os.environ.get("CUDA_PATH") or os.environ.get("CUDA_HOME")):
            os.environ["CUDA_PATH"] = str(cuda)
            result["changes"].append("CUDA_PATH")
        result["cuda_tools"] = os.environ.get("CUDA_PATH") or os.environ.get("CUDA_HOME")
        result.update(_python_development_files())
        from .native_tool_text import prepare_native_tool_text
        result['native_tool_text'] = prepare_native_tool_text()
        return result
