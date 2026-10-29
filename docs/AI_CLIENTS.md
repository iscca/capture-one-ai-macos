# AI 接入与调色流程

接口不绑定模型、账号或聊天。支持本机 MCP stdio 的客户端可以直接连接；能执行本机命令的 Agent 可以使用 JSON CLI。纯网页聊天需要自己的本地连接程序，本项目没有公网 HTTP 服务，也不保存云端模型密钥。

## MCP

按平台安装：Windows 使用 Install.ps1，Mac 使用 Install.command（见 [MACOS](MACOS.md)）。安装后使用 `config/mcp-server.local.json`。[示意配置](../config/mcp-server.example.json) 中的目录需要替换为安装目录。不同客户端外层配置格式可能不同，核心字段为 command、args、env。

程序已安装到本地 Python 环境，可从其他工作目录启动，不依赖 cwd。六个工具为 context、images、read、adjust、preview、operations。视觉模型须收到 preview 的图片块；纯文本模型不能声称检查过画面。当前以标准 MCP SDK 客户端验证，没有逐个验收所有 AI 产品。

若工具仍显示旧定义（如 preview 只有 viewer/render、images 没有 variant_uuid），刷新 MCP 连接。Windows 默认使用 kind=native；Mac 仅在用户授权临时导出后使用 kind=render。无需为刷新工具重启 C1 或降级为截图。

## 给 AI 的操作说明

先读 context.workflow 和 read.guidance，再按以下流程操作：

1. 确认当前 Session、照片和未决操作，实际查看原生预览；依据画面和用户意图提出简短方案，不默认每张照片都需要提亮、增饱和或肤色均匀化。
2. 若目标不是登记候选，先创建副本。用户要求从头构思时从其选定原始版本复制，不叠加旧版风格；已有明确候选则继续使用，不为每次改参数再建副本。
3. 建议按白平衡、曝光、HDR、色阶、曲线、混色器、色轮处理，跳过无必要模块。read 后将 target、observation_token 原样用于 adjust。数值为绝对目标，曲线 points 替换完整通道。
4. 同模块多个技术调用可共用一次预览复核。保持 kind 和尺寸一致，检查人物层次、色相和明暗过渡；变亮或更饱和不自动等于更符合目标。
5. 全局后仅为明确局部问题加图层／蒙版。先看 mask 覆盖，再看 native 效果。选层后重新读取模块，渐变坐标经 map_points 转到 RAW。
6. 超时／outcome_unknown 用原 request_id 查询 operations，禁止换编号重复。目标改变则重新读，不自动改用预设、UIA、截图识色或键鼠。
7. 达到目标后停止，说明实际结果和遗留问题，默认留在候选变体，不导出、不切文档。

这些原则也已内置到 MCP 初始化说明、context.workflow 和 read.guidance，无需另装专属技能。程序负责执行，审美由客户端视觉模型判断。

## CLI / Python

```powershell
.\Run.ps1 context --compact
.\Run.ps1 read --json '{"module":"exposure"}' --compact
.\Run.ps1 request --file request.json
```

完整 JSON 响应用于继续编辑；--compact 适合人查看，不保证保留所有后续调用字段。CLI 返回预览路径，Agent 需另行读取图片。Python 入口是 `from c1ai.client import call`，示例见 [API](API.md)。多个客户端共享一个 owner，但不应同时规划和编辑同一照片。
