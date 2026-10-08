"""Optional local Qwen3.5 kernels, with a CPU reference path.

The dependency set is installed and pinned by the product, never downloaded here.
No Transformers files are changed; both independent products vendor this adapter.
"""
from __future__ import annotations

import importlib.metadata
import logging
import sys
import threading

_lock = threading.Lock()
_report = None
_dlls = []
_fast_disabled = False


def _recoverable_kernel_error(error):
    # An optional compiler/DLL/kernel failure can use PyTorch. Retrying after
    # OOM or a poisoned CUDA context would hide the actual resource failure.
    import torch
    message = str(error).lower()
    return not isinstance(error, torch.cuda.OutOfMemoryError) and not any(
        marker in message for marker in ('out of memory', 'illegal memory access',
                                         'device-side assert', 'unspecified launch failure'))


def _disable_fast(error):
    global _fast_disabled
    if not _recoverable_kernel_error(error):
        raise error
    _fast_disabled = True
    if _report is not None:
        _report.update(enabled=False, runtime_fallback=str(error)[:500])
    logging.getLogger(__name__).warning('Optional translation kernels disabled: %s', error)


def kernel_status():
    return dict(_report or {})


def _package_version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return 'unknown (distribution metadata absent)'


def supports_static_qwen_cache():
    from transformers.cache_utils import Cache
    from transformers.models.qwen3_5 import modeling_qwen3_5 as qwen
    return not hasattr(qwen, 'Qwen3_5DynamicCache') and callable(getattr(Cache, 'has_previous_state', None))


def _use_torch_reference(qwen):
    if hasattr(qwen, 'Qwen3_5DynamicCache'):
        # Legacy HF chooses these globals in every layer's constructor. A
        # partially installed FLA package can otherwise route CPU into Triton.
        for name in ('causal_conv1d_fn', 'causal_conv1d_update', 'chunk_gated_delta_rule',
                     'fused_recurrent_gated_delta_rule', 'FusedRMSNormGated'):
            if hasattr(qwen, name):
                setattr(qwen, name, None)
        qwen.is_fast_path_available = False
    else:
        # HF may dispatch straight to an installed optional package. Disable
        # that dispatch too when our adapter cannot initialize it safely.
        for name in ('torch_chunk_gated_delta_rule', 'torch_recurrent_gated_delta_rule',
                     'causal_conv1d_fn', 'causal_conv1d_update'):
            function = getattr(qwen, name, None)
            if function is not None:
                setattr(qwen, name, getattr(function, '__wrapped__', function))


def preload_int8_libraries():
    """Load the wheel's cuBLAS before Kitchen probes its optional support."""
    import sys
    if sys.platform != 'win32' or _dlls:
        return
    import ctypes
    import importlib.util
    from pathlib import Path
    try:
        spec = importlib.util.find_spec('nvidia.cu13')
    except ModuleNotFoundError:
        return
    for location in (spec.submodule_search_locations if spec else []) or []:
        binary = Path(location) / 'bin/x86_64/cublasLt64_13.dll'
        if binary.is_file():
            _dlls.append(ctypes.WinDLL(str(binary.resolve())))


def enable_fused_decode(model) -> dict:
    """Use Kitchen's native decode only for its supported cached one-token case."""
    import types
    import torch
    if not supports_static_qwen_cache():
        return {'enabled': False, 'reason': 'legacy Qwen cache uses reference decode'}
    try:
        preload_int8_libraries()
        import comfy_kitchen as kitchen
    except Exception as error:
        if not _recoverable_kernel_error(error):
            raise
        return {'enabled': False, 'reason': str(error)[:500]}
    from transformers.models.qwen3_5 import modeling_qwen3_5 as qwen
    if not kitchen.gated_delta_decode_is_available():
        return {'enabled': False, 'reason': 'native gated delta unavailable'}
    count = 0
    for module in model.modules():
        if not isinstance(module, qwen.Qwen3_5GatedDeltaNet) or hasattr(module, '_index_original_forward'):
            continue
        if (module.head_k_dim != 128 or module.head_v_dim % 32
                or not kitchen.gated_delta_decode_is_available(module.in_proj_a.weight.device,
                                                              module.head_k_dim, module.head_v_dim)):
            continue
        original = module.forward
        module._index_original_forward = original
        module._index_dt_bias = module.dt_bias.detach().float().contiguous()
        module._index_g_decay = -module.A_log.detach().float().exp().contiguous()

        def forward(self, hidden_states, cache_params=None, attention_mask=None, **kwargs):
            if (hidden_states.shape[1] != 1 or not hidden_states.is_cuda
                    or hidden_states.dtype not in (torch.bfloat16, torch.float16)
                    or cache_params is None or not cache_params.has_previous_state(self.layer_idx, state_idx=0)
                    or cache_params.layers[self.layer_idx].record_past):
                return self._index_original_forward(hidden_states, cache_params, attention_mask, **kwargs)
            hidden_states = qwen.apply_mask_to_padding_states(hidden_states, attention_mask)
            batch = hidden_states.shape[0]
            mixed = self.in_proj_qkv(hidden_states).transpose(1, 2)
            z = self.in_proj_z(hidden_states).reshape(batch, 1, self.num_v_heads, self.head_v_dim)
            layer = cache_params.layers[self.layer_idx]
            mixed = qwen.causal_conv1d_update(mixed, layer.conv_states[0], self.conv1d.weight.squeeze(1),
                                             self.conv1d.bias, self.activation)
            state = layer.recurrent_states[0]
            if state.dtype != torch.float32 or not state.is_contiguous():
                state = state.float().contiguous()
            out = kitchen.gated_delta_decode_fused(
                mixed, hidden_states, self.in_proj_a.weight, self.in_proj_b.weight,
                self._index_dt_bias, self._index_g_decay, state, self.key_dim, self.num_k_heads,
                self.head_k_dim ** -0.5, z, self.norm.weight, self.layer_norm_epsilon)
            cache_params.update_recurrent_state(state, self.layer_idx)
            return self.out_proj(out.reshape(batch, 1, self.value_dim))

        module.forward = types.MethodType(forward, module)
        count += 1
    return {'enabled': bool(count), 'layers': count, 'backend': 'comfy-kitchen-native',
            'scope': 'single-token cached decode', 'prefill': 'fla-triton'}


def enable_fast_kernels() -> dict:
    global _report
    with _lock:
        if _report is not None:
            if not _report.get('enabled'):
                existing_qwen = sys.modules.get('transformers.models.qwen3_5.modeling_qwen3_5')
                if existing_qwen is not None:
                    _use_torch_reference(existing_qwen)
            return dict(_report)
        qwen = None
        try:
            import torch
            from .runtime_environment import prepare_runtime_environment
            environment = prepare_runtime_environment()
            from transformers.models.qwen3_5 import modeling_qwen3_5 as qwen
            # Older HF versions have a different cache and optional conv=None.
            # Leave their complete built-in PyTorch fallback intact rather than
            # injecting an adapter that assumes the newer reference functions.
            if not supports_static_qwen_cache() or not all(callable(getattr(qwen, name, None)) for name in (
                    'torch_chunk_gated_delta_rule', 'torch_recurrent_gated_delta_rule',
                    'causal_conv1d_fn', 'causal_conv1d_update')):
                _report = {'enabled': False, 'cpu_reference': True,
                           'reason': 'legacy Qwen API: using built-in PyTorch inference'}
                _use_torch_reference(qwen)
                return dict(_report)
            # Probe the optional driver before FLA imports/cache device state.
            # A missing C compiler must select the reference implementation
            # here, rather than leaving FLA initialized with a CPU backend.
            if torch.cuda.is_available():
                from triton.runtime import driver
                driver.active.get_current_target()
            from fla.modules.conv import causal_conv1d
            from fla.modules.conv.triton.kernels import causal_conv1d_update
            from fla.ops.gated_delta_rule import chunk_gated_delta_rule, fused_recurrent_gated_delta_rule
        except Exception as error:
            if not _recoverable_kernel_error(error):
                raise
            _report = {"enabled": False, "reason": str(error)[:500], "cpu_reference": True}
            if qwen is None:
                qwen = sys.modules.get('transformers.models.qwen3_5.modeling_qwen3_5')
            if qwen is not None:
                _use_torch_reference(qwen)
            return dict(_report)

        # __wrapped__ is the readable reference beneath HF's package dispatch.
        # FLA's package dispatch itself does not provide a CPU implementation.
        ref_chunk = getattr(qwen.torch_chunk_gated_delta_rule, '__wrapped__', qwen.torch_chunk_gated_delta_rule)
        ref_recurrent = getattr(qwen.torch_recurrent_gated_delta_rule, '__wrapped__', qwen.torch_recurrent_gated_delta_rule)
        ref_conv = getattr(qwen.causal_conv1d_fn, '__wrapped__', qwen.causal_conv1d_fn)
        ref_update = getattr(qwen.causal_conv1d_update, '__wrapped__', qwen.causal_conv1d_update)

        def supported(tensor):
            return not _fast_disabled and tensor.is_cuda and tensor.dtype in (torch.float16, torch.bfloat16)

        def chunk(query, key, value, g, beta, chunk_size=64, initial_state=None,
                  output_final_state=False, use_qk_l2norm_in_kernel=False, **kwargs):
            if not supported(query):
                return ref_chunk(query, key, value, g, beta, chunk_size, initial_state,
                                 output_final_state, use_qk_l2norm_in_kernel, **kwargs)
            try:
                return chunk_gated_delta_rule(query, key, value, g=g, beta=beta,
                                              initial_state=initial_state, output_final_state=output_final_state,
                                              use_qk_l2norm_in_kernel=use_qk_l2norm_in_kernel,
                                              cu_seqlens=kwargs.get('cu_seqlens'))
            except Exception as error:
                _disable_fast(error)
                return ref_chunk(query, key, value, g, beta, chunk_size, initial_state,
                                 output_final_state, use_qk_l2norm_in_kernel, **kwargs)

        def recurrent(query, key, value, g, beta, initial_state=None,
                      output_final_state=False, use_qk_l2norm_in_kernel=False, **kwargs):
            if not supported(query):
                return ref_recurrent(query, key, value, g, beta, initial_state,
                                     output_final_state, use_qk_l2norm_in_kernel, **kwargs)
            try:
                return fused_recurrent_gated_delta_rule(query, key, value, g=g, beta=beta,
                                                       initial_state=initial_state, output_final_state=output_final_state,
                                                       use_qk_l2norm_in_kernel=use_qk_l2norm_in_kernel,
                                                       cu_seqlens=kwargs.get('cu_seqlens'))
            except Exception as error:
                _disable_fast(error)
                return ref_recurrent(query, key, value, g, beta, initial_state,
                                     output_final_state, use_qk_l2norm_in_kernel, **kwargs)

        def conv(hidden_states=None, weight=None, bias=None, activation=None, **kwargs):
            # causal-conv1d/HF releases also call this operation with x=.
            if hidden_states is None:
                hidden_states = kwargs.pop('x', None)
            if hidden_states is None or weight is None:
                raise TypeError('causal convolution requires hidden_states (or x) and weight')
            if not supported(hidden_states) or activation not in (None, 'silu', 'swish'):
                return ref_conv(hidden_states, weight, bias, activation, **kwargs)
            try:
                value, _ = causal_conv1d(hidden_states.transpose(1, 2), weight=weight, bias=bias,
                                        activation=activation, backend='triton', output_final_state=False)
            except Exception as error:
                _disable_fast(error)
                return ref_conv(hidden_states, weight, bias, activation, **kwargs)
            return value.transpose(1, 2)

        def update(hidden_states, conv_state, weight, bias=None, activation=None):
            if (not supported(hidden_states) or hidden_states.shape[-1] != 1
                    or conv_state.shape[-1] != weight.shape[-1]
                    or activation not in (None, 'silu', 'swish')):
                return ref_update(hidden_states, conv_state, weight, bias, activation)
            # The optional kernel mutates conv_state. Preserve it so a failed
            # launch cannot cause the reference retry to advance it twice.
            before = conv_state.clone()
            try:
                value, _ = causal_conv1d_update(hidden_states.transpose(1, 2), cache=conv_state,
                                               weight=weight, bias=bias, activation=activation)
            except Exception as error:
                _disable_fast(error)
                conv_state.copy_(before)
                return ref_update(hidden_states, conv_state, weight, bias, activation)
            return value.transpose(1, 2)

        qwen.torch_chunk_gated_delta_rule = chunk
        qwen.torch_recurrent_gated_delta_rule = recurrent
        qwen.causal_conv1d_fn = conv
        qwen.causal_conv1d_update = update
        _report = {"enabled": True, "gated_delta": "fla-triton", "causal_conv": "fla-triton",
                   "cpu_reference": True, "fla_core": _package_version('fla-core'),
                   "triton": __import__('triton').__version__, "environment": environment}
        return dict(_report)
