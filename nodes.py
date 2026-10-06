import hashlib
import importlib.util
import json
import sys
import threading
import os
import tempfile
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


def read_srt_file(value):
    from comfyui_index_translate_core.subtitles import decode_srt, MAX_SRT_BYTES
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path(folder_paths.get_input_directory()) / path
    with path.open('rb') as stream:
        return decode_srt(stream.read(MAX_SRT_BYTES + 1))


class IndexTranslateSRT(IndexTranslateText):
    @classmethod
    def INPUT_TYPES(cls):
        inputs = super().INPUT_TYPES()
        inputs['required'].pop('text')
        inputs['required']['srt_text'] = ('STRING', {'multiline': True,
            'default': '1\n00:00:00,000 --> 00:00:02,000\n你好，世界。\n\n2\n00:00:02,500 --> 00:00:05,000\n欢迎使用本地字幕翻译。\n',
            'tooltip': '粘贴完整 SRT；仅翻译正文，保留序号和时间轴。'})
        inputs['required']['source'] = ('STRING', {'default': 'zh'})
        inputs['required']['output_budget'] = ('INT', {'default': 256, 'min': 1, 'max': 32704,
                                                        'tooltip': '每条字幕的输出预算。'})
        inputs['required']['bilingual'] = ('BOOLEAN', {'default': False})
        inputs['required']['save_file'] = ('BOOLEAN', {'default': True})
        inputs['required']['filename_prefix'] = ('STRING', {'default': 'IndexTranslate/subtitles',
                                                           'tooltip': '保存在 ComfyUI output 目录。'})
        inputs['optional']['srt_file'] = ('STRING', {'default': '',
            'tooltip': '有值时读取此文件，忽略 srt_text；相对路径位于 ComfyUI input 目录。'})
        return inputs

    RETURN_TYPES = ('STRING', 'STRING', 'STRING')
    RETURN_NAMES = ('translated_srt', 'file_path', 'metadata')
    FUNCTION = 'translate_subtitles'
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, model_path, srt_file='', **kwargs):
        signature = super().IS_CHANGED(model_path, **kwargs)
        if srt_file.strip():
            text = read_srt_file(srt_file.strip())
            signature += hashlib.sha256(text.encode('utf-8')).hexdigest()
        return signature

    def translate_subtitles(self, srt_text, model_path, source, target, device, precision,
                            output_budget, context_limit, bilingual=False, save_file=True,
                            filename_prefix='IndexTranslate/subtitles', glossary='{}', srt_file=''):
        from comfyui_index_translate_core.inference import LocalTranslator
        from comfyui_index_translate_core.subtitles import parse_srt, translate_srt
        from comfy.utils import ProgressBar
        def check_cancel():
            model_management.throw_exception_if_processing_interrupted()
        check_cancel()
        text = read_srt_file(srt_file.strip()) if srt_file.strip() else srt_text
        cues = parse_srt(text)  # Reject malformed files before loading weights.
        terms = json.loads(glossary or '{}')
        if not isinstance(terms, dict):
            raise ValueError('术语表必须是 JSON 对象')
        progress = ProgressBar(len(cues))
        with _lock:
            check_cancel()
            translator = None
            try:
                translator = LocalTranslator(model_directory(model_path), device, precision, context_limit)
                result, metadata = translate_srt(text,
                    lambda cue: translator.translate(cue, source, target, output_budget, terms, check_cancel),
                    bilingual=bilingual, cancel_check=check_cancel,
                    progress=lambda done, total: progress.update_absolute(done, total))
            finally:
                if translator is not None:
                    translator.release()
        check_cancel()
        destination = ''
        if save_file:
            folder, filename, counter, _, _ = folder_paths.get_save_image_path(
                filename_prefix, folder_paths.get_output_directory())
            output = Path(folder) / f'{filename}_{counter:05d}.srt'
            descriptor, temporary = tempfile.mkstemp(prefix='.srt-', suffix='.tmp', dir=folder)
            try:
                with os.fdopen(descriptor, 'wb') as stream:
                    stream.write(result.encode('utf-8-sig'))
                check_cancel()
                # Preserve an existing SRT even if another writer used the
                # selected counter after the directory was scanned.
                while output.exists():
                    counter += 1
                    output = Path(folder) / f'{filename}_{counter:05d}.srt'
                os.rename(temporary, output)
                destination = str(output.resolve())
            finally:
                Path(temporary).unlink(missing_ok=True)
        metadata.update(source=source, target=target, output_budget_per_cue=output_budget,
                        file_path=destination)
        return {'ui': {'text': [destination or '字幕翻译完成']},
                'result': (result, destination, json.dumps(metadata, ensure_ascii=False))}
