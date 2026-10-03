from __future__ import annotations

import json
import os
import re
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Query, Path as ApiPath
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "_vendor"))
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("DO_NOT_TRACK", "1")
from index_translate_core.inference import hardware, PROMPT_VERSION
from index_translate_core.models import catalog, inspect_model
from .state import State
from .folder_picker import choose_directory
from .speech_service import SpeechSessions


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Paragraph(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=12000)
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class JobRequest(StrictModel):
    request_id: str = Field(min_length=8, max_length=128)
    expected_identity: str | None = Field(default=None, min_length=1, max_length=128)
    page_epoch: str = Field(default="manual", max_length=128)
    source: str = Field(default="auto", min_length=1, max_length=64)
    target: str = Field(default="zh", min_length=1, max_length=64)
    paragraphs: list[Paragraph] = Field(min_length=1, max_length=16)
    output_budget: int = Field(default=512, ge=1, le=8192)
    glossary: dict[str, str] = Field(default_factory=dict, max_length=100)

    @field_validator("target")
    @classmethod
    def target_explicit(cls, value):
        if value == "auto":
            raise ValueError("请选择明确的目标语言")
        return value


class Settings(StrictModel):
    model_id: str
    model_path: str = Field(min_length=1, max_length=1024)
    device: str = Field(pattern=r"^(auto|cpu|cuda)$")
    precision: str = Field(pattern=r"^(auto|bf16|fp32|nf4|convrot-int8)$")
    context_limit: int = Field(ge=256, le=32768)
    idle_unload_seconds: int = Field(ge=5, le=3600)

    @field_validator("model_id")
    @classmethod
    def official_model(cls, value):
        if value not in catalog() or catalog()[value].get("unavailable_reasons"):
            raise ValueError("该模型版本尚不完整")
        return value


class Pair(StrictModel):
    code: str = Field(min_length=12, max_length=100)
    extension_id: str = Field(pattern=r"^[a-p]{32}$")


class Download(StrictModel):
    model_id: str
    source: str = Field(default="modelscope", pattern=r"^(modelscope|huggingface)$")


class SpeechFeed(StrictModel):
    seq: int = Field(ge=0)
    start_sample: int = Field(ge=0)
    pcm_f32le_b64: str = Field(min_length=4, max_length=180000)


class SpeechSettings(StrictModel):
    model_path: str = Field(min_length=1, max_length=1024)

    @field_validator("model_path")
    @classmethod
    def nonblank_path(cls, value):
        if not value.strip():
            raise ValueError("请选择语音模型目录")
        return value.strip()


class SpeechFinish(StrictModel):
    last_seq: int = Field(ge=-1)
    total_samples: int = Field(ge=0)


def create_app(root=ROOT, state=None, port=8098):
    state = state or State(Path(root))
    speech = SpeechSessions(Path(root))
    origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    @asynccontextmanager
    async def lifespan(app):
        yield
        speech.close()
        state.close()
    app = FastAPI(title="Index Translate Local", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.service = state
    app.state.speech = speech
    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        return Response(json.dumps({"detail": jsonable_encoder(error.errors())}, ensure_ascii=True),
                        status_code=422, media_type="application/json")
    app.add_middleware(CORSMiddleware, allow_origin_regex=r"chrome-extension://[a-p]{32}",
                       allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
                       allow_headers=["Authorization", "Content-Type", "X-Expected-Identity"])
    @app.middleware("http")
    async def boundary(request, call_next):
        if request.headers.get("host") not in hosts:
            return JSONResponse({"error": "Host rejected"}, status_code=403)
        if request.headers.get("content-length", "0").isdigit() and int(request.headers.get("content-length", "0")) > 256000:
            return JSONResponse({"error": "Request too large"}, status_code=413)
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 256000:
                return JSONResponse({"error": "Request too large"}, status_code=413)
            body.extend(chunk)
        request._body = bytes(body)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'"
        return response

    def client(request, ui_only=False):
        def check_identity():
            expected = request.headers.get("x-expected-identity")
            if expected and expected != state.config["identity"]:
                raise HTTPException(409, "本地服务已更换，请确认操作后重试")
        origin = request.headers.get("origin")
        token = request.headers.get("authorization", "")
        if not ui_only and token.startswith("Bearer "):
            owner = state.token_client(token[7:])
            if owner and (not origin or origin == "chrome-extension://" + owner.removeprefix("chrome:")):
                check_identity()
                return owner
            raise HTTPException(401, "配对凭据无效，请重新配对")
        if secrets.compare_digest(request.cookies.get("it_session", ""), state.session):
            if request.method in {"GET", "HEAD"}:
                if origin and origin not in origins:
                    raise HTTPException(403, "Origin rejected")
            elif origin not in origins:
                raise HTTPException(403, "Origin rejected")
            check_identity()
            return "webui"
        raise HTTPException(401, "请从本机整合包界面配对")

    def speech_call(action):
        try:
            return action()
        except PermissionError as error:
            raise HTTPException(403, str(error)) from error
        except (ValueError, OSError) as error:
            raise HTTPException(400, str(error)) from error
        except RuntimeError as error:
            raise HTTPException(503, str(error)) from error

    @app.get("/")
    def index(request: Request):
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "Cross-site navigation rejected")
        response = FileResponse(Path(root) / "web/index.html")
        response.set_cookie("it_session", state.session, httponly=True, samesite="strict")
        return response

    @app.get("/health")
    def health():
        return {"status": "service_ready", "identity": state.config["identity"], "protocol": 1}

    @app.get("/api/status")
    def status(request: Request):
        client(request)
        with state.lock:
            return {"service_ready": True, "model_ready": state.translator is not None and state.loaded_spec is not None,
                    "acceleration": state.acceleration,
                    "capabilities": ["cancel_by_request_v1", "expected_identity_v1", "job_wait_v1", "video_priority_v1", "convrot_int8_v1"] +
                    (["speech_r2t2_v1"] if speech.available else []),
                    "busy": state.worker_busy, "settings": state.public_settings(), "download": dict(state.download),
                    "cleanup_error": state.cleanup_error,
                    "prompt_version": PROMPT_VERSION, "revision": state.model_revision()}

    @app.get("/api/hardware")
    def resources(request: Request):
        client(request, ui_only=True)
        return hardware()

    @app.get("/api/models")
    def models(request: Request):
        client(request)
        return [{"id": key, "revision": spec["revision"], "bytes": sum(f["size"] for f in spec["files"]),
                 "available": not bool(spec.get("unavailable_reasons")), "reasons": spec.get("unavailable_reasons", [])}
                for key, spec in catalog().items()]

    @app.post("/api/model-directory/pick")
    def pick_model_directory(request: Request):
        client(request, ui_only=True)
        try:
            selected = choose_directory(state.resolve_model())
        except (OSError, RuntimeError, ValueError) as error:
            raise HTTPException(500, str(error)) from error
        return {"path": selected}

    @app.post("/api/settings")
    def settings(value: Settings, request: Request):
        client(request, ui_only=True)
        try:
            return state.configure(value.model_dump())
        except ValueError as error:
            raise HTTPException(409, str(error))

    @app.post("/api/verify")
    def verify(request: Request):
        client(request, ui_only=True)
        try:
            info = inspect_model(state.resolve_model(), state.config["model_id"], verify=True)
            return {key: value for key, value in info.items() if key != "spec"}
        except (ValueError, OSError) as error:
            raise HTTPException(400, str(error))

    @app.post('/api/warmup', status_code=202)
    def warmup(request: Request):
        owner = client(request, ui_only=True)
        import hashlib
        text = 'Hello, world.'
        try:
            return state.submit(owner, {'request_id': secrets.token_hex(16), 'page_epoch': 'warmup',
                'source': 'en', 'target': 'zh', 'output_budget': 64, 'glossary': {},
                'paragraphs': [{'id': 'warmup', 'text': text, 'source_hash': hashlib.sha256(text.encode()).hexdigest()}]})
        except OverflowError as error:
            raise HTTPException(429, str(error))
        except ValueError as error:
            raise HTTPException(400, str(error))

    @app.post("/api/pair-code")
    def pair_code(request: Request):
        client(request, ui_only=True)
        return state.new_pair_code()

    @app.post("/api/pair")
    def pair(value: Pair, request: Request):
        origin = request.headers.get("origin")
        if origin and origin != "chrome-extension://" + value.extension_id:
            raise HTTPException(403, "Extension origin rejected")
        try:
            return state.pair(value.code, value.extension_id)
        except ValueError as error:
            raise HTTPException(401, str(error))

    @app.delete("/api/clients")
    def revoke(request: Request):
        client(request, ui_only=True)
        state.revoke_clients()
        return {"revoked": True}

    @app.post("/api/jobs", status_code=202)
    def submit(value: JobRequest, request: Request):
        owner = client(request)
        try:
            return state.submit(owner, value.model_dump(exclude_none=True))
        except OverflowError as error:
            raise HTTPException(429, str(error))
        except ValueError as error:
            raise HTTPException(400, str(error))

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, request: Request, wait_ms: int = Query(default=0, ge=0, le=20000)):
        try:
            owner = client(request)
            result = state.wait_job(owner, job_id, wait_ms)
            client(request)  # Revocation/identity may have changed while waiting.
            return result
        except KeyError:
            raise HTTPException(404, "任务不存在")

    @app.delete("/api/jobs/{job_id}")
    def cancel_job(job_id: str, request: Request):
        try:
            return state.cancel_job(client(request), job_id)
        except KeyError:
            raise HTTPException(404, "任务不存在")

    @app.post('/api/jobs/{job_id}/priority')
    def promote_video_job(job_id: str, request: Request):
        try:
            return state.promote_video_job(client(request), job_id)
        except KeyError:
            raise HTTPException(404, '任务不存在')
        except ValueError as error:
            raise HTTPException(400, str(error))

    @app.get("/api/requests/{request_id:path}")
    def get_request(request: Request, request_id: str = ApiPath(min_length=8, max_length=128)):
        try:
            return state.get_request(client(request), request_id)
        except KeyError:
            raise HTTPException(404, "任务不存在")

    @app.delete("/api/requests/{request_id:path}")
    def cancel_request(request: Request, request_id: str = ApiPath(min_length=8, max_length=128)):
        return state.cancel_request(client(request), request_id)

    @app.post("/api/unload")
    def unload(request: Request):
        client(request, ui_only=True)
        try:
            speech.close()
            state.unload()
            return {"model_ready": False}
        except ValueError as error:
            raise HTTPException(409, str(error))

    @app.get("/api/speech/settings")
    def speech_settings(request: Request):
        client(request, ui_only=True)
        return speech.settings()

    @app.post("/api/speech/settings")
    def configure_speech(value: SpeechSettings, request: Request):
        client(request, ui_only=True)
        return speech_call(lambda: speech.configure(value.model_path.strip()))

    @app.post("/api/speech/verify")
    def verify_speech(value: SpeechSettings, request: Request):
        client(request, ui_only=True)
        return speech_call(lambda: speech.verify(value.model_path.strip()))

    @app.post("/api/speech/model-directory/pick")
    def pick_speech_directory(request: Request):
        client(request, ui_only=True)
        return speech_call(lambda: {"path": choose_directory(speech.resolve_model(), "选择 R2T2 语音识别模型文件夹")})

    @app.post("/api/speech/start")
    def speech_start(request: Request):
        owner = client(request)
        return speech_call(lambda: speech.start(owner))

    @app.post("/api/speech/{sid}/feed")
    def speech_feed(sid: str, value: SpeechFeed, request: Request):
        owner = client(request)
        return speech_call(lambda: speech.feed(owner, sid, value.seq,
                           value.start_sample, value.pcm_f32le_b64))

    @app.post("/api/speech/{sid}/finish")
    def speech_finish(sid: str, value: SpeechFinish, request: Request):
        owner = client(request)
        return speech_call(lambda: speech.finish(owner, sid, value.last_seq, value.total_samples))

    @app.post("/api/speech/{sid}/cancel")
    def speech_cancel(sid: str, request: Request):
        owner = client(request)
        return speech_call(lambda: speech.cancel(owner, sid))

    @app.post("/api/download")
    def download(value: Download, request: Request):
        client(request, ui_only=True)
        try:
            return state.begin_download(value.model_id, value.source)
        except ValueError as error:
            raise HTTPException(409, str(error))

    @app.delete("/api/download")
    def cancel_download(request: Request):
        client(request, ui_only=True)
        state.download_cancel.set()
        return {"cancel_requested": True}

    app.mount("/static", StaticFiles(directory=Path(root) / "web"), name="static")
    return app
