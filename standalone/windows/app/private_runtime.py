"""Keep the standalone runtime independent of user/global Python packages."""
import os
import sys
from pathlib import Path

PYTHON_OPTIONS = ['-E', '-s', '-X', 'utf8']
_DLL_HANDLES = []


def ensure_private_runtime(script):
    # CMD/EXE already use these options. Direct Python entry points re-exec
    # before third-party imports too, retaining script-local app imports.
    if not (sys.flags.ignore_environment and sys.flags.no_user_site and sys.flags.utf8_mode):
        os.execv(sys.executable, [sys.executable, *PYTHON_OPTIONS,
                                 str(Path(script).resolve()), *sys.argv[1:]])
    directory = Path(script).resolve().parent / 'app/dlls'
    if os.name == 'nt' and directory.is_dir():
        _DLL_HANDLES.append(os.add_dll_directory(str(directory)))
