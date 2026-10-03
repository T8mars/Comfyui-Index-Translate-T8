"""Checksummed Comfy INT8+ConvRot checkpoints and an independent HF loader."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

MANIFEST = 'index-quantization.json'


def inspect_quantized_model(root, model_id, verify, cancel, models):
    from .models import DownloadCancelled, digest, safe_file
    with (root / MANIFEST).open('rb') as stream:
        raw = stream.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError('量化清单过大')
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get('schema') != 1 or data.get('format') != 'convrot-int8':
        raise ValueError('不支持的量化清单格式')
    base_id = data.get('source_model_id')
    if base_id not in models or (model_id and model_id != base_id):
        raise ValueError('量化模型与所选 2B/9B 型号不匹配')
    spec = models[base_id]
    expected_source = [{'path': v['path'], 'size': v['size'], 'sha256': v['sha256']} for v in spec['files']]
    if data.get('source_revision') != spec['revision'] or data.get('source_files') != expected_source:
        raise ValueError('量化模型来源与固定官方版本不匹配')
    if data.get('recipe') != 'fp32-regular-hadamard-per-channel-absmax-v1':
        raise ValueError('未知的 CONVROT 量化 recipe')
    files, layers = data.get('files'), data.get('quantized_layers')
    if not isinstance(files, list) or not isinstance(layers, list) or not layers or len(files) > 128 or len(layers) > 2048:
        raise ValueError('量化清单缺少完整文件或层信息')
    names = [entry['path'] for entry in files]
    layer_names = [entry['name'] for entry in layers]
    if len(set(names)) != len(names) or len(set(layer_names)) != len(layer_names):
        raise ValueError('量化清单包含重复项')
    required = {v['path'] for v in expected_source if not v['path'].endswith('.safetensors')}
    required.update(v['path'] for v in expected_source if v['path'].endswith('.safetensors'))
    if not required.issubset(names):
        raise ValueError('量化目录缺少官方配置、分词器或完整权重分片')
    by_name = {entry['path']: entry for entry in files}
    for original in expected_source:
        if original['path'].endswith('.safetensors') or original['path'] == 'model.safetensors.index.json':
            continue
        if by_name[original['path']] != original:
            raise ValueError('量化目录配置/分词器被修改：' + original['path'])
    config_sha = next(v['sha256'] for v in expected_source if v['path'] == 'config.json')
    if digest(safe_file(root, 'config.json'), cancel) != config_sha:
        raise ValueError('量化模型 config 与官方模型不匹配')
    revision = spec['revision'] + ':convrot-int8:' + hashlib.sha256(raw).hexdigest()
    try:
        receipt = json.loads((root / '.index-verified.json').read_text('utf-8'))
    except (ValueError, OSError):
        receipt = {}
    if not isinstance(receipt, dict) or not isinstance(receipt.get('files'), dict):
        receipt = {}
    checked, problems = {}, list(spec.get('unavailable_reasons', []))
    for entry in files:
        if cancel and cancel.is_set():
            raise DownloadCancelled('量化模型校验已取消')
        path = safe_file(root, entry['path'])
        if not path.is_file():
            problems.append('缺少 ' + entry['path'])
            continue
        stat = path.stat()
        if stat.st_size != entry['size']:
            problems.append('文件大小错误 ' + entry['path'])
            continue
        stamp = {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'sha256': entry['sha256']}
        cached = receipt.get('revision') == revision and receipt.get('files', {}).get(entry['path']) == stamp
        if (verify or not cached) and digest(path, cancel) != entry['sha256']:
            problems.append('SHA256 校验失败 ' + entry['path'])
            continue
        checked[entry['path']] = stamp
    if not problems:
        import uuid
        temporary = root / ('.index-verified.' + uuid.uuid4().hex + '.tmp')
        try:
            temporary.write_text(json.dumps({'model_id': base_id, 'revision': revision, 'files': checked}), 'utf-8')
            temporary.replace(root / '.index-verified.json')
        except OSError:
            pass
        finally:
            temporary.unlink(missing_ok=True)
    return {'id': base_id, 'revision': revision, 'path': str(root), 'complete': not problems,
            'problems': problems, 'spec': spec, 'quantization': data,
            'weight_bytes': sum(v['size'] for v in files if v['path'].endswith('.safetensors'))}


def load_quantized_model(root, model_info, device, cancel=None):
    import gc
    import torch
    owned = []
    try:
        return _load_quantized_model(root, model_info, device, cancel, owned)
    except BaseException:
        # Exception frames still reference the partially loaded module. Drop its
        # GPU buffers before clearing the allocator, rather than waiting for GC.
        try:
            for model in owned:
                model.to('meta')
            owned.clear()
            gc.collect()
            if torch.cuda.is_initialized():
                torch.cuda.synchronize()
                torch._C._cuda_clearCublasWorkspaces()
                torch.cuda.empty_cache()
        except Exception:
            pass
        raise


def _load_quantized_model(root, model_info, device, cancel, owned):
    import torch
    from torch import nn
    from accelerate import init_empty_weights
    from accelerate.utils import set_module_tensor_to_device
    from safetensors import safe_open
    from transformers import AutoConfig, GenerationConfig, Qwen3_5ForConditionalGeneration
    from .acceleration import preload_int8_libraries
    from .inference import TranslationCancelled
    preload_int8_libraries()
    import comfy_kitchen as kitchen
    from comfy_kitchen.tensor.int8_utils import _build_hadamard
    if device != 'cuda':
        raise ValueError('CONVROT INT8 目前需要 NVIDIA CUDA；CPU 请选择官方 BF16/FP32 模型')
    if 'int8_linear' not in kitchen.list_backends().get('cuda', {}).get('capabilities', {}):
        raise RuntimeError('原生 INT8 CUDA 内核未启用，请使用配套的 comfy-kitchen / cuBLAS 依赖')

    class ConvRotLinear(nn.Module):
        def __init__(self, shape, groupsize):
            super().__init__()
            self.out_features, self.in_features = shape
            self.groupsize = groupsize
            self.register_buffer('weight', torch.empty(shape, device='meta', dtype=torch.int8))
            self.register_buffer('weight_scale', torch.empty((shape[0], 1), device='meta', dtype=torch.float32))
            self.bias = None

        def forward(self, value):
            dtype_code = {torch.float32: 0, torch.float16: 1, torch.bfloat16: 2}[value.dtype]
            # The registered custom op has a fake implementation and a graph-
            # capture boundary. The public Python dispatcher is not traceable.
            return torch.ops.comfy_kitchen.int8_linear(value, self.weight, self.weight_scale,
                                                      None, dtype_code, True, self.groupsize)

    class ConvRotEmbedding(nn.Module):
        def __init__(self, shape, groupsize):
            super().__init__()
            self.num_embeddings, self.embedding_dim = shape
            self.groupsize = groupsize
            self.padding_idx = None
            self.register_buffer('weight', torch.empty(shape, device='meta', dtype=torch.int8))
            self.register_buffer('weight_scale', torch.empty((shape[0], 1), device='meta', dtype=torch.float32))
            self.register_buffer('rotation', _build_hadamard(groupsize, device='cpu', dtype=torch.float32), persistent=False)

        def forward(self, indices):
            selected = nn.functional.embedding(indices, self.weight).float()
            scales = nn.functional.embedding(indices, self.weight_scale)
            value = selected * scales
            return (value.reshape(-1, self.embedding_dim // self.groupsize, self.groupsize)
                    @ self.rotation).reshape(*indices.shape, self.embedding_dim).to(torch.bfloat16)

    root = Path(root)
    config = AutoConfig.from_pretrained(root, local_files_only=True, trust_remote_code=False)
    config._attn_implementation = 'sdpa'
    config.text_config._attn_implementation = 'sdpa'
    config.vision_config._attn_implementation = 'sdpa'
    with init_empty_weights(include_buffers=False):
        model = Qwen3_5ForConditionalGeneration(config)
    owned.append(model)
    layers = {entry['name']: entry for entry in model_info['quantization']['quantized_layers']}
    for name, entry in layers.items():
        old = model.get_submodule(name)
        if entry['groupsize'] not in (16, 64, 256) or entry['shape'][1] % entry['groupsize']:
            raise ValueError('非法 CONVROT 分组：' + name)
        if list(old.weight.shape) != entry['shape'] or getattr(old, 'bias', None) is not None:
            raise ValueError('量化层 shape / bias 不匹配：' + name)
        cls = ConvRotEmbedding if entry['role'] == 'embedding' else ConvRotLinear
        if not isinstance(old, nn.Embedding if cls is ConvRotEmbedding else nn.Linear):
            raise ValueError('量化层类型不匹配：' + name)
        parent, attr = name.rsplit('.', 1) if '.' in name else ('', name)
        setattr(model.get_submodule(parent), attr, cls(entry['shape'], entry['groupsize']))
    embed = model.model.language_model.embed_tokens
    tied = bool(config.tie_word_embeddings and isinstance(embed, ConvRotEmbedding))
    if tied:
        model.lm_head = ConvRotLinear((embed.num_embeddings, embed.embedding_dim), embed.groupsize)
    expected = set(model.state_dict())
    loaded, markers, ignored = set(), set(), []
    for entry in model_info['quantization']['files']:
        if not entry['path'].endswith('.safetensors'):
            continue
        with safe_open(root / entry['path'], framework='pt', device='cpu') as reader:
            for name in reader.keys():
                if cancel and cancel.is_set():
                    raise TranslationCancelled('量化模型加载已取消')
                value = reader.get_tensor(name)
                if name.endswith('.comfy_quant'):
                    base = name.removesuffix('.comfy_quant')
                    marker = json.loads(bytes(value.tolist()))
                    required_marker = {'format': 'int8_tensorwise', 'convrot': True,
                                       'convrot_groupsize': layers[base]['groupsize']}
                    if marker != required_marker or value.dtype != torch.uint8:
                        raise ValueError('量化旋转标记不匹配：' + base)
                    markers.add(base)
                    continue
                if name.startswith('mtp.'):
                    ignored.append(name)
                    continue
                if name not in expected or name in loaded:
                    raise ValueError('未知或重复权重键：' + name)
                parameter = model.state_dict()[name]
                if tuple(parameter.shape) != tuple(value.shape):
                    raise ValueError('权重 shape 不匹配：' + name)
                if parameter.dtype == torch.int8 and value.dtype != torch.int8:
                    raise ValueError('量化权重并非 INT8：' + name)
                if name.endswith('.weight_scale') and (value.dtype != torch.float32 or not bool(value.isfinite().all())
                                                      or bool((value <= 0).any())):
                    raise ValueError('非法量化 scale：' + name)
                dtype = torch.float32 if name.endswith('.weight_scale') else (torch.bfloat16 if value.is_floating_point() else value.dtype)
                set_module_tensor_to_device(model, name, device, value=value, dtype=dtype)
                loaded.add(name)
                del value
    if tied:
        model.lm_head.weight = embed.weight
        model.lm_head.weight_scale = embed.weight_scale
        loaded.update(('lm_head.weight', 'lm_head.weight_scale'))
    missing = expected - loaded
    if missing or markers != set(layers):
        raise ValueError('量化模型不完整：' + str(sorted(missing)) + '; missing markers ' + str(sorted(set(layers) - markers)))
    model.to(device)
    if any(t.is_meta for t in list(model.parameters()) + list(model.buffers())):
        raise ValueError('量化模型仍有未加载的 meta tensor')
    model.generation_config = GenerationConfig.from_pretrained(root, local_files_only=True)
    model.eval()
    model.requires_grad_(False)
    return model, {'missing_keys': [], 'unexpected_keys': [], 'mismatched_keys': [], 'error_msgs': [],
                   'ignored_keys': ignored, 'quantized_layers': len(layers), 'native_int8': True}
