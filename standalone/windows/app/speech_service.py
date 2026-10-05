"""Authenticated, standalone access to the bundled R2T2 streaming worker."""

from __future__ import annotations

import base64
import binascii
import copy
import importlib
import json
import threading
import time
from collections import OrderedDict
from pathlib import Path

from .state import atomic_json

DEFAULT_MODEL_PATH = "speech/models/Confucius4-R2T2-GGUF"
MODEL_FILES = ("Confucius4-R2T2-Q8_0.gguf", "mmproj-Confucius4-R2T2-Q8_0.gguf")


class SpeechSessions:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._lock = threading.RLock()
        self._owners: dict[str, str] = {}
        self._manager = None
        self._finished = OrderedDict()
        self.settings_path = self.root / "data/speech-settings.json"
        try:
            settings = json.loads(self.settings_path.read_text("utf-8"))
            self.model_path = settings["model_path"]
            if not isinstance(self.model_path, str) or not self.model_path.strip():
                raise ValueError("无效的语音模型路径")
        except FileNotFoundError:
            self.model_path = DEFAULT_MODEL_PATH
        except (ValueError, KeyError, TypeError):
            self.model_path = DEFAULT_MODEL_PATH

    def resolve_model(self, value: str | None = None) -> Path:
        path = Path(value if value is not None else self.model_path).expanduser()
        path = path.resolve() if path.is_absolute() else (self.root / path).resolve()
        # Accept the original R2T2 project, its models folder, or the pair's folder.
        candidates = (path, path / "Confucius4-R2T2-GGUF", path / "models/Confucius4-R2T2-GGUF")
        return next((p for p in candidates if all((p / name).is_file() for name in MODEL_FILES)), path)

    def settings(self):
        with self._lock:
            return {"model_path": self.model_path, "resolved_path": str(self.resolve_model()),
                    "available": self.available, "active": bool(self._owners)}

    def verify(self, value: str | None = None, *, hash_files: bool = True):
        directory = self.resolve_model(value)
        try:
            native = importlib.import_module("speech.r2t2_core.native")
            model, projector = native.verify_pair(directory, hash_files=hash_files)
        except (OSError, ValueError) as error:
            raise ValueError("语音模型目录需包含完整的官方 Q8 主模型和 mmproj 文件：" + str(error)) from error
        return {"path": str(directory), "files": [model.name, projector.name], "verified": hash_files}

    def configure(self, value: str):
        # Validate before committing; a failed edit preserves the working configuration.
        info = self.verify(value, hash_files=False)
        directory = Path(info["path"])
        if not Path(value).expanduser().is_absolute() and directory.is_relative_to(self.root):
            saved = directory.relative_to(self.root).as_posix()
        else:
            saved = str(directory)
        with self._lock:
            if self._owners:
                raise RuntimeError("请先停止视频语音翻译，再修改语音模型路径")
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(self.settings_path, {"model_path": saved})
            if self.model_path != saved:
                self.close()
            self.model_path = saved
            return self.settings()

    @property
    def available(self) -> bool:
        base = self.root / "speech"
        return all((self.resolve_model() / name).is_file() for name in MODEL_FILES) and all((base / item).is_file() for item in (
            "models/FireRedVAD-ONNX/fireredvad_stream_vad_with_cache.onnx",
            ".runtime/build-native-cu128/python/Release/qwen3asr_native.cp312-win_amd64.pyd",
        ))

    def manager(self):
        with self._lock:
            if self._manager is None:
                if not self.available:
                    raise RuntimeError("R2T2 语音模型或原生库未就绪，请在模型设置中选择已有语音模型目录")
                self._manager = importlib.import_module("speech.bridge").manager
            return self._manager

    def start(self, owner: str):
        with self._lock:
            if not self.available:
                raise RuntimeError("R2T2 语音模型或原生库未就绪，请在模型设置中选择已有语音模型目录")
            manager = self.manager()
            # Chrome's worker can restart and lose the old sid. Native ASR
            # permits one active stream, so retire this owner's orphan first.
            for sid, former in list(self._owners.items()):
                if former != owner:
                    continue
                try:
                    manager.session_request('POST', sid, 'cancel', value={})
                except RuntimeError:
                    manager.close()  # Lost transport/generation cannot be reused.
                    self._owners.clear()
                    break
                finally:
                    self._owners.pop(sid, None)
            result = manager.start_live({"model_dir": str(self.resolve_model())}, {
                "language": "Auto", "context": "", "stream_chunk_ms": 320,
                "min_segment_seconds": 4,
            })
            sid = result["session_id"]
            self._finished.pop(sid, None)  # A new native generation may reuse an ID.
            self._owners[sid] = owner
        return {"session_id": sid, "sample_rate": result["sample_rate"]}

    def _owned(self, owner: str, sid: str):
        with self._lock:
            if self._owners.get(sid) != owner:
                raise PermissionError("语音会话无效或不属于此扩展")
        return self.manager()

    def feed(self, owner: str, sid: str, seq: int, start_sample: int, audio_b64: str):
        manager = self._owned(owner, sid)
        try:
            data = base64.b64decode(audio_b64, validate=True)
        except binascii.Error as error:
            raise ValueError("无效的音频数据") from error
        if not 0 < len(data) <= 32000 * 4 or len(data) % 4:
            raise ValueError("单次音频必须为最多 2 秒的 16 kHz float32 单声道")
        return manager.session_request("POST", sid, "feed", body=data, headers={
            "X-R2T2-Seq": str(seq),
            "X-R2T2-Start-Sample": str(start_sample),
            "Content-Type": "application/octet-stream",
        })

    def finish(self, owner: str, sid: str, last_seq: int, total_samples: int):
        with self._lock:
            now = time.monotonic()
            for key, cached in list(self._finished.items()):
                if now - cached['created'] > 300:
                    del self._finished[key]
            if sid in self._finished:
                cached = self._finished[sid]
                if cached['owner'] != owner:
                    raise PermissionError("语音会话无效或不属于此扩展")
                if cached['watermark'] != (last_seq, total_samples):
                    raise ValueError('同一已结束会话不能使用不同的音频水位')
                return copy.deepcopy(cached['result'])
        manager = self._owned(owner, sid)
        result = manager.session_request("POST", sid, "finish", value={
            "last_seq": last_seq, "total_samples": total_samples,
        })
        # A failed watermark/transport request can leave the worker active.
        # Preserve ownership so the extension can retry or cancel the session.
        if result.get('status') == 'finalized':
            with self._lock:
                if self._owners.get(sid) == owner:
                    self._finished[sid] = {'owner':owner, 'watermark':(last_seq,total_samples),
                                           'result':copy.deepcopy(result), 'created':time.monotonic()}
                    while len(self._finished) > 64:
                        self._finished.popitem(last=False)
                    self._owners.pop(sid, None)
        return result

    def revoke_extensions(self):
        with self._lock:
            sessions = [(sid, owner) for sid, owner in self._owners.items() if owner.startswith('chrome:')]
            for sid, cached in list(self._finished.items()):
                if cached['owner'].startswith('chrome:'):
                    del self._finished[sid]
            if sessions and len(sessions) == len(self._owners):
                self.close()
                return
        for sid, owner in sessions:
            try:
                self.cancel(owner, sid)
            except PermissionError:
                pass  # The session may have ended while revocation was queued.

    def cancel(self, owner: str, sid: str):
        manager = self._owned(owner, sid)
        try:
            return manager.session_request("POST", sid, "cancel", value={})
        finally:
            with self._lock:
                self._owners.pop(sid, None)
                if not self._owners:
                    manager.close()

    def close(self):
        with self._lock:
            if self._manager is not None:
                self._manager.close()
                self._manager = None
            self._owners.clear()
            self._finished.clear()
