import hashlib
import importlib.util
import json
import sys
import threading
from pathlib import Path

import folder_paths
import comfy.model_management as model_management

_lock = threading.RLock()
_core_directory = Path(__file__).parent / "_vendor/index_translate_core"
_module_name = "comfyui_index_translate_core"
if _module_name not in sys.modules:
    _core_spec = importlib.util.spec_from_file_location(
        _module_name, _core_directory / "__init__.py", submodule_search_locations=[str(_core_directory)])
    _core = importlib.util.module_from_spec(_core_spec)
    sys.modules[_module_name] = _core
    _core_spec.loader.exec_module(_core)

folder_paths.add_model_folder_path("index_translate", str(Path(folder_paths.models_dir) / "index_translate"))


def model_directory(value):
    path = Path(value).expanduser()
    if not path.is_absolute():
        root = (Path(folder_paths.models_dir) / "index_translate").resolve()
        path = (root / path).resolve()
        if not path.is_relative_to(root):
            raise ValueError("相对模型目录必须位于models/index_translate")
    return path.resolve()


class IndexTranslateText:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "text": ("STRING", {"multiline": True, "default": "你好，世界。"}),
            "model_path": ("STRING", {"default": "Index-Translate-2B"}),
            "source": ("STRING", {"default": "auto"}),
            "target": ("STRING", {"default": "en"}),
            "device": (["auto", "cuda", "cpu"],),
            "precision": (["auto", "bf16", "fp32", "nf4", "convrot-int8"],),
            "output_budget": ("INT", {"default": 1024, "min": 1, "max": 32704}),
            "context_limit": ("INT", {"default": 4096, "min": 256, "max": 32768}),
        }, "optional": {"glossary": ("STRING", {"multiline": True, "default": "{}"})}}

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("translation", "metadata")
    FUNCTION = "translate"
    CATEGORY = "Index Translate"

    @classmethod
    def IS_CHANGED(cls, model_path, **kwargs):
        root = model_directory(model_path)
        signature = [(file.name, file.stat().st_size, file.stat().st_mtime_ns)
                     for file in sorted(root.glob("*")) if file.is_file() and not file.name.startswith(".")]
        core_hash = [(file.name, hashlib.sha256(file.read_bytes()).hexdigest())
                     for file in sorted(_core_directory.iterdir())
                     if file.is_file() and file.suffix in ('.py', '.json')]
        return hashlib.sha256(json.dumps([signature, core_hash]).encode()).hexdigest()

    def translate(self, text, model_path, source, target, device, precision,
                  output_budget, context_limit, glossary="{}"):
        try:
            from comfyui_index_translate_core.inference import LocalTranslator
        except ImportError as error:
            raise RuntimeError("缺少节点推理依赖，请用当前ComfyUI的Python安装节点requirements.txt；不要更换torch") from error
        path = model_directory(model_path)
        terms = json.loads(glossary or "{}")
        def check_cancel():
            model_management.throw_exception_if_processing_interrupted()
            return False
        with _lock:
            check_cancel()
            translator = None
            try:
                translator = LocalTranslator(path, device, precision, context_limit)
                result = translator.translate(text, source, target, output_budget, terms, check_cancel)
                if result["status"] != "completed":
                    raise RuntimeError("译文达到输出预算而被截断，请提高预算或缩短输入")
                return result["text"], json.dumps({key: value for key, value in result.items() if key != "text"}, ensure_ascii=False)
            finally:
                if translator is not None:
                    translator.release()
