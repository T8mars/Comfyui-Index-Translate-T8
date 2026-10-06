# Index Translate · T8star-Aix

**中文** · [English](README_EN.md)

基于 [IndexTeam 的 Index-Translate](https://github.com/bilibili/Index-Translate) 的本地翻译项目。此仓库包含三个交付物：独立 ComfyUI 节点、Windows 本地整合包的代码更新、Chrome 网页自动翻译扩展。**ComfyUI 节点直接在 ComfyUI 环境推理，不连接 Windows 整合包**；Chrome 扩展连接本机整合包服务。

## 0.1.22 中文 SRT → 英文 SRT

[下载 v0.1.22](https://github.com/T8mars/Comfyui-Index-Translate-T8/releases/tag/v0.1.22)。ComfyUI 新增 **Index Translate SRT 字幕翻译** 节点，Windows 新增 **字幕翻译** 页：导入中文 SRT，默认 `zh` → `en`，仅翻译正文，保留原序号及时间轴。支持 UTF-8 / UTF-16 BOM / GB18030，最多 1 MiB / 5000 条；可选双语，失败、截断或取消不会导出不完整文件。节点保存到 ComfyUI `output/IndexTranslate/`，整合包完成后下载 SRT。

此功能处理已有字幕，不需要语音模型或额外依赖；纯文本/音视频需要先取得带时间轴的字幕。节点在自身 Python 中推理，一个 SRT 只加载一次模型，结束释放。旧加速整合包关闭服务后运行 `update.cmd`，模型、路径、配对保留；匹配的 v0.1.21 语音组件继续复用。详见 [字幕节点](README.zh-CN.md)、[Windows 用法](standalone/windows/README.zh-CN.md) 和 [发行说明](docs/releases/v0.1.22.md)。

## 0.1.21 更新与下载

[下载 v0.1.21 修复版](https://github.com/T8mars/Comfyui-Index-Translate-T8/releases/tag/v0.1.21)：修复 `hidden_states` / `x=` 内核调用、旧 Qwen 缓存与 CPU 回退、包内编译工具定位，以及外部 Python 包混入导致的 FastAPI / Transformers 报错。完整 Windows 包自带所需工具，首次预热自动生成 GPU 缓存，无需另装 Mamba、Visual Studio 或系统 CUDA Toolkit。可选加速失败会验证普通推理回退；当前 GPU 的精度和显存检查也已修正。详见 [修复及实测范围](docs/releases/v0.1.21.md)。已有 0.1.17–0.1.20 加速包关闭服务后运行 `update.cmd`，模型及原配置保留。

语音翻译的 WinError 10054 也在此版修复：短暂断线安全重试、恢复最后一次已提交字幕；原生进程崩溃会报告原因。完整离线包预装多架构 CUDA、独立 CPU 和标准 VC++ 库。已有加速包代码更新后，首次加载语音模型会自动联网获取并校验约 149 MiB 的独立原生资源，无需用户编译；后续不再下载。驱动或架构不符合要求时在推理前选择 CPU，速度较慢。完整 Python 和模型权重仍另行提供。

### 0.1.20 的界面与启动器功能

[下载 v0.1.20 Release](https://github.com/T8mars/Comfyui-Index-Translate-T8/releases/tag/v0.1.20)。此次包含此前 0.1.18 / 0.1.19 的 BUG 修复，以及新启动器、预热状态和声音翻译反馈。

- **Windows**：已有 0.1.17 加速整合包先关闭服务，再运行 `update.cmd`；也可下载 `IndexTranslate-Windows-code-v0.1.20.zip`，直接覆盖解压到原整合包根目录，再运行 `start.cmd`。新增 **By T8star** 原生 EXE，启动后常驻任务栏，关窗口会取消任务、释放翻译/语音模型并等待自有服务退出。为兼容旧更新器，代码 ZIP 的 EXE 放在 `app/`，启动时自动放到根目录，用户无需改名或搬文件。GitHub 代码 ZIP 不含 Python 或模型，不能单独作为完整整合包使用；0.1.16 及更旧运行时仍需完整包升级。
- **预热**：按钮旁持续显示排队、加载/编译、完成或具体错误，刷新恢复任务，支持取消和重试。完成为绿色提示；eager 回退会明确说明。
- **Chrome**：`IndexTranslate-Chrome-v0.1.20.zip` 解压后直接包含 `manifest.json`。已有用户覆盖原扩展目录，在 `chrome://extensions` 重新加载并刷新网页，配对保留。点击「直接识别声音」立即反馈授权准备、模型加载、等待声音、采集进度、暂停或具体失败原因；进度不插入视频字幕。
- **节点**：继续使用自己的 Python 和模型路径，包含量化清单检查、升级后的缓存失效与下载续传修复。模型目录无需改名；BF16 与 ConvRot INT8 均可选择完整目录。权重未改变，无需重新下载。

验证：Python 111 项及 4 subtest，预热 UI 14 项，声音反馈 14 项，Chrome 生命周期 51 项；真实 2B INT8 和 R2T2 活动会话下关闭 EXE，CUDA allocated / reserved 均释放至 0。具体升级说明见 [Windows](standalone/windows/README.zh-CN.md)、[Chrome](standalone/chrome/README.zh-CN.md) 和 [Release notes](docs/releases/v0.1.20.md)。

## 安装 ComfyUI 节点

在 ComfyUI Manager 中搜索 **Index Translate · T8star-Aix** 安装；也可克隆本仓库到 `ComfyUI/custom_nodes/Comfyui-Index-Translate-T8`，用 **ComfyUI 自己的 Python** 安装 `requirements.txt` 后重启。节点使用现有 PyTorch，不会随包安装模型或 Python。Transformers 5.17.0 可能影响宿主其他节点，升级前请检查兼容性。

从[模型仓库](https://huggingface.co/t8star/Index-Translate-Comfy/tree/main)下载完整的 `Index-Translate-2B` 或 `Index-Translate-9B` 文件夹，原样放进 `ComfyUI/models/index_translate/` 即可，无需改名。也可在节点的 `model_path` 填写完整目录，或运行 `python download_model.py --directory <目标目录> --model 2B` 从官方源逐文件下载、校验。建议先用 2B；9B 需要更多显存。35B preview 缺少完整分片，暂不支持。

在节点菜单搜索「Index Translate 本地翻译」，文本输入可接工作流其他节点，`translation` 可接文本预览。示例见 [examples/text-translation.json](examples/text-translation.json)。低显存 NVIDIA GPU 可另装 `requirements-nf4.txt` 并选 NF4；CPU 选 FP32。

通过 ComfyUI Manager/Registry 安装的节点可在 Manager 中检查和安装后续版本。GitHub [Releases](https://github.com/T8mars/Comfyui-Index-Translate-T8/releases) 另提供轻量代码 ZIP，**不含 Python、依赖运行时或模型权重**。

## Windows 与 Chrome

[Windows 代码与说明](https://github.com/T8mars/Comfyui-Index-Translate-T8/tree/main/standalone/windows)在 `standalone/windows`。完整离线整合包由作者另外分享；GitHub Release 的 Windows ZIP 仅用于给已有整合包更新代码，保留用户的 `runtime/`、`models/`、`data/`。Chrome 扩展 ZIP 解压后，在 `chrome://extensions` 开启开发者模式，选择**直接包含 `manifest.json` 的文件夹**并配对本地服务；不能选择 ZIP 文件。详情见 [Chrome 说明](https://github.com/T8mars/Comfyui-Index-Translate-T8/tree/main/standalone/chrome)。

0.1.13 的 Chrome 扩展增加 HTML5 视频翻译：优先翻译标准字幕轨；无标准字幕时，点击扩展后采集当前标签页声音，交给 Windows 整合包内独立的 R2T2 流式语音 worker 识别，再用 Index-Translate 翻译。语音模型、原生库和新 Python 依赖只在新完整离线包中提供，**旧包不能通过 GitHub 的代码 ZIP 单独升级出语音能力**。ComfyUI 节点与 Windows 整合包仍各自独立推理。

0.1.14 可以在整合包「模型与设备 → 视频语音识别」中选择已有 R2T2 模型目录，直接读取两个官方 Q8 GGUF 文件，不复制模型。可选原 R2T2 项目根目录、models 或 GGUF 文件夹，目录无需改名；路径保存到 data/speech-settings.json，重启与代码更新后保留。已有语音模型可使用作者另行分享的 reuse-ASR 运行包，省去约 2.19 GB 语音权重。流式语音处理复用了 [你们已有的 R2T2 项目](https://github.com/T8mars/comfyui-confucius-r2t2-t8)；原项目和 ComfyUI 都不需要启动。

0.1.15 的 Chrome 悬浮按钮默认贴右，点击面板外自动收起；每个可见播放器内提供「字幕译」「语音译」「停止」按钮。Vimeo 优先使用官方播放器字幕接口，读不到字幕时回退至本地 R2T2。连续切换模式会先完成旧语音会话取消再启动，避免 BUSY 抢占。Chrome 首次采集某个标签页声音前，需先点工具栏扩展授权；视频内会明确提示，之后同一标签页可直接操作。配对和模型路径保持兼容 0.1.14，节点仍独立。

0.1.16 修复刷新后按钮消失：普通 HTTP/HTTPS 网页默认加载 36 像素蒂芙尼蓝「译」按钮，单击开始翻译；可见视频自动显示「字幕译」「语音译」「停止」，无需先点击工具栏加载页面脚本。按钮常驻与本站自动翻译规则分开，未点击且未开启自动规则时不提交正文或采集声音。升级已有解压扩展后重新加载，允许新增的网站访问权限并选择「在所有网站上」，再刷新网页；配对与偏好保留。视频字幕等待时只显示原文，不显示「翻译中」或「识别中」占位文字。声音采集仍遵守 Chrome 标签页授权规则；与已有 0.1.14 Windows 服务兼容，完整离线 Windows 包仍为 0.1.15。

0.1.17 新增完整 2B / 9B CONVROT INT8 模型和独立加载器，路径可选、文件夹无需改名。Windows 新完整包内置 2B INT8 与官方 wheel 加速依赖，旧包需要完整运行时升级；保存模型设置后先「加载并预热」。本机 2B 首次编译约 40–60 秒；9B 首次编译约 116 秒，加载另需约 20 秒。2B 热态 12 次字幕生成中位数 0.297 秒（旧 BF16 0.727 秒）；真实 Chrome 字幕显示 0.561 秒，刷新后缓存命中 0.090 秒。节点使用自己的 Python 安装 `install_convrot.py`，不连接整合包，执行后仍释放权重，不能将常驻服务速度当节点工作流速度。INT8 本版要求 NVIDIA CUDA；CPU 用官方目录。

视频新增当前字幕优先、正文逐段让出、未来字幕低优先级预取、及时长轮询、精确持久缓存与多消费方合并。语音按 320 ms 传输，只翻译确认文本，长文本完整拆分；会话结束用 revision 游标排除历史重放。本机约 40 秒英文样例在 1.5 倍速下最大积压 0.41 秒，没有持续增长；结果依赖本机负载和内容。升级扩展后重新加载一次并刷新网页，配对保留。源码 ZIP 不含 Python 或权重。详细模型路径与依赖见 [节点说明](README.zh-CN.md)、[Windows](standalone/windows/README.zh-CN.md) 和 [模型仓库](https://huggingface.co/t8star/Index-Translate-Comfy)。

## 项目与作者

By **T8star-Aix**。这是社区集成，模型及原项目版权属于 IndexTeam / bilibili；详见 [NOTICE](NOTICE) 与 [LICENSE](LICENSE)。模型文件在 Hugging Face 保留官方文件和模型卡。

[B站](https://space.bilibili.com/385085361) · [YouTube](https://www.youtube.com/@T8star-Aix/) · [API](https://api.seedance.nz/sign-up?aff=5f4w) · [免费画廊](https://www.openzhenzhen.com) · [在线 AI 应用](https://www.runninghub.ai/zh-cn/user-center/1907375370302308353/userPost?inviteCode=rh-v1121) · [ComfyUI 整合包](https://pan.quark.cn/s/264edb7e36bd) · [Hugging Face](https://huggingface.co/t8star)
