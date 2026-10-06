"""ComfyUI-side transport to the isolated Python 3.12 inference worker."""

from __future__ import annotations

import atexit
import errno
import http.client
import json
import os
import secrets
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SESSION_CREDENTIAL_TTL_SECONDS = 75 * 60


class WorkerError(RuntimeError):
    def __init__(self, message: str, *, http_status: int | None = None,
                 worker_code: str | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.worker_code = worker_code


class WorkerManager:
    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None
        self.port: int | None = None
        self.token: str | None = None
        self.generation: str | None = None
        self._lock = threading.RLock()
        self._model_lock = threading.RLock()
        self._log_file = None
        self._log_path = ROOT / ".runtime/worker.log"
        self._log_start = 0
        # The host may set a system HTTP proxy. Worker traffic must go directly
        # to the loopback listener even if proxy-bypass settings later change.
        self._local_http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self._owners: dict[str, str] = {}
        self._browser_tokens: dict[str, str] = {}
        self._session_activity: dict[str, float] = {}

    def _forget_credentials(self, sid: str) -> None:
        with self._lock:
            self._owners.pop(sid, None)
            self._browser_tokens.pop(sid, None)
            self._session_activity.pop(sid, None)

    def _prune_credentials(self) -> None:
        cutoff = time.monotonic() - SESSION_CREDENTIAL_TTL_SECONDS
        with self._lock:
            for sid, last_activity in list(self._session_activity.items()):
                if last_activity < cutoff:
                    self._owners.pop(sid, None)
                    self._browser_tokens.pop(sid, None)
                    del self._session_activity[sid]

    @staticmethod
    def _port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def _start(self) -> None:
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None
        python = Path(os.environ.get("R2T2_WORKER_PYTHON", sys.executable))
        if not python.is_file():
            raise WorkerError(f"Isolated worker Python not found: {python}. Run scripts/setup_worker_windows.ps1")
        self.port = self._port()
        self.token = secrets.token_urlsafe(48)
        env_keys = ("SYSTEMROOT", "WINDIR", "PATH", "USERPROFILE", "TEMP", "TMP", "CUDA_PATH", "CUDA_VISIBLE_DEVICES")
        env = {key: os.environ[key] for key in env_keys if key in os.environ}
        env["R2T2_WORKER_TOKEN"] = self.token
        log_path = ROOT / ".runtime/worker.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_path = log_path
        self._log_start = log_path.stat().st_size if log_path.exists() else 0
        self._log_file = log_path.open("ab", buffering=0)
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.process = subprocess.Popen(
            [str(python), "-E", "-s", "-X", "utf8", "-X", "faulthandler", "-m", "r2t2_core.worker", "--port", str(self.port)],
            cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
            stdout=self._log_file, stderr=subprocess.STDOUT, creationflags=creationflags,
        )
        self._owners.clear()
        self._browser_tokens.clear()
        self._session_activity.clear()
        for _ in range(100):
            if self.process.poll() is not None:
                message = self._transport_message(self.process.poll(), startup=True)
                self._invalidate(self.process)
                raise WorkerError(message, worker_code="WORKER_EXITED")
            try:
                info = self._request("GET", "/health", timeout=1, invalidate_on_failure=False)
                self.generation = info["generation"]
                return
            except (WorkerError, urllib.error.URLError, TimeoutError):
                time.sleep(0.1)
        self.process.terminate()
        raise WorkerError("语音识别服务启动超时，请停止后重新启动；若仍失败，请检查完整语音运行库。" +
                          self._diagnostic_hint(), worker_code="WORKER_STARTUP_TIMEOUT")

    def ensure(self) -> tuple:
        with self._lock:
            if self.process is None or self.process.poll() is not None:
                self._start()
            return self.process, self.port, self.token

    def _matches(self, prepared: tuple) -> bool:
        return (self.process is prepared[0] and self.port == prepared[1]
                and self.token == prepared[2])

    def _invalidate(self, failed_process: subprocess.Popen | None = None) -> None:
        with self._lock:
            if failed_process is not None and self.process is not failed_process:
                return
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            self.process = None
            self.port = None
            self.token = None
            self.generation = None
            self._owners.clear()
            self._browser_tokens.clear()
            self._session_activity.clear()
            if self._log_file is not None:
                self._log_file.close()
                self._log_file = None

    def _diagnostic_hint(self) -> str:
        """Classify this worker's recent log; never return raw text or secrets."""
        try:
            with self._log_path.open("rb") as stream:
                end = stream.seek(0, 2)
                stream.seek(max(self._log_start, end - 16384))
                text = stream.read(16384).decode("utf-8", "replace").lower()
        except OSError:
            return ""
        reasons = (
            (("out of memory", "failed to allocate", "bad_alloc", "memoryerror", "cudamalloc failed"),
             "显存或内存不足，请关闭其他模型任务并释放显存后重试"),
            (("no kernel image", "invalid device function", "unsupported gpu", "cuda driver version is insufficient"),
             "显卡或驱动不支持当前原生语音运行库，请更新驱动或使用匹配的整合包"),
            (("illegal memory access", "access violation", "device-side assert", "fatal python error"),
             "原生语音推理发生崩溃，请停止服务后重新启动；若重复发生，请检查显卡驱动和运行库"),
            (("dll load failed", "cannot load library", "could not find module", "modulenotfounderror"),
             "语音依赖或 DLL 未能加载，请使用完整整合包并检查文件是否被安全软件隔离"),
            (("permissionerror", "permission denied", "access is denied"),
             "语音模型或运行库访问被拒绝，请检查目录权限和安全软件拦截"),
            (("sha-256 mismatch", "sha256 mismatch", "invalid gguf", "failed to load model"),
             "语音模型未能加载，请检查完整 Q8 模型和 mmproj 文件"),
        )
        for markers, message in reasons:
            if any(marker in text for marker in markers):
                return " 日志提示：" + message + "。"
        return ""

    def _transport_message(self, exit_code: int | None, *, startup: bool = False,
                           retry_safe: bool = False) -> str:
        if exit_code is not None:
            phase = "启动失败" if startup else "已退出，本次语音会话已失效"
            return f"语音识别子进程{phase}（退出码 {exit_code}），请停止语音翻译后重新启动。" + self._diagnostic_hint()
        if retry_safe:
            return "本地语音连接暂时中断，识别进程仍在运行、会话已保留；请重试，或停止后重新启动。" + self._diagnostic_hint()
        return ("本地语音请求的响应中断，识别进程仍在运行；为避免重复创建会话或处理音频，未重复提交。"
                "请停止语音翻译后重新启动；若仍无法启动，请关闭整合包启动器后重新打开。") + self._diagnostic_hint()

    @staticmethod
    def _retry_safe(method: str, path: str, body: bytes | None, headers: dict) -> bool:
        if method in ("GET", "HEAD"):
            return True
        if method != "POST":
            return False
        if path == "/models/load":
            return True  # Worker serializes and reuses the identical model config.
        pieces = path.strip("/").split("/")
        if len(pieces) == 3 and pieces[0] == "sessions":
            if pieces[2] in ("finish", "cancel"):
                return True
            if pieces[2] == "feed":
                return bool(body) and all(str(headers.get(key, "")).isdigit()
                                          for key in ("X-R2T2-Seq", "X-R2T2-Start-Sample"))
        return False

    @staticmethod
    def _retryable_transport(error: Exception) -> bool:
        cause = error.reason if isinstance(error, urllib.error.URLError) else error
        if isinstance(cause, PermissionError):
            raise cause
        if isinstance(cause, (ConnectionError, TimeoutError, http.client.IncompleteRead,
                              http.client.RemoteDisconnected)):
            return True
        return isinstance(cause, OSError) and (
            cause.errno in (errno.ECONNRESET, errno.ECONNREFUSED, errno.ECONNABORTED, errno.EPIPE, errno.ETIMEDOUT)
            or getattr(cause, "winerror", None) in (10053, 10054, 10060, 10061))

    def _safe_detail(self, value: str) -> str:
        with self._lock:
            secrets_to_hide = (self.token, *self._owners.values(), *self._browser_tokens.values())
        for secret in secrets_to_hide:
            if secret:
                value = value.replace(secret, "[已隐藏凭据]")
        return value[:600]

    def _request(self, method: str, path: str, *, body: bytes | None = None,
                 headers: dict[str, str] | None = None, timeout: int = 120,
                 invalidate_on_failure: bool = True, prepared: tuple | None = None) -> dict:
        with self._lock:
            if prepared is not None and not self._matches(prepared):
                raise WorkerError('语音服务已关闭或重新启动，本次启动已取消，请重新启动语音翻译。',
                                  worker_code='SESSION_INVALID')
            request_process = self.process
            request_port, request_token = self.port, self.token
        if request_port is None or request_token is None:
            raise WorkerError("语音服务未运行或上次会话已失效，请重新启动语音翻译。",
                              worker_code="WORKER_NOT_RUNNING")
        retry_safe = self._retry_safe(method, path, body, headers or {})
        request = urllib.request.Request(
            f"http://127.0.0.1:{request_port}{path}", data=body, method=method,
            headers={"Authorization": "Bearer " + request_token, **(headers or {})},
        )
        for attempt in range(2):
            with self._lock:
                same_worker = (self.process is request_process and self.port == request_port
                               and self.token == request_token)
            if not same_worker:
                raise WorkerError("语音服务已关闭或重新启动，本次旧会话请求未重新提交；请重新启动语音翻译。",
                                  worker_code="SESSION_INVALID")
            exit_code = request_process.poll() if request_process is not None else None
            if exit_code is not None:
                message = self._transport_message(exit_code)
                if invalidate_on_failure:
                    self._invalidate(request_process)
                raise WorkerError(message, worker_code="WORKER_EXITED")
            try:
                with self._local_http.open(request, timeout=timeout) as response:
                    answer = json.load(response)
                with self._lock:
                    if not self._matches((request_process, request_port, request_token)):
                        raise WorkerError('语音服务已关闭或重新启动，旧请求的迟到响应已丢弃。',
                                          worker_code='SESSION_INVALID')
                return answer
            except urllib.error.HTTPError as exc:
                try:
                    detail = exc.read(2001).decode("utf-8", "replace")
                except (OSError, http.client.HTTPException):
                    detail = ""
                finally:
                    exc.close()
                try:
                    parsed = json.loads(detail)
                    worker_code = parsed.get("code") if isinstance(parsed, dict) else None
                    detail = parsed.get("message", detail) if isinstance(parsed, dict) else detail
                except json.JSONDecodeError:
                    worker_code = None
                prefix = "语音服务权限校验失败" if exc.code in (401, 403) else "语音识别请求失败"
                message = f"{prefix}（HTTP {exc.code}）：" + self._safe_detail(str(detail))
                if exc.code >= 500:
                    message += self._diagnostic_hint()
                raise WorkerError(message, http_status=exc.code,
                                  worker_code=worker_code if isinstance(worker_code, str) else None) from exc
            except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
                retryable = self._retryable_transport(exc)
                exit_code = request_process.poll() if request_process is not None else None
                same_worker = (request_process is not None and self.process is request_process
                               and self.port == request_port and self.token == request_token)
                if attempt == 0 and retry_safe and retryable and exit_code is None and same_worker:
                    time.sleep(0.1)
                    continue
                message = self._transport_message(exit_code, retry_safe=retry_safe)
                if exit_code is not None and invalidate_on_failure:
                    self._invalidate(request_process)
                raise WorkerError(message, worker_code="WORKER_EXITED" if exit_code is not None else
                                  "WORKER_TRANSPORT_RESET" if retry_safe else "WORKER_RESPONSE_UNKNOWN") from exc
            except (ValueError, UnicodeError) as exc:
                raise WorkerError("语音服务返回了无效响应，请停止后重新启动语音翻译。",
                                  worker_code="WORKER_INVALID_RESPONSE") from exc

    def request(self, method: str, path: str, *, value: dict | None = None,
                body: bytes | None = None, headers: dict[str, str] | None = None,
                timeout: int = 120, prepared: tuple | None = None) -> dict:
        prepared = self.ensure() if prepared is None else prepared
        if value is not None:
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            headers = {"Content-Type": "application/json", **(headers or {})}
        return self._request(method, path, body=body, headers=headers, timeout=timeout, prepared=prepared)

    def load(self, config: dict, *, prepared: tuple | None = None) -> dict:
        prepared = self.ensure() if prepared is None else prepared
        with self._model_lock:
            # A code-only upgrade may fetch the verified prebuilt speech asset
            # once. The full offline bundle already has it installed.
            return self.request("POST", "/models/load", value=config, timeout=900, prepared=prepared)

    def transcribe(self, audio: bytes, options: dict, config: dict) -> dict:
        metadata = json.dumps(options, ensure_ascii=False).encode("utf-8")
        if len(metadata) > 16_384:
            raise ValueError("Transcription options exceed 16 KB")
        payload = struct.pack("<I", len(metadata)) + metadata + audio
        prepared = self.ensure()
        with self._model_lock:
            self.load(config, prepared=prepared)
            return self.request("POST", "/transcribe", body=payload,
                                headers={"Content-Type": "application/octet-stream"},
                                timeout=1800, prepared=prepared)

    def start_live(self, config: dict, options: dict, *, prepared: tuple | None = None) -> dict:
        prepared = self.ensure() if prepared is None else prepared
        self._prune_credentials()
        with self._model_lock:
            loaded = self.load(config, prepared=prepared)
            owner = secrets.token_urlsafe(48)
            browser_token = secrets.token_urlsafe(32)
            result = self.request("POST", "/sessions", value=options, headers={"X-R2T2-Owner": owner},
                                  prepared=prepared)
            sid = result["session_id"]
            with self._lock:
                if not self._matches(prepared):
                    raise WorkerError('语音服务已关闭，本次启动已取消，请重新启动语音翻译。',
                                      worker_code='SESSION_INVALID')
                self._owners[sid] = owner
                self._browser_tokens[sid] = browser_token
                self._session_activity[sid] = time.monotonic()
            return {**result, "browser_token": browser_token, "backend": loaded.get('backend', {})}

    def unload(self) -> dict:
        with self._model_lock:
            return self.request("POST", "/models/unload", value={})

    def check_browser(self, sid: str, token: str) -> None:
        self._prune_credentials()
        with self._lock:
            expected = self._browser_tokens.get(sid)
        if not expected or not secrets.compare_digest(expected, token):
            raise WorkerError("Invalid live-session credential")

    def session_request(self, method: str, sid: str, action: str, *, value: dict | None = None,
                        body: bytes | None = None, headers: dict | None = None) -> dict:
        self._prune_credentials()
        with self._lock:
            owner = self._owners.get(sid)
        if not owner:
            raise WorkerError("上次语音会话已失效，请停止后重新启动语音翻译。", worker_code="SESSION_INVALID")
        try:
            if value is not None:
                body = json.dumps(value, ensure_ascii=False).encode("utf-8")
                headers = {"Content-Type": "application/json", **(headers or {})}
            # A live request cannot recreate the worker: its audio/session state
            # belongs to this generation and cannot be restored by ensure().
            answer = self._request(method, f"/sessions/{sid}/{action}", body=body,
                                   headers={"X-R2T2-Owner": owner, **(headers or {})},
                                   timeout=180 if action == "finish" else 120 if action == "feed" else 30)
            with self._lock:
                if self._owners.get(sid) == owner:
                    self._session_activity[sid] = time.monotonic()
            return answer
        except WorkerError as exc:
            if (exc.worker_code in ("WORKER_NOT_RUNNING", "SESSION_INVALID") or
                    exc.http_status == 404 and exc.worker_code == "SESSION_NOT_FOUND"):
                self._forget_credentials(sid)
            raise

    def close(self) -> None:
        with self._lock:
            self._invalidate()
            if self._log_file is not None:
                self._log_file.close()
            self._log_file = None


manager = WorkerManager()
atexit.register(manager.close)
