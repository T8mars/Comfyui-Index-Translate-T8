# ComfyUI Index Translate

将本目录复制到 `ComfyUI/custom_nodes/ComfyUI-Index-Translate`，用 **ComfyUI 自己的 Python** 安装 `requirements.txt`，然后重启 ComfyUI。依赖文件不安装或覆盖 torch；CUDA 运行需要支持当前显卡的 PyTorch。

节点直接加载本地 Index-Translate 模型。无需启动 Windows 整合包，没有 HTTP 推理请求，也不引用整合包的 Python。

0.1.6 更新独立节点随包携带的共享模型管理代码：已校验模型可跳过下载空间预检；校验收据写入失败不影响模型可用性；损坏或超长续传文件不会被误计为可复用空间。节点推理接口保持兼容，先前的加载失败清理、模型核验和取消修复继续保留。

推荐先使用官方 2B。将完整模型放到 `ComfyUI/models/index_translate/Index-Translate-2B`，或者在节点 `model_path` 填写完整模型目录。可用本节点自带 `download_model.py --directory <完整模型目录> --model 2B` 下载并逐文件校验。

搜索「Index Translate 本地翻译」，连接 `translation` 输出到「Preview as Text」或图像流程的文本输入。语言可填写 `auto/zh/en/ja` 等代码，也可填写语言名称；目标语言不能为 auto。`metadata` 返回设备、精度、token 数和耗时。

执行结束或中断后，节点释放模型。模型权重不会跨工作流保留。输出预算不足会明确报错，调整预算或缩短输入后重新执行。

`examples/text-translation.json` 是可拖入的工作流；默认使用相对模型目录。2B BF16 权重约 4.24 GiB，还需预留生成显存。CPU FP32 需要约 10.5 GiB 可用内存。9B 的资源需求更高，按实际空闲资源选择。

选择 NF4 前，在 **ComfyUI 自己的 Python** 中安装 `requirements-nf4.txt`。NF4 仅支持 NVIDIA GPU，可能改变译文质量；2B / 9B 的真实样例已测试。宿主 torch 2.7.0+cu128 的 2B NF4 工作流已验证，执行结束后仅余约 1 KiB 量化查表常量；节点不会跨执行持有模型。CPU 权重释放后操作系统可能仍保留进程堆工作集。

2B / 9B 完整模型包独立提供。解压到自己的模型目录即可，不需引用整合包目录。模型管理按固定 revision、SHA256 和文件大小核验，35B 当前缺失分片的快照不可加载。节点 vendored 核心代码包含在本目录中。

本工作区已按用户授权升级宿主的 Transformers 依赖，采用独立目录和 `.pth` 激活，未更换 torch。新进程使用升级依赖，现有运行进程不会自动切换。回退时移除 `python/Lib/site-packages/index-translate-upgrade.pth` 并重启宿主；具体版本与原有依赖冲突见工作区 verification/comfy-process-dependencies.json。其他 ComfyUI 环境应自行评估 Transformers 5.17 对已有节点的兼容性。
