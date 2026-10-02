# Index Translate · T8star-Aix

[中文](README.md) · **English**

A local translation project built on [IndexTeam's Index-Translate](https://github.com/bilibili/Index-Translate). This repository contains three deliverables: an independent ComfyUI node, source updates for a standalone Windows bundle, and a Chrome web-page translation extension. **The ComfyUI node runs inference inside ComfyUI and does not connect to the Windows bundle.** The Chrome extension connects to the local Windows service.

## ComfyUI node

Search for **Index Translate · T8star-Aix** in ComfyUI Manager, or clone this repository into `ComfyUI/custom_nodes/Comfyui-Index-Translate-T8`, install `requirements.txt` with **ComfyUI's own Python**, and restart ComfyUI. The package uses the host's existing PyTorch and does not include Python or weights. Transformers 5.17.0 may affect other nodes; check host compatibility before upgrading.

Download the complete `Index-Translate-2B` or `Index-Translate-9B` directory from the [model repository](https://huggingface.co/t8star/Index-Translate-Comfy/tree/main), and place it as-is under `ComfyUI/models/index_translate/`. No rename is needed. You can also enter an absolute `model_path` in the node, or run `python download_model.py --directory <target> --model 2B` to fetch and verify files from the official source. Start with 2B; 9B needs substantially more VRAM. The incomplete 35B preview is unsupported.

Find “Index Translate 本地翻译” in the node menu and connect `translation` to a text preview or downstream node. An [example workflow](examples/text-translation.json) is included. On NVIDIA GPUs, install `requirements-nf4.txt` separately to use NF4. Use FP32 on CPU.

Nodes installed through ComfyUI Manager/Registry can be updated through Manager. GitHub [Releases](https://github.com/T8mars/Comfyui-Index-Translate-T8/releases) carry lightweight source ZIPs **without Python, a runtime, or model weights**.

## Windows and Chrome

The [Windows source and instructions](https://github.com/T8mars/Comfyui-Index-Translate-T8/tree/main/standalone/windows) are in `standalone/windows`. The author distributes the complete offline bundle separately. The Windows ZIP in GitHub Releases updates code in an existing bundle while preserving its `runtime/`, `models/`, and `data/`. For Chrome, extract the extension ZIP, enable Developer mode at `chrome://extensions`, select the **folder directly containing `manifest.json`**, and pair it with the local service. Do not select the ZIP. See the [Chrome guide](https://github.com/T8mars/Comfyui-Index-Translate-T8/tree/main/standalone/chrome).

Version 0.1.13 adds HTML5 video translation. The extension translates standard subtitle tracks; when none is available, a user click starts tab-audio capture, streams speech to a standalone R2T2 worker in the Windows bundle, and translates finalized speech segments. This requires the new complete offline bundle with its speech weights, native library, and Python dependencies. A code-only GitHub ZIP cannot add speech support to an older bundle. The ComfyUI node remains independent of the Windows bundle.

## Credits and links

By **T8star-Aix**. This is an independent community integration; the original project and model credits belong to IndexTeam / bilibili. See [NOTICE](NOTICE) and [LICENSE](LICENSE). The Hugging Face model mirror retains the upstream files and cards.

[Bilibili](https://space.bilibili.com/385085361) · [YouTube](https://www.youtube.com/@T8star-Aix/) · [API](https://api.seedance.nz/sign-up?aff=5f4w) · [Free gallery](https://www.openzhenzhen.com) · [Online AI apps](https://www.runninghub.ai/zh-cn/user-center/1907375370302308353/userPost?inviteCode=rh-v1121) · [ComfyUI bundle](https://pan.quark.cn/s/264edb7e36bd) · [Hugging Face](https://huggingface.co/t8star)
