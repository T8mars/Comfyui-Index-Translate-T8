# Native backend provenance

`native_ext.cpp` is adapted from
[`netease-youdao/Confucius4-R2T2/r2t2_llama/native_ext.cpp`](https://github.com/netease-youdao/Confucius4-R2T2/blob/26d55a54ce5670cff9947a167d8ed95d569fd4d9/r2t2_llama/native_ext.cpp)
at commit `26d55a54ce5670cff9947a167d8ed95d569fd4d9`. The original project
licenses its code under Apache License 2.0; the corresponding license text is
included in this directory.

This copy adds Windows-compatible pybind sizing, whole-sequence UTF-8
detokenization, visible EOG filtering, and log filtering to avoid writing
prompts and transcripts to the worker log. `CMakeLists.txt` integrates the
adapted file with the pinned llama.cpp checkout.

The build script obtains llama.cpp at commit
`ad6c66839af3c5646fba8c6c2e2087a1e4e38948`. llama.cpp is MIT-licensed;
its source is not committed to this repository. Anyone distributing compiled
native binaries must also comply with that project's license terms.

The 0.1.21 prebuilt payload contains coherent CUDA 12.8 and CPU-only builds
of this pinned source. SHA-256 values and build settings are recorded in
`native-binaries.json`; files are installed from `native-binaries.*.bin`.
The CPU build disables CUDA, native-machine tuning, AVX, AVX2 and BMI2.
Both builds retain the SSE4.2 baseline. No local user compilation is needed.

The payload also includes unmodified, Microsoft-signed Visual C++ runtime
DLLs, copyright Microsoft Corporation, from the official x64 redistributable
14.51.36247.0 (`https://aka.ms/vc14/vc_redist.x64.exe`). They retain their
Microsoft licensing; they are not licensed under this project's Apache or
llama.cpp's MIT license. See Microsoft's Visual C++ redistributable terms:
https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files

The GGUF model and projector weights are downloaded separately into the
Git-ignored `models/` directory. They are governed by the upstream
[NetEase Youdao Model Use License Agreement](https://github.com/netease-youdao/Confucius4-R2T2/blob/26d55a54ce5670cff9947a167d8ed95d569fd4d9/MODEL_LICENSE),
not by this directory's Apache license.
