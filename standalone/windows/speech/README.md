# Standalone R2T2 speech component

The streaming worker and audio resampler were adapted from
[T8mars/comfyui-confucius-r2t2-t8](https://github.com/T8mars/comfyui-confucius-r2t2-t8).
They run under the Windows bundle's private Python; ComfyUI is not needed.

In version 0.1.14, choose the existing R2T2 project, its `models` directory,
or the folder containing the official Q8 decoder and projector under
**Models and devices → Video speech recognition**. The worker opens those files
directly without copying them. Settings persist in `data/speech-settings.json`.
The `reuse-ASR` Windows archive omits the two GGUF weights; VAD and the native
worker remain bundled. The native worker uses the private runtime's CUDA DLLs,
so a system CUDA Toolkit is not needed for this prebuilt bundle.

`r2t2_core/` and `bridge.py` are Apache-2.0 code (see `CODE_LICENSE`).
`audio-worklet.js` in the Chrome extension is from the same project and is
also Apache-2.0. The compiled native library includes code from llama.cpp;
see `NATIVE_NOTICE.md` and `LLAMA_CPP_LICENSE`.

The Confucius4-R2T2 GGUF model and projector have a separate NetEase Youdao
Model Use License Agreement, included as `models/Confucius4-R2T2-GGUF/MODEL_LICENSE`
and `MODEL_LICENSE_zh`. The FireRedVAD files have their own Apache license
in `models/FireRedVAD-ONNX/LICENSE`.

Version 0.1.21 includes precompiled multi-architecture CUDA and separate CPU
libraries, plus private Microsoft VC++ runtime DLLs. No user-side C++ compiler
or system CUDA Toolkit is required. The CPU library has no CUDA dependency;
GPU architecture/driver checks choose it before native loading when necessary.
CPU recognition is slower. Supported CUDA drivers must report at least 12.8;
the native CPU baseline is x64 SSE4.2. The bundled manifest records exact
architectures, binary hashes, source revision and runtime provenance.

The complete offline bundle has these libraries preinstalled, with local
eight-MiB recovery parts. The small code update contains their fixed SHA256
manifest. On first speech load after an older bundle's code update, it downloads
the separate approximately 149 MiB `IndexTranslate-Speech-native-v0.1.21.zip`
Release asset and installs it atomically under `.runtime/build-native-cu128`.
This first download needs internet access; subsequent loads and the complete
offline bundle do not. No compiler is used. Failed downloads or validation keep
the previous libraries. Close the launcher before updating.

Transient local resets (including WinError 10054) are retried once for safe
requests without recreating an active worker. A committed audio packet is
not recognized twice; its subtitle events are replayed from the last ack.
Worker exit, memory failure, DLL failure and incompatible CUDA are reported
separately. Session creation with an uncertain response is not replayed.

CUDA speech was physically tested on Windows 11 / RTX 5090 Laptop, and the
CPU build was tested with actual speech without loading a CUDA DLL. Other GPU
architectures are included and inspected, but not individually hardware-tested.
The Chrome extension translates finalized speech segments and shows provisional
recognition while a segment is active.

Author publishing: build the CPU and CUDA libraries from the pinned source,
then run `tools/build_speech_payload.py` with `--cuda-build`, `--cpu-build` and
`--crt-dir`. It writes the offline recovery parts, fixed manifest, bootstrap
CRT files and `.build/native-release-assets/IndexTranslate-Speech-native-vVERSION.zip`.
Upload this native ZIP to a draft Release for the version before pushing its
tag. CI verifies the asset and all binary hashes, uploads the three small code
ZIPs and the combined SHA256SUMS, then publishes that draft. Native parts are
kept out of Git and the code update to preserve the old updater's limits.
