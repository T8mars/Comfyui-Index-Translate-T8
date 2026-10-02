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
            handle.writestr(info, source.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return target


node_files = [ROOT / name for name in (
    "__init__.py", "nodes.py", "download_model.py", "pyproject.toml", "requirements.txt",
    "requirements-nf4.txt", "README.md", "README.zh-CN.md", "README_EN.md", "LICENSE", "NOTICE",
)] + files_under(ROOT / "_vendor") + files_under(ROOT / "examples")
assets = [
    archive(f"IndexTranslate-ComfyUI-v{VERSION}.zip", node_files, ROOT),
    archive(f"IndexTranslate-Windows-code-v{VERSION}.zip", files_under(ROOT / "standalone" / "windows"), ROOT / "standalone" / "windows"),
    archive(f"IndexTranslate-Chrome-v{VERSION}.zip", files_under(ROOT / "standalone" / "chrome"), ROOT / "standalone" / "chrome"),
]
(OUT / "SHA256SUMS.txt").write_text(
    "".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in assets), "utf-8"
)
for asset in assets:
    print(asset.name, asset.stat().st_size)
