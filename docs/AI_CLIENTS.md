# Mac AI 客户端接入

本项目不绑定 AI 厂商或账号。支持本机 MCP stdio 的视觉客户端可以连接；能运行本机命令的 Agent 可以使用 JSON CLI。没有公网服务，不保存模型提供商密钥。

先完成 [Mac 安装](INSTALL.md)，再使用安装器生成的 `config/mcp-server.local.json`。配置示意见 [模板](../config/mcp-server.example.json)，具体路径以本机安装结果为准。客户端需支持图片块；仅有文本的客户端不能声称检查了画面。

## 调色流程

1. `context` 确认宿主、目标、能力和未决操作；`read(fields=[])` 取得目标与观察凭据。
2. 经用户明确授权临时 JPEG 导出后，用 `preview(kind=render)` 看实际 C1 渲染。
3. 新方案创建一次 `images(action=create_candidate)` 并核对选中的候选；继续方案复用既有候选。
4. 按画面需要选择模块：read → adjust → 回读 → 实际预览。参数为绝对目标，曲线替换完整通道。
5. 全局完成后，仅为明确剩余问题加局部。蒙版无法直接读取灰度像素，须检查渲染效果与边缘。
6. 超时或 `outcome_unknown` 查询原 `request_id`，不换编号重放。切图、换层或前态变化后重新读取。
7. 达到用户目标后停止，保留原版本，选中候选，展示真实预览并说明限制。

不自动切文档、批量修改、正式导出、导入预设或发送键鼠。完整功能范围以 `context.capabilities` 和 [MACOS](MACOS.md) 为准。

## CLI 与 Python

```sh
./Run.command context --compact
./Run.command read --json '{"module":"exposure"}' --compact
./Run.command request --file request.json
```

Python 使用 `from c1ai.client import call`。MCP 与 CLI 共用同一个本机串行执行器；多个客户端不要同时规划并编辑同一照片。[接口说明](API.md)
