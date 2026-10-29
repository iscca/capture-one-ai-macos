# Mac 执行架构

```text
视觉 AI → MCP / CLI → 本机私有 Unix socket → 单一串行 Core
       → MacOSBackend / MacTools → Apple Events / JXA → Capture One
       → 参数回读 + 已授权的临时 JPEG 实际预览
```

六工具共用 Core，模型/客户端不另建执行器。owner 在客户端断开后继续持有已接受请求，使用当前用户的私有目录和 socket；不暴露公网服务。

Mac 实现在 macos_ipc.py、macos_backend.py、macos_tools.py 及 native/macos.js、macos_tools.js。宿主字段、命令与单位依已安装脚本字典核对。参数与结构变化均绑定实例、文档、照片、变体、活动层和前态；只允许登记候选写入。

每次写入使用唯一 request_id 和持久收据。超时与未知结果查询原编号，不重放；多个 setter 不承诺事务回滚。工具层使用唯一名称标记和清单保护身份；实测不可靠的动作在派发前拒绝。

临时预览导出需要用户授权，检查队列、文件、目标与已暴露的修订状态。没有完整蒙版修订计数、直接蒙版缓冲或独立 ICC 验收。蒙版命令收据与视觉结果分开描述。

私人状态位于 ~/Library/Application Support/CaptureOneAI/，不进入源码或分发。实际功能与证据边界见 [MACOS](MACOS.md) 和 [MAC_VALIDATION](MAC_VALIDATION.md)。
