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
        self._start_lock = threading.Lock()
        self._lifecycle = 0
        self._starting_owner = None
        self._owners: dict[str, str] = {}
        self._manager = None
        self._backend = {}
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
                    "available": self.available, "active": bool(self._owners), "backend": self._backend,
                    "starting": self._starting_owner is not None}

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
        native_ready = (base / '.runtime/build-native-cu128/python/Release/qwen3asr_native.cp312-win_amd64.pyd').is_file()
        # The trusted manifest is verified inside the worker before fetching or
        # repairing a missing native library. Model/VAD data are still required.
        native_ready = native_ready or (base / 'native-binaries.json').is_file()
        return (all((self.resolve_model() / name).is_file() for name in MODEL_FILES)
                and (base / 'models/FireRedVAD-ONNX/fireredvad_stream_vad_with_cache.onnx').is_file()
                and native_ready)

    def manager(self):
        with self._lock:
            if self._manager is None:
                if not self.available:
                    raise RuntimeError("R2T2 语音模型或原生库未就绪，请在模型设置中选择已有语音模型目录")
                self._manager = importlib.import_module("speech.bridge").manager
            return self._manager

    def start(self, owner: str):
        with self._lock:
            lifecycle = self._lifecycle
        # Long native loading/downloads serialize starts but cannot hold the
        # app lock needed by close(), settings(), and model-path changes.
        with self._start_lock:
            with self._lock:
                if lifecycle != self._lifecycle:
                    raise RuntimeError('语音启动已取消，请重新启动语音翻译')
                if not self.available:
                    raise RuntimeError("R2T2 语音模型或原生库未就绪，请在模型设置中选择已有语音模型目录")
                manager = self.manager()
                orphans = [sid for sid, former in self._owners.items() if former == owner]
                config = {"model_dir": str(self.resolve_model())}
                prepared = manager.ensure()
                self._starting_owner = owner
            try:
                # Chrome can lose its old sid after a worker restart.
                for sid in orphans:
                    try:
                        manager.session_request('POST', sid, 'cancel', value={})
                    except RuntimeError:
                        with self._lock:
                            if self._manager is not manager or lifecycle != self._lifecycle:
                                raise RuntimeError('语音启动已取消，请重新启动语音翻译')
                            manager.close()  # An unusable old generation needs a clean worker.
                            self._owners.clear()
                            prepared = manager.ensure()
                        break
                    finally:
                        with self._lock:
                            if self._manager is manager and lifecycle == self._lifecycle:
                                self._owners.pop(sid, None)
                result = manager.start_live(config, {
                    "language": "Auto", "context": "", "stream_chunk_ms": 320,
                    "min_segment_seconds": 4,
                }, prepared=prepared)
                with self._lock:
                    if self._manager is not manager or lifecycle != self._lifecycle:
                        raise RuntimeError('语音服务已关闭，本次迟到的启动结果已取消，请重新启动语音翻译')
                    sid = result["session_id"]
                    self._backend = result.get('backend', {})
                    self._finished.pop(sid, None)
                    self._owners[sid] = owner
                    return {"session_id": sid, "sample_rate": result["sample_rate"], "backend": self._backend}
            finally:
                with self._lock:
                    if lifecycle == self._lifecycle:
                        self._starting_owner = None

    def _owned(self, owner: str, sid: str):
        with self._lock:
            if self._owners.get(sid) != owner:
                raise PermissionError("语音会话无效或不属于此扩展")
            if self._manager is None:
                raise RuntimeError('语音服务已关闭，请重新启动语音翻译')
            return self._manager

    def _session_request(self, manager, owner: str, sid: str, action: str, **kwargs):
        try:
            return manager.session_request("POST", sid, action, **kwargs)
        except RuntimeError as error:
            # A transient reset retains audio state and credentials. A dead
            # worker or expired session cannot be retried as an active stream.
            code = getattr(error, "worker_code", None)
            if (code in ("WORKER_EXITED", "WORKER_NOT_RUNNING", "SESSION_INVALID") or
                    code == "SESSION_NOT_FOUND" and getattr(error, "http_status", None) == 404):
                with self._lock:
                    if self._manager is manager and self._owners.get(sid) == owner:
                        self._owners.pop(sid, None)
            raise

    def feed(self, owner: str, sid: str, seq: int, start_sample: int, audio_b64: str):
        manager = self._owned(owner, sid)
        try:
            data = base64.b64decode(audio_b64, validate=True)
        except binascii.Error as error:
            raise ValueError("无效的音频数据") from error
        if not 0 < len(data) <= 32000 * 4 or len(data) % 4:
            raise ValueError("单次音频必须为最多 2 秒的 16 kHz float32 单声道")
        return self._session_request(manager, owner, sid, "feed", body=data, headers={
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
        result = self._session_request(manager, owner, sid, "finish", value={
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
            pending_chrome = self._starting_owner is not None and self._starting_owner.startswith('chrome:')
            if (sessions or pending_chrome) and len(sessions) == len(self._owners):
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
            self._lifecycle += 1
            self._starting_owner = None
            if self._manager is not None:
                self._manager.close()
                self._manager = None
            self._owners.clear()
            self._finished.clear()
            self._backend = {}
