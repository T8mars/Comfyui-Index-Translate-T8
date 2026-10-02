"""Open a native directory picker in a separate process for the local WebUI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def choose_directory(initial_directory: Path, title: str = "选择 Index Translate 模型文件夹") -> str | None:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), str(initial_directory), title],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
            creationflags=flags,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("选择文件夹超时，请重试") from error
    if result.returncode:
        raise RuntimeError("无法打开文件夹选择窗口：" + (result.stderr.strip() or "未知错误"))
    path = json.loads(result.stdout)
    return str(Path(path).resolve()) if path else None


def _dialog(initial_directory: str, title: str) -> None:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.update()
    initial = Path(initial_directory)
    if not initial.is_dir():
        initial = next((parent for parent in initial.parents if parent.is_dir()), Path.home())
    try:
        selected = filedialog.askdirectory(
            parent=root,
            title=title,
            initialdir=str(initial),
            mustexist=True,
        )
        print(json.dumps(selected, ensure_ascii=False))
    finally:
        root.destroy()


if __name__ == "__main__":
    _dialog(sys.argv[1], sys.argv[2])
