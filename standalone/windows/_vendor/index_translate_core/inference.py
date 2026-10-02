from __future__ import annotations

import gc
import json
import re
import threading
import time
from pathlib import Path

from .models import inspect_model

LANGUAGES = {"auto": "自动", "zh": "中文", "en": "英语", "ja": "日语", "ko": "韩语",
             "de": "德语", "fr": "法语", "es": "西班牙语", "ru": "俄语", "ar": "阿拉伯语",
             "pt": "葡萄牙语", "it": "意大利语", "vi": "越南语", "th": "泰语", "hi": "印地语"}
PROMPT_VERSION = "index-official-v1"


class TranslationCancelled(Exception):
    pass


class ResourceUnavailable(Exception):
    pass


def build_prompt(text: str, source: str, target: str, glossary: dict | None = None) -> str:
    if not text.strip():
        raise ValueError("请输入要翻译的文本")
    if target == "auto" or not target.strip() or len(target) > 64 or len(source) > 64:
        raise ValueError("请选择明确的目标语言")
    src = "" if source == "auto" else LANGUAGES.get(source, source)
    dst = LANGUAGES.get(target, target)
    prefix = f"请将以下{src}文本翻译为{dst}，直接输出翻译结果，不要进行任何解释。"
    if glossary:
        if not isinstance(glossary, dict) or len(glossary) > 100:
            raise ValueError("术语表需为最多100项的JSON对象")
        prefix += "\n使用以下术语对应关系：" + json.dumps(glossary, ensure_ascii=False)
    placeholders = re.findall(r"%%IT_[A-Za-z0-9_]+%%", text)
    if placeholders:
        prefix += "\n原样保留所有以%%IT_开头的占位符，不要修改、删除或重复。"
    return prefix + "\n\n" + text.strip()


def hardware() -> dict:
    import psutil
    import torch
    data = {"ram_available_bytes": psutil.virtual_memory().available,
            "ram_total_bytes": psutil.virtual_memory().total, "torch": torch.__version__,
            "cuda_available": torch.cuda.is_available(), "gpus": []}
    if torch.cuda.is_available():
        for device in range(torch.cuda.device_count()):
            free, total = torch.cuda.mem_get_info(device)
            data["gpus"].append({"index": device, "name": torch.cuda.get_device_name(device),
                                 "capability": list(torch.cuda.get_device_capability(device)),
                                 "free_bytes": free, "total_bytes": total,
                                 "process_allocated_bytes": torch.cuda.memory_allocated(device),
                                 "process_reserved_bytes": torch.cuda.memory_reserved(device)})
    return data


class LocalTranslator:
    def __init__(self, model_path: str | Path, device: str = "auto", precision: str = "auto",
                 context_limit: int = 4096, cancel: threading.Event | None = None):
        import torch
        from transformers import AutoTokenizer, Qwen3_5ForConditionalGeneration
        self.lock = threading.Lock()
        self.model = None
        self.tokenizer = None
        self.cancel = cancel or threading.Event()
        self.path = str(Path(model_path).resolve())
        self.model_info = inspect_model(self.path)
        if not self.model_info["complete"]:
            raise ValueError("模型不完整：" + "；".join(self.model_info["problems"]))
        self.context_limit = context_limit
        spec = self.model_info["spec"]
        info = hardware()
        if precision not in ("auto", "bf16", "fp32", "nf4"):
            raise ValueError("精度需为 auto、bf16、fp32 或 nf4")
        storage_bytes = .65 if precision == "nf4" else (4 if precision == "fp32" else 2)
        gpu_bytes = int(spec["parameters"] * storage_bytes) + 2 * 1024 ** 3
        cpu_bytes = spec["parameters"] * 4 + 2 * 1024 ** 3
        if device == "auto":
            device = "cuda" if precision == "nf4" or (info["gpus"] and info["gpus"][0]["free_bytes"] >= gpu_bytes) else "cpu"
        if device not in ("cpu", "cuda"):
            raise ValueError("设备需为auto、cpu或cuda")
        if device == "cuda" and (not info["gpus"] or info["gpus"][0]["free_bytes"] < gpu_bytes):
            raise ResourceUnavailable(f"CUDA 空闲显存不足，建议至少 {gpu_bytes / 1024 ** 3:.1f} GiB；可切换 CPU")
        self.device = device
        if precision == "auto":
            precision = "bf16" if device == "cuda" else "fp32"
        if precision not in ("bf16", "fp32", "nf4"):
            raise ValueError("精度需为 auto、bf16、fp32 或 nf4")
        if precision == "nf4" and device != "cuda":
            raise ValueError("NF4 档位仅支持 NVIDIA CUDA；CPU 请使用 bf16/fp32")
        if device == "cpu" and info["ram_available_bytes"] < (cpu_bytes if precision == "fp32" else gpu_bytes):
            raise ResourceUnavailable("空闲内存不足，请在其他大内存任务结束后重试")
        self.precision = precision
        if self.cancel.is_set():
            raise TranslationCancelled("加载已取消")
        started = time.monotonic()
        try:
            self.tokenizer = AutoTokenizer.from_pretrained(self.path, local_files_only=True, trust_remote_code=False)
            quantization = None
            if precision == "nf4":
                from transformers import BitsAndBytesConfig
                quantization = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                                  bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True)
            self.model, loading = Qwen3_5ForConditionalGeneration.from_pretrained(
                self.path, local_files_only=True, trust_remote_code=False,
                dtype=torch.bfloat16 if precision in ("bf16", "nf4") else torch.float32,
                quantization_config=quantization,
                device_map=device, attn_implementation="sdpa", output_loading_info=True)
            self.model.eval()
            self.loading_info = loading
            unexplained = {key: loading.get(key, []) for key in ("missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs")}
            if any(unexplained.values()):
                raise ValueError("模型加载键不匹配：" + json.dumps(unexplained)[:2000])
        except BaseException:
            self.release()
            raise
        self.load_seconds = round(time.monotonic() - started, 3)
        if self.cancel.is_set():
            self.release()
            raise TranslationCancelled("加载已取消")

    def translate(self, text: str, source: str = "auto", target: str = "zh",
                  output_budget: int = 1024, glossary: dict | None = None, cancel_check=None) -> dict:
        import torch
        from transformers import StoppingCriteria, StoppingCriteriaList
        def cancelled():
            if self.cancel.is_set():
                return True
            return bool(cancel_check and cancel_check())
        class StopOnCancel(StoppingCriteria):
            def __call__(self, input_ids, scores, **kwargs):
                return cancelled()
        if cancelled():
            raise TranslationCancelled("翻译已取消")
        if not 1 <= output_budget <= self.context_limit - 64:
            raise ValueError("输出预算超出上下文限制")
        prompt = build_prompt(text, source, target, glossary)
        inputs = self.tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}], add_generation_prompt=True,
            enable_thinking=False, tokenize=True, return_dict=True, return_tensors="pt")
        input_length = inputs["input_ids"].shape[-1]
        if input_length + output_budget > self.context_limit:
            raise ValueError(f"输入 {input_length} + 输出预算 {output_budget} 超过上下文 {self.context_limit}，请缩短文本")
        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        with self.lock:
            if self.model is None:
                raise ValueError("模型已释放，请重新加载")
            if cancelled():
                raise TranslationCancelled("翻译已取消")
            started = time.monotonic()
            if self.device == "cuda":
                torch.cuda.reset_peak_memory_stats()
            generated = self.model.generate(**inputs, max_new_tokens=output_budget,
                                            do_sample=False,
                                            stopping_criteria=StoppingCriteriaList([StopOnCancel()]))
            if cancelled():
                raise TranslationCancelled("翻译已取消")
            tokens = generated[0, input_length:].tolist()
            eos = self.model.generation_config.eos_token_id
            eos_ids = eos if isinstance(eos, list) else [eos]
            finished = bool(tokens and tokens[-1] in eos_ids)
            translation = self.tokenizer.decode(tokens, skip_special_tokens=True).strip()
            translation = re.sub(r"^\s*<think>.*?</think>\s*", "", translation, flags=re.S).strip()
            if not translation:
                raise ValueError("模型返回空译文")
            return {"text": translation, "status": "completed" if finished else "truncated",
                    "finish_reason": "stop" if finished else "length", "input_tokens": input_length,
                    "output_tokens": len(tokens), "seconds": round(time.monotonic() - started, 3),
                    "model_id": self.model_info["id"], "model_revision": self.model_info["revision"],
                    "device": self.device, "precision": self.precision,
                    "peak_cuda_bytes": torch.cuda.max_memory_allocated() if self.device == "cuda" else 0,
                    "prompt_version": PROMPT_VERSION}

    def release(self):
        self.model = None
        self.tokenizer = None
        gc.collect()
        import torch
        if self.device == "cuda" and torch.cuda.is_available():
            torch.cuda.synchronize()
            # cuBLAS workspaces can pin an allocator segment after model deletion.
            # Verified for the pinned torch versions; empty_cache alone leaves them live.
            torch._C._cuda_clearCublasWorkspaces()
            torch.cuda.empty_cache()
