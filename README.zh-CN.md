# ComfyUI Index Translate

0.1.21 修复 `hidden_states` / `x=` 卷积调用兼容错误。旧版 Qwen 缓存使用完整 PyTorch 回退，不注入新版融合解码或静态缓存；可选内核不可用时使用普通推理，不要求安装 mamba-ssm。节点仍使用 ComfyUI 自己的 Python、PyTorch 和模型目录，不连接 Windows 服务或借用整合包运行时。

CUDA `auto` 按当前 GPU 的能力与空闲显存选档位；无原生 BF16 的旧 GPU 默认官方模型 FP32，NF4 使用 FP16 计算。固定 BF16 / CONVROT INT8 档位要求 NVIDIA Ampere 或更新的 GPU，旧显卡会在加载前得到具体提示。硬件兼容门控已测；实际加速硬件验证为 RTX 5090 Laptop，其他 GPU 未逐一实测。

加载前检查宿主 PyTorch 实际支持的显卡架构。官方模型的 `auto` 在架构不支持时选择 CPU/FP32；手动 CUDA、NF4 或 INT8 会提前提示。CPU 回退先隔离可选 CUDA 内核导入，避免加速库在加载权重前探测不兼容显卡。

0.1.19 加强量化清单文件和层字段检查，损坏清单会得到明确错误；共享模型下载代码在网络提前结束时保留续传进度。模型路径、宿主 Python 和独立运行方式保持原有用法。

0.1.18 补齐工作流缓存校验：独立加载器、加速代码、模型管理与模型清单发生变化时，节点会重新执行，避免复用升级前的旧结果。节点仍在自身 Python 中推理，执行后释放模型。

0.1.17 支持真正的 CONVROT INT8。下载完整 `Index-Translate-2B-ConvRot-INT8` / `Index-Translate-9B-ConvRot-INT8` 目录，原样放入 `ComfyUI/models/index_translate/`；在节点 `model_path` 选择该目录或填写任意完整目录的绝对路径，精度选择 `auto` 或 `convrot-int8`。无需改名。配置、分词器、全部权重分片和 `index-quantization.json` 必须齐全。权重约 2.67 / 10.37 GB。

Windows 用 **ComfyUI 自己的 Python** 执行 `install_convrot.py`，安装固定官方 comfy-kitchen 0.2.37、NVIDIA cuBLAS 13.2.2.2 wheel 到独立目录，不替换宿主 torch。新进程生效；回退只需移除 `index_translate_convrot.pth` 并重启。其他系统用宿主 Python 安装 `requirements-convrot.txt`。INT8 本版需要 NVIDIA CUDA，CPU 仍用官方模型。真实 Python 3.10 / torch 2.7/cu128 宿主工作流已通过，整合包两个端口被阻断时仍能独立翻译。

节点仍在执行结束后释放模型。Windows 常驻服务的静态缓存编译速度不适用于该节点；此宿主 GDN 使用正确的参考路径，首次工作流含加载耗时。不得借用整合包 Python、依赖目录或 HTTP 服务。

将本目录复制到 `ComfyUI/custom_nodes/ComfyUI-Index-Translate`，用 **ComfyUI 自己的 Python** 安装 `requirements.txt`，然后重启 ComfyUI。依赖文件不安装或覆盖 torch；CUDA 运行需要支持当前显卡的 PyTorch。

节点直接加载本地 Index-Translate 模型。无需启动 Windows 整合包，没有 HTTP 推理请求，也不引用整合包的 Python。

0.1.6 更新独立节点随包携带的共享模型管理代码：已校验模型可跳过下载空间预检；校验收据写入失败不影响模型可用性；损坏或超长续传文件不会被误计为可复用空间。节点推理接口保持兼容，先前的加载失败清理、模型核验和取消修复继续保留。

推荐先使用官方 2B。模型仓库中的完整文件夹已命名为 `Index-Translate-2B` / `Index-Translate-9B`，下载后原样放入 `ComfyUI/models/index_translate/` 即可，无需改名；也可在节点 `model_path` 填写完整模型目录。可用本节点自带 `download_model.py --directory <完整模型目录> --model 2B` 下载并逐文件校验。

搜索「Index Translate 本地翻译」，连接 `translation` 输出到「Preview as Text」或图像流程的文本输入。语言可填写 `auto/zh/en/ja` 等代码，也可填写语言名称；目标语言不能为 auto。`metadata` 返回设备、精度、token 数和耗时。

执行结束或中断后，节点释放模型。模型权重不会跨工作流保留。输出预算不足会明确报错，调整预算或缩短输入后重新执行。

`examples/text-translation.json` 是可拖入的工作流；默认使用相对模型目录。2B BF16 权重约 4.24 GiB，还需预留生成显存。CPU FP32 需要约 10.5 GiB 可用内存。9B 的资源需求更高，按实际空闲资源选择。

选择 NF4 前，在 **ComfyUI 自己的 Python** 中安装 `requirements-nf4.txt`。NF4 仅支持 NVIDIA GPU，可能改变译文质量；2B / 9B 的真实样例已测试。宿主 torch 2.7.0+cu128 的 2B NF4 工作流已验证，执行结束后仅余约 1 KiB 量化查表常量；节点不会跨执行持有模型。CPU 权重释放后操作系统可能仍保留进程堆工作集。

2B / 9B 完整模型包独立提供。解压到自己的模型目录即可，不需引用整合包目录。模型管理按固定 revision、SHA256 和文件大小核验，35B 当前缺失分片的快照不可加载。节点 vendored 核心代码包含在本目录中。

本工作区已按用户授权升级宿主的 Transformers 依赖，采用独立目录和 `.pth` 激活，未更换 torch。新进程使用升级依赖，现有运行进程不会自动切换。回退时移除 `python/Lib/site-packages/index-translate-upgrade.pth` 并重启宿主；具体版本与原有依赖冲突见工作区 verification/comfy-process-dependencies.json。其他 ComfyUI 环境应自行评估 Transformers 5.17 对已有节点的兼容性。
