"""Build reproducible code-only assets for a tagged GitHub Release."""
from __future__ import annotations

import hashlib
import pathlib
import tomllib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["version"]
OUT = ROOT / "dist"
OUT.mkdir(exist_ok=True)


def files_under(directory: pathlib.Path) -> list[pathlib.Path]:
    return sorted(
        path for path in directory.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
        and path.name != "FILE-MANIFEST.json"
    )


def archive(name: str, files: list[pathlib.Path], base: pathlib.Path) -> pathlib.Path:
    target = OUT / name
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as handle:
        for source in files:
            relative = source.relative_to(base).as_posix()
            if any(part in {"runtime", "models", "data"} for part in source.relative_to(base).parts):
                raise ValueError(f"Forbidden release path: {relative}")
            info = zipfile.ZipInfo(relative, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            content = source.read_bytes()
            if source.suffix != '.exe':
                content = content.replace(b'\r\n', b'\n')
            if name.startswith("IndexTranslate-Windows-code-") and relative == "T8IndexTranslate.exe":
                # v0.1.17 permits app/* but not a new root EXE. The updated
                # launcher automatically promotes this file before opening it.
                relative = "app/T8IndexTranslate.exe"
                info.filename = relative
            if source.suffix == '.cmd':
                content = content.replace(b'\n', b'\r\n')
            if name.startswith("IndexTranslate-Windows-code-") and relative == "requirements.lock.txt":
                # Match the line endings in existing Windows bundles.
                content = content.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            handle.writestr(info, content, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return target


node_files = [ROOT / name for name in (
    "__init__.py", "nodes.py", "download_model.py", "pyproject.toml", "requirements.txt",
    "requirements-nf4.txt", "requirements-convrot.txt", "install_convrot.py", "ACCELERATION-DEPS.json", "README.md", "README.zh-CN.md", "README_EN.md", "LICENSE", "NOTICE",
)] + files_under(ROOT / "_vendor") + files_under(ROOT / "examples")
windows = ROOT / "standalone" / "windows"
windows_top = {
    "VERSION", "run.py", "launcher.py", "process_lock.py", "update.py", "update.cmd",
    "start.cmd", "stop.cmd", "diagnose.cmd", "T8IndexTranslate.exe", "README.zh-CN.md",
    "requirements.lock.txt", "LICENSE", "NOTICE",
}
windows_files = [file for file in files_under(windows)
                 if file.relative_to(windows).parts[0] in {"app", "web", "_vendor", "speech"}
                 or file.relative_to(windows).as_posix() in windows_top]
assets = [
    archive(f"IndexTranslate-ComfyUI-v{VERSION}.zip", node_files, ROOT),
    archive(f"IndexTranslate-Windows-code-v{VERSION}.zip", windows_files, windows),
    archive(f"IndexTranslate-Chrome-v{VERSION}.zip", files_under(ROOT / "standalone" / "chrome"), ROOT / "standalone" / "chrome"),
]
(OUT / "SHA256SUMS.txt").write_text(
    "".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in assets), "utf-8"
)
for asset in assets:
    print(asset.name, asset.stat().st_size)
