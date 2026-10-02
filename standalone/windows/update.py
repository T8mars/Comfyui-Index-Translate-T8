"""Apply code-only GitHub updates without touching Python, models or user data."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import time
import urllib.request
import urllib.error
import zipfile
from pathlib import Path, PurePosixPath

from process_lock import exclusive_lock

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
VERSION = ROOT / "VERSION"
RELEASE_API = "https://api.github.com/repos/T8mars/Comfyui-Index-Translate-T8/releases/latest"
MAX_ARCHIVE = 50 * 1024 * 1024
ALLOWED_DIRS = {"app", "web", "_vendor"}
ALLOWED_FILES = {
    "VERSION", "run.py", "launcher.py", "process_lock.py", "update.py", "update.cmd",
    "start.cmd", "stop.cmd", "diagnose.cmd", "README.zh-CN.md", "requirements.lock.txt",
    "LICENSE", "NOTICE",
}


def version(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value.strip())
    if not match:
        raise ValueError("无效版本号")
    return tuple(map(int, match.groups()))


def request(url: str, *, limit: int) -> bytes:
    headers = {"User-Agent": "IndexTranslate-CodeUpdater/1", "Accept": "application/vnd.github+json"}
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=8) as response:
        body = response.read(limit + 1)
    if len(body) > limit:
        raise ValueError("更新响应超出大小限制")
    return body


def latest_release() -> tuple[str, dict] | None:
    try:
        release = json.loads(request(RELEASE_API, limit=2 * 1024 * 1024))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise
    if release.get("draft") or release.get("prerelease"):
        return None
    tag = release["tag_name"]
    if version(tag) <= version(VERSION.read_text("utf-8")):
        return None
    expected = f"IndexTranslate-Windows-code-{tag}.zip"
    asset = next((item for item in release["assets"] if item["name"] == expected), None)
    if not asset or not re.fullmatch(r"sha256:[a-fA-F0-9]{64}", asset.get("digest", "")):
        raise ValueError("新版缺少受校验的 Windows 代码包")
    if not 0 < asset.get("size", 0) <= MAX_ARCHIVE:
        raise ValueError("代码包大小不合理")
    return tag, asset


def validated_entries(payload: bytes, tag: str) -> list[tuple[str, bytes]]:
    result = []
    seen = set()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        if len(archive.infolist()) > 200:
            raise ValueError("代码包文件过多")
        for item in archive.infolist():
            if item.is_dir():
                continue
            parts = PurePosixPath(item.filename).parts
            if not parts or any(part in {"", ".", ".."} for part in parts):
                raise ValueError("代码包路径不安全")
            if not (len(parts) == 1 and parts[0] in ALLOWED_FILES or
                    len(parts) > 1 and parts[0] in ALLOWED_DIRS):
                raise ValueError(f"代码包包含禁止路径：{item.filename}")
            if item.filename in seen or item.file_size > 10 * 1024 * 1024:
                raise ValueError("代码包文件重复或过大")
            mode = (item.external_attr >> 16) & 0o170000
            if mode not in (0, 0o100000):
                raise ValueError("代码包包含非普通文件")
            seen.add(item.filename)
            result.append((item.filename, archive.read(item)))
    content = dict(result)
    if content.get("VERSION", b"").decode("utf-8").strip() != tag.removeprefix("v"):
        raise ValueError("代码包版本不匹配")
    if not {"run.py", "launcher.py", "update.py", "web/index.html"} <= seen:
        raise ValueError("代码包缺少必要文件")
    return result


def apply_release(tag: str, asset: dict) -> None:
    from launcher import owned, read_record

    if (record := read_record()) and owned(record):
        raise RuntimeError("服务正在运行；请先使用 stop.cmd 停止，再运行 update.cmd")
    payload = request(asset["browser_download_url"], limit=MAX_ARCHIVE)
    if len(payload) != asset["size"] or hashlib.sha256(payload).hexdigest() != asset["digest"][7:].lower():
        raise ValueError("代码包 SHA256 校验失败")
    entries = validated_entries(payload, tag)
    candidate_lock = dict(entries).get("requirements.lock.txt")
    current_lock = ROOT / "requirements.lock.txt"
    if (candidate_lock is not None and current_lock.exists() and
            candidate_lock.decode("utf-8").splitlines() != current_lock.read_text("utf-8").splitlines()):
        raise RuntimeError("新版需要更新 Python 依赖，请获取作者分享的新完整整合包")
    DATA.mkdir(exist_ok=True)
    with exclusive_lock(DATA / "update.lock", "另一更新任务正在运行"):
        backup = DATA / "update-backup" / VERSION.read_text("utf-8").strip()
        backup.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="update-", dir=DATA) as temporary:
            staging = Path(temporary)
            for name, content in entries:
                destination = staging / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
            touched = []
            try:
                for name, _ in entries:
                    target = ROOT / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    old = backup / name
                    old.parent.mkdir(parents=True, exist_ok=True)
                    existed = target.exists()
                    if existed:
                        shutil.copy2(target, old)
                    os.replace(staging / name, target)
                    touched.append((target, old, existed))
            except Exception:
                for target, old, existed in reversed(touched):
                    if existed:
                        os.replace(old, target)
                    else:
                        target.unlink(missing_ok=True)
                raise
    print(f"代码已自动更新到 {tag}；Python、模型和用户数据未改动。")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["auto", "check", "apply"])
    mode = parser.parse_args().mode
    DATA.mkdir(exist_ok=True)
    cache = DATA / "update-check.json"
    if mode == "auto" and cache.exists() and time.time() - cache.stat().st_mtime < 24 * 3600:
        return
    if mode == "auto":
        from launcher import owned, read_record
        record = read_record()
        if record and owned(record):
            return
    try:
        available = latest_release()
        if not available:
            print("当前代码已是最新版本。")
            return
        tag, asset = available
        if mode == "check":
            print(f"有新版本 {tag}：{asset['browser_download_url']}")
        else:
            apply_release(tag, asset)
    except Exception as error:
        if mode != "auto":
            raise
        print(f"自动更新暂不可用：{error}")
    finally:
        cache.write_text(json.dumps({"checked_at": time.time()}), "utf-8")


if __name__ == "__main__":
    main()
