"""Decode Windows compiler diagnostics without changing model/cache encodings.

Some native tools emit the Windows ANSI/OEM code page even under Python UTF-8
mode. Only the compiler modules' subprocess bindings and PTX diagnostic logs
are adapted; the standard subprocess module and third-party files are untouched.
"""
from __future__ import annotations

import io
import locale
import os
from pathlib import Path
import subprocess
import sys
import threading

_lock = threading.RLock()


def _windows_encodings():
    import ctypes
    return [f'cp{ctypes.windll.kernel32.GetACP()}', f'cp{ctypes.windll.kernel32.GetOEMCP()}']


def decode_tool_text(data, encoding='utf-8', errors='strict'):
    if not isinstance(data, bytes):
        return data
    try:
        return bytes.decode(data, encoding, errors)
    except UnicodeDecodeError:
        if sys.platform != 'win32' or errors != 'strict':
            raise
    # Retain a valid UTF-8 tool's output, then try the actual system code pages.
    # No Latin-1/ignore fallback: unknown diagnostic bytes remain visible.
    for candidate in dict.fromkeys(['utf-8-sig', *_windows_encodings()]):
        try:
            return bytes.decode(data, candidate, 'strict')
        except (UnicodeDecodeError, LookupError):
            continue
    return bytes.decode(data, encoding, 'backslashreplace')


class _ToolOutput(bytes):
    def decode(self, encoding='utf-8', errors='strict'):
        # This wrapper only contains native tool stdout/stderr, never a weight,
        # cache, JSON response or other binary payload.
        return decode_tool_text(self, encoding, errors)

    def strip(self, chars=None):
        return type(self)(super().strip(chars))


def _native_command(arguments):
    if not isinstance(arguments, (list, tuple)) or not arguments:
        return False
    name = os.fsdecode(arguments[0]).replace('\\', '/').rsplit('/', 1)[-1].lower().removesuffix('.exe')
    return name in {'ptxas', 'ptxas-blackwell', 'nvcc', 'tcc', 'cl', 'gcc', 'g++',
                    'clang', 'clang++', 'clang-cl', 'icx', 'icx-cl', 'vswhere'}


class _ToolSubprocess:
    _index_native_tool_proxy = True

    def __init__(self, original):
        self.original = original

    def __getattr__(self, name):
        return getattr(self.original, name)

    def check_output(self, arguments, *args, **kwargs):
        if not _native_command(arguments):
            return self.original.check_output(arguments, *args, **kwargs)
        text = bool(kwargs.get('text') or kwargs.get('universal_newlines') or
                    kwargs.get('encoding') or kwargs.get('errors'))
        encoding = kwargs.get('encoding') or locale.getpreferredencoding(False)
        errors = kwargs.get('errors') or 'strict'
        options = dict(kwargs)
        if text:
            for name in ('text', 'universal_newlines', 'encoding', 'errors'):
                options.pop(name, None)
            if isinstance(options.get('input'), str):
                options['input'] = options['input'].encode(encoding, errors)
        def convert(value):
            if not isinstance(value, bytes):
                return value
            if text:
                return decode_tool_text(value, encoding, errors).replace('\r\n', '\n').replace('\r', '\n')
            return _ToolOutput(value)
        try:
            return convert(self.original.check_output(arguments, *args, **options))
        except subprocess.CalledProcessError as error:
            # Keep the actual failing command and exit code; only its display
            # encoding changes. A compilation failure must still be a failure.
            error.output = convert(error.output)
            error.stderr = convert(error.stderr)
            raise


def _ptx_log_open(original):
    def open_log(file, mode='r', *args, **kwargs):
        # Triton reads ptxas stderr from .log files with the Python default.
        # Cache metadata, compiler source and cubins remain strict/unmodified.
        if mode == 'r' and not args and not kwargs and str(file).endswith('.log'):
            with original(file, 'rb') as stream:
                return io.StringIO(decode_tool_text(stream.read()))
        return original(file, mode, *args, **kwargs)
    open_log._index_native_log = True
    return open_log


def prepare_native_tool_text():
    if sys.platform != 'win32':
        return {'enabled': False}
    import builtins
    import importlib
    names = ['triton.knobs', 'triton.windows_utils', 'triton.backends.nvidia.compiler',
             'torch._inductor.cpp_builder', 'torch.utils.cpp_extension']
    adapted = []
    with _lock:
        for name in names:
            try:
                module = importlib.import_module(name)
            except ImportError:
                continue
            original = getattr(module, 'subprocess', None)
            if original is not None and not getattr(original, '_index_native_tool_proxy', False):
                module.subprocess = _ToolSubprocess(original)
            if original is not None:
                adapted.append(name)
            if name == 'triton.backends.nvidia.compiler':
                original_open = getattr(module, 'open', builtins.open)
                if not getattr(original_open, '_index_native_log', False):
                    module.open = _ptx_log_open(original_open)
        from torch._inductor.exc import CppCompileError
        original_init = CppCompileError.__init__
        if not getattr(original_init, '_index_native_tool_text', False):
            def compile_error(self, cmd, output):
                return original_init(self, cmd, decode_tool_text(output))
            compile_error._index_native_tool_text = True
            CppCompileError.__init__ = compile_error
    return {'enabled': True, 'modules': adapted}
