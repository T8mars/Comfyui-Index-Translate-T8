"""Windows-native Confucius4-R2T2 GGUF inference support."""

__all__ = ["NativeQ8Engine", "StreamingSession"]

# The worker imports this package before NumPy, SoXR and ONNX Runtime.
# Register the private CRT before any of those extensions need its DLLs.
import os
from pathlib import Path

_DLL_HANDLES = []
_CRT = Path(__file__).resolve().parents[2] / 'app/dlls'
if os.name == 'nt' and _CRT.is_dir():
    _DLL_HANDLES.append(os.add_dll_directory(str(_CRT)))


def __getattr__(name):
    if name == 'NativeQ8Engine':
        from .native import NativeQ8Engine
        return NativeQ8Engine
    if name == 'StreamingSession':
        from .streaming import StreamingSession
        return StreamingSession
    raise AttributeError(name)
