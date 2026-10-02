"""Authenticated, standalone access to the bundled R2T2 streaming worker."""

from __future__ import annotations

import base64
import binascii
import importlib
import threading
from pathlib import Path


class SpeechSessions:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._lock = threading.RLock()
        self._owners: dict[str, str] = {}
        self._manager = None

    @property
    def available(self) -> bool:
        base = self.root / "speech"
        return all((base / item).is_file() for item in (
            "models/Confucius4-R2T2-GGUF/Confucius4-R2T2-Q8_0.gguf",
            "models/Confucius4-R2T2-GGUF/mmproj-Confucius4-R2T2-Q8_0.gguf",
            "models/FireRedVAD-ONNX/fireredvad_stream_vad_with_cache.onnx",
            ".runtime/build-native-cu128/python/Release/qwen3asr_native.cp312-win_amd64.pyd",
        ))

    def manager(self):
        if not self.available:
            raise RuntimeError("整合包缺少 R2T2 语音模型或原生库")
        with self._lock:
            if self._manager is None:
                self._manager = importlib.import_module("speech.bridge").manager
            return self._manager

    def start(self, owner: str):
        result = self.manager().start_live({}, {
            "language": "Auto", "context": "", "stream_chunk_ms": 320,
            "min_segment_seconds": 4,
        })
        sid = result["session_id"]
        with self._lock:
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
        manager = self._owned(owner, sid)
        try:
            return manager.session_request("POST", sid, "finish", value={
                "last_seq": last_seq, "total_samples": total_samples,
            })
        finally:
            with self._lock:
                self._owners.pop(sid, None)

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
