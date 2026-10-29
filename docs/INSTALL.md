# Mac 安装与启动

适用 macOS、Apple Silicon、Capture One 16.8.6.25 和用户打开的 Session。需要 Python 3.11+ 与有效 C1 授权；其他组合未验证。

1. 从本仓库 Releases 下载 Source code，解压到固定目录。安装依赖需要联网。
2. 在终端进入目录，运行 `./Install.command`。若 Python 不满足版本要求，用 `C1AI_PYTHON=/path/to/python3 ./Install.command` 指定解释器。
3. 在 Capture One 中打开一个 Session 并选中一张照片。按系统提示正常授予自动化权限。
4. 在明确选择该 Session 后开启实验写入：

```sh
.venv/bin/python scripts/configure_macos.py \
  --enable-current-session --enable-experimental-modules
```

临时预览使用低分辨率 JPEG 导出，只有用户明确允许后再运行配置并添加 `--allow-preview-export`。配置持久保存在本机，不需要每张照片重复启用。更换 Session 或 C1 版本需重新核对配置。

## 接入 AI

安装生成 `config/mcp-server.local.json`。将其中的 MCP 配置加入支持本地 stdio 和图片块的视觉客户端。命令为本目录 `.venv/bin/python`，参数 `-m c1ai mcp`。刷新 MCP 连接即可发现六个工具，不需为刷新工具重启 C1。

```sh
./Run.command context --compact
./Run.command read --json '{"module":"exposure"}' --compact
```

配套 skill 安装见 [SKILL](SKILL.md)。原生模块范围、预览及蒙版限制见 [MACOS](MACOS.md)。

## 更新与排错

保留本机状态目录与未决操作记录。更新前完成或只读核对在途请求，断开客户端后再更新代码；不要结束其他 Python 程序。不要为了清除错误而删除候选登记、操作数据库或重发未知请求。只有登记的候选变体可写。

没有目标时，先在 C1 选择照片；Catalog 不能当成已启用的 Session。无法预览时检查是否明确授权并配置了临时导出。宿主版本不匹配时核对字典与实机结果，不改资格数据绕过检查。

卸载程序目录不会自动清除 `~/Library/Application Support/CaptureOneAI/`。该目录包含私人配置、候选、操作记录及预览，不上传到公开仓库。
