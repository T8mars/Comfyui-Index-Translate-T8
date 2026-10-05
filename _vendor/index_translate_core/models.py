from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


class DownloadCancelled(Exception):
    pass


def catalog() -> dict:
    return json.loads((Path(__file__).parent / "model-catalog.json").read_text("utf-8"))


def digest(path: Path, cancel: threading.Event | None = None) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            if cancel and cancel.is_set():
                raise DownloadCancelled("下载已取消，可继续下载")
            block = stream.read(4 * 1024 * 1024)
            if not block:
                break
            checksum.update(block)
    return checksum.hexdigest()


def safe_file(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError("模型文件路径不在所选目录内")
    return path


def model_revision(root: str | Path, model_id: str) -> str:
    """Cheap cache identity; full file verification still happens before load."""
    spec = catalog()[model_id]
    manifest = Path(root) / 'index-quantization.json'
    if not manifest.is_file():
        return spec['revision']
    with manifest.open('rb') as stream:
        raw = stream.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError('量化清单过大')
    data = json.loads(raw)
    if (not isinstance(data, dict) or data.get('schema') != 1 or data.get('format') != 'convrot-int8'
            or data.get('source_model_id') != model_id or data.get('source_revision') != spec['revision']):
        raise ValueError('量化模型来源与所选型号不匹配')
    return spec['revision'] + ':convrot-int8:' + hashlib.sha256(raw).hexdigest()


def inspect_model(root: str | Path, model_id: str | None = None, verify: bool = False,
                  cancel: threading.Event | None = None) -> dict:
    root = Path(root).resolve()
    models = catalog()
    if (root / 'index-quantization.json').is_file():
        from .convrot import inspect_quantized_model
        return inspect_quantized_model(root, model_id, verify, cancel, models)
    if model_id is None:
        config_path = root / "config.json"
        candidates = []
        if config_path.exists():
            config_hash = digest(config_path, cancel)
            candidates = [key for key, spec in models.items()
                          if any(item["path"] == "config.json" and item["sha256"] == config_hash
                                 for item in spec["files"])]
        if len(candidates) != 1:
            raise ValueError("无法识别模型版本；请选择官方 2B/9B 完整模型目录")
        model_id = candidates[0]
    spec = models[model_id]
    problems = list(spec.get("unavailable_reasons", []))
    receipt_path = root / ".index-verified.json"
    try:
        receipt = json.loads(receipt_path.read_text("utf-8"))
    except (OSError, ValueError):
        receipt = {}
    if not isinstance(receipt, dict) or not isinstance(receipt.get("files", {}), dict):
        receipt = {}
    checked = {}
    for item in spec["files"]:
        if cancel and cancel.is_set():
            raise DownloadCancelled("下载已取消，可继续下载")
        path = safe_file(root, item["path"])
        if not path.is_file():
            problems.append(f"缺少 {item['path']}")
            continue
        stat = path.stat()
        if stat.st_size != item["size"]:
            problems.append(f"文件大小错误 {item['path']}")
            continue
        stamp = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                 "sha256": item["sha256"]}
        cached = receipt.get("revision") == spec["revision"] and receipt.get("files", {}).get(item["path"]) == stamp
        if verify or not cached:
            if digest(path, cancel) != item["sha256"]:
                problems.append(f"SHA256 校验失败 {item['path']}")
                continue
        checked[item["path"]] = stamp
    complete = not problems
    if complete:
        payload = {"model_id": model_id, "revision": spec["revision"], "files": checked,
                   "verified_at": time.time()}
        temp = root / (".index-verified." + uuid.uuid4().hex + ".tmp")
        try:
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
            temp.replace(receipt_path)
        except OSError:
            # The receipt is optional after the model files themselves pass verification.
            pass
        finally:
            temp.unlink(missing_ok=True)
    return {"id": model_id, "revision": spec["revision"], "path": str(root),
            "complete": complete, "problems": problems, "spec": spec}


def download_model(model_id: str, root: str | Path, source: str = "modelscope",
                   cancel: threading.Event | None = None, progress=None) -> dict:
    spec = catalog()[model_id]
    if spec.get("unavailable_reasons"):
        raise ValueError("；".join(spec["unavailable_reasons"]))
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    required = sum(item["size"] for item in spec["files"])
    healthy = {item["path"] for item in spec["files"]
               if safe_file(root, item["path"]).is_file()
               and safe_file(root, item["path"]).stat().st_size == item["size"]
               and digest(safe_file(root, item["path"]), cancel) == item["sha256"]}
    existing = sum(item["size"] for item in spec["files"] if item["path"] in healthy)
    if existing == required:
        return inspect_model(root, model_id, cancel=cancel)
    for item in spec["files"]:
        partial = safe_file(root, item["path"] + ".part")
        if item["path"] not in healthy and partial.is_file():
            size = partial.stat().st_size
            if size < item["size"] or (size == item["size"] and digest(partial, cancel) == item["sha256"]):
                existing += size
    if shutil.disk_usage(root).free < required - existing + 512 * 1024 * 1024:
        raise ValueError("磁盘空间不足，请选择其他模型目录")
    lock = root / ".download.lock"
    try:
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            pid = int(lock.read_text("utf-8"))
            import psutil
            if psutil.pid_exists(pid):
                raise RuntimeError("模型正在被另一个下载进程写入")
        except (ValueError, ImportError):
            raise RuntimeError("模型下载锁存在，请先确认没有其他下载后移除锁")
        lock.unlink()
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(lock_fd, str(os.getpid()).encode())
    os.close(lock_fd)
    transferred = 0
    started = time.monotonic()
    try:
        for item in spec["files"]:
            if cancel and cancel.is_set():
                raise DownloadCancelled("下载已取消，可继续下载")
            target = safe_file(root, item["path"])
            if item["path"] in healthy:
                transferred += item["size"]
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(target.name + ".part")
            url = item["urls"][source]
            for attempt in range(4):
                if cancel and cancel.is_set():
                    raise DownloadCancelled("下载已取消，可继续下载")
                try:
                    offset = partial.stat().st_size if partial.exists() else 0
                    if offset == item["size"]:
                        if digest(partial, cancel) == item["sha256"]:
                            partial.replace(target)
                            transferred += item["size"]
                            break
                        partial.unlink()
                        offset = 0
                    if offset > item["size"]:
                        partial.unlink()
                        offset = 0
                    headers = {"User-Agent": "IndexTranslateLocal/0.1"}
                    if offset:
                        headers["Range"] = f"bytes={offset}-"
                    # Bypass environment proxies for local traffic only; model URLs use normal TLS.
                    request = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(request, timeout=60) as response:
                        if offset and response.status != 206:
                            offset = 0
                        if response.status == 206 and not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
                            raise ValueError("服务器返回错误的续传范围")
                        with partial.open("ab" if offset else "wb") as stream:
                            while True:
                                if cancel and cancel.is_set():
                                    raise DownloadCancelled("下载已取消，可继续下载")
                                block = response.read(1024 * 1024)
                                if not block:
                                    break
                                stream.write(block)
                                offset += len(block)
                                if offset > item["size"]:
                                    raise ValueError("下载文件超过发布清单大小")
                                if progress:
                                    progress({"file": item["path"], "file_bytes": offset,
                                              "completed_bytes": transferred + offset, "total_bytes": required,
                                              "elapsed_seconds": round(time.monotonic() - started, 1)})
                    if offset < item["size"]:
                        # A clean EOF can still be a network interruption. Keep
                        # the prefix for Range retry; hash only the complete file.
                        raise OSError(f"下载连接提前结束 {item['path']}，保留进度以便续传")
                    if offset != item["size"] or digest(partial, cancel) != item["sha256"]:
                        partial.unlink(missing_ok=True)
                        raise ValueError(f"下载校验失败 {item['path']}")
                    partial.replace(target)
                    transferred += item["size"]
                    break
                except (urllib.error.URLError, TimeoutError, OSError, ValueError):
                    if attempt == 3:
                        raise
                    if cancel:
                        if cancel.wait(min(2 ** attempt, 8)):
                            raise DownloadCancelled("下载已取消，可继续下载")
                    else:
                        time.sleep(min(2 ** attempt, 8))
        return inspect_model(root, model_id, cancel=cancel)
    finally:
        lock.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="下载并校验 Index-Translate 官方模型")
    parser.add_argument("--model", choices=list(catalog()), default="IndexTeam/Index-Translate-2B")
    parser.add_argument("--directory", required=True)
    parser.add_argument("--source", choices=["modelscope", "huggingface"], default="modelscope")
    args = parser.parse_args()
    last = [0.0]
    def report(event):
        if time.monotonic() - last[0] > 5 or event["file_bytes"] == event["total_bytes"]:
            print(json.dumps(event), flush=True)
            last[0] = time.monotonic()
    result = download_model(args.model, args.directory, args.source, progress=report)
    print(json.dumps({key: value for key, value in result.items() if key != "spec"}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
