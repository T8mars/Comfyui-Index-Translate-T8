# Standalone R2T2 speech component

The streaming worker and audio resampler were adapted from
[T8mars/comfyui-confucius-r2t2-t8](https://github.com/T8mars/comfyui-confucius-r2t2-t8).
They run under the Windows bundle's private Python; ComfyUI is not needed.

`r2t2_core/` and `bridge.py` are Apache-2.0 code (see `CODE_LICENSE`).
`audio-worklet.js` in the Chrome extension is from the same project and is
also Apache-2.0. The compiled native library includes code from llama.cpp;
see `NATIVE_NOTICE.md` and `LLAMA_CPP_LICENSE`.

The Confucius4-R2T2 GGUF model and projector have a separate NetEase Youdao
Model Use License Agreement, included as `models/Confucius4-R2T2-GGUF/MODEL_LICENSE`
and `MODEL_LICENSE_zh`. The FireRedVAD files have their own Apache license
in `models/FireRedVAD-ONNX/LICENSE`.

This prebuilt native CUDA component was verified on Windows 11 with an RTX 5090
Laptop GPU. The speech model currently recognizes the languages listed by the
R2T2 upstream implementation; hardware outside this tested configuration may
require a different native build. The Chrome extension translates finalized
speech segments and shows provisional recognition while a segment is active.
