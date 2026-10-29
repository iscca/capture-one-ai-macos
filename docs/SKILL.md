# capture-one-edit skill

这个 skill 为视觉 AI 提供 Capture One 调色流程，包括参考色调、胶片与电影风格、必要的局部调整和实际预览复核。执行依赖本项目 MCP/CLI，skill 本身不是应用程序或 Capture One 插件。

## 安装

1. 安装本项目：[Mac 版](INSTALL.md)。将安装器生成的本机 MCP 配置接入支持视觉与图片块的客户端。
2. 将仓库 `skills/capture-one-edit` 文件夹，或独立 skill ZIP 中同名文件夹，复制到客户端的技能目录。Codex 通常为 `~/.codex/skills/capture-one-edit`；使用其他客户端时遵循其 skill 目录约定。无需复制整个仓库到技能目录。
3. 刷新客户端技能与 MCP 连接。使用 `$capture-one-edit`，例如：“为当前选中的照片制作一个电影胶片候选版本，先说明方向，再调整并查看预览。”

安装 skill 不会自动授权修改照片、启用临时导出、重启应用或公开照片。Mac 临时预览导出需用户明确选择；已经明确授权的会话无需逐次重复确认。MCP 未连接时也可以使用已安装的 CLI。

## 包含内容

- `SKILL.md`：操作与审美判断流程、候选保护、停止和交付条件。
- `references/usage.md`：独立安装所需链接、Mac 使用约定、六工具调用与模块约定。
- `agents/openai.yaml`：技能名称和示例提示。
- 独立 ZIP 附 MIT 许可证。

不包含预设、LUT、照片、私人风格配方或示例操作收据。胶片风格由 AI 依据照片解释并调用原生调色模块，不宣称模拟了特定胶片的实测光谱响应。
