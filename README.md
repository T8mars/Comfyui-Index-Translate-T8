# Index Translate · T8star-Aix

**中文** · [English](README_EN.md)

基于 [IndexTeam 的 Index-Translate](https://github.com/bilibili/Index-Translate) 的本地翻译项目。此仓库包含三个交付物：独立 ComfyUI 节点、Windows 本地整合包的代码更新、Chrome 网页自动翻译扩展。**ComfyUI 节点直接在 ComfyUI 环境推理，不连接 Windows 整合包**；Chrome 扩展连接本机整合包服务。

## 安装 ComfyUI 节点

在 ComfyUI Manager 中搜索 **Index Translate · T8star-Aix** 安装；也可克隆本仓库到 `ComfyUI/custom_nodes/Comfyui-Index-Translate-T8`，用 **ComfyUI 自己的 Python** 安装 `requirements.txt` 后重启。节点使用现有 PyTorch，不会随包安装模型或 Python。Transformers 5.17.0 可能影响宿主其他节点，升级前请检查兼容性。

从[模型仓库](https://huggingface.co/t8star/Index-Translate-Comfy/tree/main)下载完整的 `Index-Translate-2B` 或 `Index-Translate-9B` 文件夹，原样放进 `ComfyUI/models/index_translate/` 即可，无需改名。也可在节点的 `model_path` 填写完整目录，或运行 `python download_model.py --directory <目标目录> --model 2B` 从官方源逐文件下载、校验。建议先用 2B；9B 需要更多显存。35B preview 缺少完整分片，暂不支持。

在节点菜单搜索「Index Translate 本地翻译」，文本输入可接工作流其他节点，`translation` 可接文本预览。示例见 [examples/text-translation.json](examples/text-translation.json)。低显存 NVIDIA GPU 可另装 `requirements-nf4.txt` 并选 NF4；CPU 选 FP32。

通过 ComfyUI Manager/Registry 安装的节点可在 Manager 中检查和安装后续版本。GitHub [Releases](https://github.com/T8mars/Comfyui-Index-Translate-T8/releases) 另提供轻量代码 ZIP，**不含 Python、依赖运行时或模型权重**。

## Windows 与 Chrome

[Windows 代码与说明](https://github.com/T8mars/Comfyui-Index-Translate-T8/tree/main/standalone/windows)在 `standalone/windows`。完整离线整合包由作者另外分享；GitHub Release 的 Windows ZIP 仅用于给已有整合包更新代码，保留用户的 `runtime/`、`models/`、`data/`。Chrome 扩展 ZIP 解压后，在 `chrome://extensions` 开启开发者模式，选择**直接包含 `manifest.json` 的文件夹**并配对本地服务；不能选择 ZIP 文件。详情见 [Chrome 说明](https://github.com/T8mars/Comfyui-Index-Translate-T8/tree/main/standalone/chrome)。

0.1.13 的 Chrome 扩展增加 HTML5 视频翻译：优先翻译标准字幕轨；无标准字幕时，点击扩展后采集当前标签页声音，交给 Windows 整合包内独立的 R2T2 流式语音 worker 识别，再用 Index-Translate 翻译。语音模型、原生库和新 Python 依赖只在新完整离线包中提供，**旧包不能通过 GitHub 的代码 ZIP 单独升级出语音能力**。ComfyUI 节点与 Windows 整合包仍各自独立推理。

## 项目与作者

By **T8star-Aix**。这是社区集成，模型及原项目版权属于 IndexTeam / bilibili；详见 [NOTICE](NOTICE) 与 [LICENSE](LICENSE)。模型文件在 Hugging Face 保留官方文件和模型卡。

[B站](https://space.bilibili.com/385085361) · [YouTube](https://www.youtube.com/@T8star-Aix/) · [API](https://api.seedance.nz/sign-up?aff=5f4w) · [免费画廊](https://www.openzhenzhen.com) · [在线 AI 应用](https://www.runninghub.ai/zh-cn/user-center/1907375370302308353/userPost?inviteCode=rh-v1121) · [ComfyUI 整合包](https://pan.quark.cn/s/264edb7e36bd) · [Hugging Face](https://huggingface.co/t8star)
