from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from collections import Counter, OrderedDict, deque
from pathlib import Path

from index_translate_core.inference import LocalTranslator, PROMPT_VERSION, TranslationCancelled, hardware
from index_translate_core.models import DownloadCancelled, catalog, download_model, inspect_model, model_revision

PLACEHOLDER = re.compile(r"%%IT_[A-Za-z0-9_]+%%")
TERMINAL = {"completed", "failed", "cancelled"}


def atomic_json(path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), "utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class State:
    def __init__(self, root: Path, translator_factory=LocalTranslator, start_worker=True):
        self.root = root.resolve()
        self.data = self.root / "data"
        self.data.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.wake = threading.Condition(self.lock)
        self.quit = threading.Event()
        self.worker_busy = False
        self.unload_requested = False
        self.unload_error = None
        self.cleanup_error = None
        self.translator_factory = translator_factory
        self.translator = None
        self.loaded_spec = None
        self.acceleration = {'compiled': False}
        self.last_used = time.monotonic()
        self.session = secrets.token_urlsafe(32)
        self.pair_code = None
        self.pair_expiry = 0
        self.pair_attempts = 0
        self.queue = OrderedDict()
        self.video_burst = 0
        self.urgent_jobs = set()
        self.cancels = {}
        self.download = {"status": "idle"}
        self.download_cancel = threading.Event()
        self.config_path = self.data / "settings.json"
        try:
            self.config = json.loads(self.config_path.read_text("utf-8"))
        except FileNotFoundError:
            default_model = 'Index-Translate-2B'
            if (self.root / 'models/Index-Translate-2B-ConvRot-INT8/index-quantization.json').is_file():
                try:
                    gpus = hardware()['gpus']
                    if gpus and gpus[0]['free_bytes'] >= 5 * 1024 ** 3:
                        default_model += '-ConvRot-INT8'
                except (RuntimeError, OSError):
                    pass
            self.config = {"identity": str(uuid.uuid4()), "model_id": "IndexTeam/Index-Translate-2B",
                           "model_path": "models/" + default_model, "device": "auto", "precision": "auto",
                           "context_limit": 4096, "idle_unload_seconds": 600 if default_model.endswith('INT8') else 60, "clients": {}}
            self.save_config()
        self.db = sqlite3.connect(self.data / "jobs.sqlite3", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, client TEXT, request_key TEXT, created REAL, updated REAL, payload TEXT, result TEXT, UNIQUE(client,request_key))")
        self.db.execute("CREATE TABLE IF NOT EXISTS cancelled_requests (client TEXT, request_key TEXT, created REAL, PRIMARY KEY(client,request_key))")
        for job_id, encoded in self.db.execute("SELECT id,result FROM jobs").fetchall():
            result = json.loads(encoded)
            if result["status"] not in TERMINAL:
                result.update(status="failed", error="服务已重启，请重试此任务", error_code="service_restarted")
                self.db.execute("UPDATE jobs SET result=?,updated=? WHERE id=?", (json.dumps(result), time.time(), job_id))
        self.db.execute("DELETE FROM jobs WHERE updated < ?", (time.time() - 3 * 86400,))
        self.db.execute("DELETE FROM cancelled_requests WHERE created < ?", (time.time() - 3 * 86400,))
        self.db.commit()
        self.last_pruned = time.monotonic()
        self.thread = None
        if start_worker:
            self.thread = threading.Thread(target=self.work, name="translation-worker", daemon=True)
            self.thread.start()

    def save_config(self):
        atomic_json(self.config_path, self.config)

    def prune_expired(self):
        with self.lock:
            now = time.monotonic()
            if now - self.last_pruned < 3600:
                return
            cutoff = time.time() - 3 * 86400
            active = tuple(self.cancels)
            if active:
                placeholders = ",".join("?" for _ in active)
                self.db.execute(f"DELETE FROM jobs WHERE updated < ? AND id NOT IN ({placeholders})",
                                (cutoff, *active))
            else:
                self.db.execute("DELETE FROM jobs WHERE updated < ?", (cutoff,))
            self.db.execute("DELETE FROM cancelled_requests WHERE created < ?", (cutoff,))
            self.db.commit()
            self.last_pruned = now

    def replace_config(self, config):
        atomic_json(self.config_path, config)
        self.config = config

    def resolve_model(self, settings=None):
        settings = settings or self.config
        path = Path(settings["model_path"]).expanduser()
        return path.resolve() if path.is_absolute() else (self.root / path).resolve()

    def public_settings(self):
        with self.lock:
            return {key: value for key, value in self.config.items() if key != "clients"}

    def configure(self, settings):
        with self.wake:
            if self.worker_busy or self.queue:
                raise ValueError("请等待当前任务结束或取消后再修改模型设置")
            self.replace_config({**self.config, **settings})
            self.wake.notify_all()
            return self.public_settings()

    def new_pair_code(self):
        with self.lock:
            self.pair_code = secrets.token_urlsafe(12)
            self.pair_expiry = time.monotonic() + 300
            self.pair_attempts = 0
            return {"code": self.pair_code, "expires_seconds": 300}

    def pair(self, code, extension_id):
        with self.lock:
            self.pair_attempts += 1
            if self.pair_attempts > 20 or time.monotonic() > self.pair_expiry or not self.pair_code or not secrets.compare_digest(code, self.pair_code):
                raise ValueError("配对码无效或已过期，请在整合包页面重新生成")
            token = secrets.token_urlsafe(32)
            client = "chrome:" + extension_id
            clients = {**self.config["clients"], hashlib.sha256(token.encode()).hexdigest(): {"id": client, "created": time.time()}}
            self.replace_config({**self.config, "clients": clients})
            self.pair_code = None
            return {"token": token, "identity": self.config["identity"], "client": client}

    def token_client(self, token):
        with self.lock:
            return self.config["clients"].get(hashlib.sha256(token.encode()).hexdigest(), {}).get("id")

    def revoke_clients(self):
        with self.lock:
            self.replace_config({**self.config, "clients": {}})

    def submit(self, client, request):
        with self.wake:
            self.prune_expired()
            if request.get("expected_identity") and request["expected_identity"] != self.config["identity"]:
                raise ValueError("本地服务已更换，请重新提交文本")
            request_key = request["request_id"]
            previous = self.db.execute("SELECT id,payload FROM jobs WHERE client=? AND request_key=?", (client, request_key)).fetchone()
            if previous:
                old = json.loads(previous[1])
                if old["request"] != request:
                    raise ValueError("同一request_id不能提交不同内容")
                return self.get_job(client, previous[0])
            if self.quit.is_set():
                raise ValueError("服务正在关闭，请稍后重试")
            if not request["paragraphs"] or sum(len(p["text"]) for p in request["paragraphs"]) > 48000:
                raise ValueError("请求为空或总长度超过限制")
            ids = [p["id"] for p in request["paragraphs"]]
            if len(ids) != len(set(ids)):
                raise ValueError("段落id重复")
            for paragraph in request["paragraphs"]:
                if hashlib.sha256(paragraph["text"].encode()).hexdigest() != paragraph["source_hash"]:
                    raise ValueError("source_hash与原文不符")
            cancelled = self.db.execute("SELECT 1 FROM cancelled_requests WHERE client=? AND request_key=?", (client, request_key)).fetchone()
            if not cancelled:
                active = self.db.execute("SELECT client,result FROM jobs").fetchall()
                pending = [owner for owner, result in active if json.loads(result)["status"] not in TERMINAL]
                if len(pending) >= 64 or pending.count(client) >= 8:
                    raise OverflowError("翻译队列已满，请等待已有任务完成")
            settings = self.public_settings()
            result = {"id": uuid.uuid4().hex, "status": "queued", "page_epoch": request["page_epoch"],
                      "results": [], "done": 0, "total": len(ids), "error": None,
                      "identity": settings["identity"], "model_revision": self.model_revision(settings),
                      "prompt_version": PROMPT_VERSION,
                      "runtime": {"device": settings["device"], "precision": settings["precision"],
                                  "context_limit": settings["context_limit"], "output_budget": request["output_budget"]}}
            if cancelled:
                result.update(status="cancelled", error="任务已取消")
            payload = {"request": request, "settings": settings}
            now = time.time()
            self.db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?)", (result["id"], client, request_key, now, now, json.dumps(payload), json.dumps(result)))
            self.db.commit()
            if not cancelled:
                self.queue.setdefault((client, request["page_epoch"]), deque()).append(result["id"])
                self.cancels[result["id"]] = threading.Event()
                self.wake.notify_all()
            return result

    def get_request(self, client, request_key):
        with self.lock:
            self.prune_expired()
            row = self.db.execute("SELECT result FROM jobs WHERE client=? AND request_key=?", (client, request_key)).fetchone()
            if not row:
                raise KeyError(request_key)
            return json.loads(row[0])

    def cancel_request(self, client, request_key):
        with self.wake:
            self.prune_expired()
            self.db.execute("INSERT OR REPLACE INTO cancelled_requests VALUES (?,?,?)", (client, request_key, time.time()))
            self.db.commit()
            try:
                result = self.get_request(client, request_key)
            except KeyError:
                return {"request_id": request_key, "status": "cancelled"}
            return self.cancel_job(client, result["id"])

    def get_job(self, client, job_id):
        with self.lock:
            self.prune_expired()
            row = self.db.execute("SELECT result FROM jobs WHERE client=? AND id=?", (client, job_id)).fetchone()
            if not row:
                raise KeyError(job_id)
            return json.loads(row[0])

    def update_job(self, job_id, result):
        with self.wake:
            self.db.execute("UPDATE jobs SET result=?,updated=? WHERE id=?", (json.dumps(result), time.time(), job_id))
            self.db.commit()
            self.wake.notify_all()

    def wait_job(self, client, job_id, wait_ms=0):
        deadline = time.monotonic() + min(max(wait_ms, 0), 20000) / 1000
        with self.wake:
            while True:
                result = self.get_job(client, job_id)
                remaining = deadline - time.monotonic()
                if result['status'] in TERMINAL or remaining <= 0 or self.quit.is_set():
                    return result
                self.wake.wait(timeout=remaining)

    def promote_video_job(self, client, job_id):
        with self.wake:
            result = self.get_job(client, job_id)
            if result['page_epoch'] != 'video-prefetch':
                raise ValueError('只有视频预取任务可以提升优先级')
            if result['status'] not in TERMINAL:
                self.urgent_jobs.add(job_id)
                for items in self.queue.values():
                    if job_id in items:
                        items.remove(job_id)
                        items.appendleft(job_id)
                        break
                self.wake.notify_all()
            return result

    def cancel_job(self, client, job_id):
        with self.wake:
            result = self.get_job(client, job_id)
            if result["status"] in TERMINAL:
                return result
            self.cancels[job_id].set()
            if result["status"] == "queued":
                result.update(status="cancelled", error="任务已取消")
                self.update_job(job_id, result)
            self.wake.notify_all()
            return result

    def unload(self):
        with self.wake:
            if self.worker_busy or (self.queue and threading.current_thread() is not self.thread):
                raise ValueError("请先取消正在运行的任务")
            if self.translator and self.thread and threading.current_thread() is not self.thread:
                self.unload_requested = True
                self.unload_error = None
                self.wake.notify_all()
                if not self.wake.wait_for(lambda: not self.unload_requested, timeout=30):
                    raise ValueError("模型释放仍在处理中，请稍后检查")
                if self.unload_error:
                    raise ValueError("模型释放失败：" + self.unload_error)
                return
            error = self.release_translator()
            if error:
                raise ValueError("模型释放失败：" + error)

    def release_translator(self):
        translator, self.translator = self.translator, None
        self.loaded_spec = None
        self.cleanup_error = None
        if translator is not None:
            try:
                translator.release()
            except Exception as error:
                self.cleanup_error = str(error)[:1800]
        return self.cleanup_error

    def work(self):
        while not self.quit.is_set():
            with self.wake:
                self.prune_expired()
                if self.unload_requested:
                    self.unload_error = self.release_translator()
                    self.unload_requested = False
                    self.wake.notify_all()
                if not self.queue:
                    idle = self.config["idle_unload_seconds"]
                    try:
                        changed = self.loaded_spec and self.loaded_spec != self.model_spec(self.public_settings())
                    except (ValueError, OSError) as error:
                        self.release_translator()
                        self.cleanup_error = '模型清单读取失败：' + str(error)[:500]
                        changed = False
                    if self.translator and (changed or time.monotonic() - self.last_used > idle):
                        self.release_translator()
                    self.wake.wait(timeout=1)
                    continue
                keys = list(self.queue)
                current = [key for key in keys if key[1] == 'video' or self.queue[key][0] in self.urgent_jobs]
                normal = [key for key in keys if key[1] not in ('video', 'video-prefetch')]
                if current and (self.video_burst < 2 or not normal):
                    queue_key = current[0]
                    self.video_burst += 1
                else:
                    queue_key = normal[0] if normal else keys[0]
                    self.video_burst = 0
                items = self.queue.pop(queue_key)
                client = queue_key[0]
                job_id = items.popleft()
                if items:
                    self.queue[queue_key] = items  # Fair round robin between tabs/clients.
                self.worker_busy = True
            try:
                self.run_job(client, job_id, quantum=1)
            except Exception as error:
                result = self.get_job(client, job_id)
                result.update(status='failed', error=str(error)[:1800], error_code=type(error).__name__)
                self.update_job(job_id,result)
                self.release_translator()
            finally:
                with self.wake:
                    self.worker_busy = False
                    self.last_used = time.monotonic()
                    if self.get_job(client, job_id)['status'] in TERMINAL:
                        self.cancels.pop(job_id, None)
                        self.urgent_jobs.discard(job_id)
                    else:
                        self.queue.setdefault(queue_key, deque()).append(job_id)
                    self.wake.notify_all()
        with self.wake:
            self.worker_busy = False
            error = self.release_translator()
            if self.unload_requested:
                self.unload_error = error
            self.unload_requested = False
            self.wake.notify_all()

    def model_spec(self, settings):
        return (str(self.resolve_model(settings)), settings["model_id"], settings["device"], settings["precision"], settings["context_limit"], self.model_revision(settings))

    def model_revision(self, settings=None):
        settings = settings or self.config
        return model_revision(self.resolve_model(settings), settings['model_id'])

    def run_job(self, client, job_id, quantum=0):
        result = self.get_job(client, job_id)
        if result["status"] in TERMINAL:
            return
        with self.lock:
            payload = json.loads(self.db.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()[0])
        request, settings = payload["request"], payload["settings"]
        cancellation = self.cancels[job_id]
        try:
            result["status"] = "loading" if self.loaded_spec != self.model_spec(settings) else "running"
            self.update_job(job_id, result)
            if cancellation.is_set():
                raise TranslationCancelled("任务已取消")
            spec = self.model_spec(settings)
            if result['model_revision'] != spec[-1]:
                raise ValueError('模型版本已变化，请重新提交任务')
            if self.loaded_spec != spec:
                error = self.release_translator()
                if error:
                    raise RuntimeError("旧模型释放失败：" + error)
                information = inspect_model(self.resolve_model(settings), settings["model_id"], cancel=cancellation)
                if not information["complete"]:
                    raise ValueError("模型不完整：" + "；".join(information["problems"]))
                self.translator = self.translator_factory(spec[0], settings["device"], settings["precision"], settings["context_limit"], cancellation)
                if hasattr(self.translator, 'prepare_acceleration'):
                    self.acceleration = self.translator.prepare_acceleration()
                self.loaded_spec = spec
            self.translator.cancel = cancellation
            result["status"] = "running"
            self.update_job(job_id, result)
            pending = request['paragraphs'][result['done']:]
            for paragraph in (pending[:quantum] if quantum else pending):
                translated = self.translator.translate(paragraph["text"], request["source"], request["target"], request["output_budget"], request["glossary"])
                if translated["status"] != "completed":
                    raise ValueError("译文被输出预算截断，请拆分原文或增加预算")
                if Counter(PLACEHOLDER.findall(paragraph["text"])) != Counter(PLACEHOLDER.findall(translated["text"])):
                    raise ValueError("占位符校验失败，保留原文，请重试或缩短段落")
                if cancellation.is_set():
                    raise TranslationCancelled("任务已取消")
                result["results"].append({"id": paragraph["id"], "source_hash": paragraph["source_hash"], **translated})
                result["done"] += 1
                self.update_job(job_id, result)
            result["status"] = "completed" if result['done'] == result['total'] else "queued"
        except (TranslationCancelled, DownloadCancelled) as error:
            result.update(status="cancelled", error=str(error))
        except Exception as error:
            result.update(status="failed", error=str(error)[:1800], error_code=type(error).__name__)
            cleanup_error = self.release_translator()
            if cleanup_error:
                result["cleanup_error"] = cleanup_error
        self.update_job(job_id, result)

    def begin_download(self, model_id, source):
        with self.lock:
            if self.download["status"] in {"downloading", "verifying"}:
                raise ValueError("已有模型正在下载")
            if model_id not in catalog():
                raise ValueError("未知模型")
            directory = self.root / "models" / model_id.split("/")[-1]
            self.download_cancel = threading.Event()
            self.download = {"status": "downloading", "model_id": model_id, "path": str(directory), "completed_bytes": 0,
                             "total_bytes": sum(f["size"] for f in catalog()[model_id]["files"])}
        def progress(event):
            with self.lock:
                self.download.update(event)
        def perform():
            try:
                info = download_model(model_id, directory, source, self.download_cancel, progress)
                with self.lock:
                    if info["complete"]:
                        self.download.update(status="completed", complete=True,
                                             completed_bytes=self.download["total_bytes"])
                    else:
                        self.download.update(status="failed", complete=False,
                                             error="；".join(info["problems"]))
            except DownloadCancelled as error:
                with self.lock:
                    self.download.update(status="cancelled", error=str(error))
            except Exception as error:
                with self.lock:
                    self.download.update(status="failed", error=str(error))
        threading.Thread(target=perform, name="model-download", daemon=True).start()
        return dict(self.download)

    def close(self):
        self.quit.set()
        self.download_cancel.set()
        with self.wake:
            for event in self.cancels.values():
                event.set()
            self.wake.notify_all()
        if self.thread:
            self.thread.join(timeout=30)
        if not self.thread or not self.thread.is_alive():
            self.db.close()
