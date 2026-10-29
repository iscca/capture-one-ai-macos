# Capture One AI for Mac

让视觉 AI 在 Mac 上读取和调整 Capture One，通过本机 **MCP stdio / JSON CLI** 完成调色，并查看 Capture One 实际渲染的画面。

**macOS 实验版 · Capture One 16.8.6.25 · Apple Silicon · Session**。当前版本为 0.3.0a2，依赖原生 Apple Events / JXA；其他宿主版本、Intel Mac、Catalog 和批量任务尚未完成兼容验证。项目非 Capture One 官方产品。

[下载预发布](https://github.com/iscca/capture-one-ai-macos/releases) · [安装说明](docs/INSTALL.md) · [Skill 使用](docs/SKILL.md)

## 可以调整什么

- 曝光、对比度、亮度、饱和度、白平衡、HDR。
- RGB / R / G / B / Luma 五通道曲线，RGB / R / G / B 四通道色阶。
- Basic / Advanced 混色器，主色轮及阴影／中间调／高光色轮。
- 六类胶片颗粒、强度与大小，仅用于背景层。
- 候选图层创建、选择、透明度、开关、工具层改名与移除。
- AI 主体／背景蒙版、复制、反相、清除、受限填充、原生羽化与亮度范围。
- 当前集合列表、分页、选图，模块重置及快照复用。

适合参考调色、胶片观感和电影色调等工作。AI 依据照片决定参数，先创建候选副本，再读写、回读并检查预览。原图和原版本保留。

开发中的 [P0 摄影师任务流程](docs/EDITING_TASKS.md) 增加目标与颜色保护项、参考图角色分析、
实际预览版本留存、比较、反馈和已认可版本。参考判断由视觉 AI 完成；恢复范围为快照所在层的调色参数，
不包含完整图层／蒙版历史。现有发布包不会自动获得这些开发改动。

[P1-A / P1-B](docs/P1_EDITING.md) 进一步跟踪参考特征、定向反馈和局部认可，
每轮检查目标改善与认可项退化。由视觉 AI 判断，参数执行器不提供精确区域锁定或自动相似度。

当前优先完善 [灰度偏色与固定细节颜色映射](docs/REFERENCE_MATCHING.md)，P2 批量暂缓。
分析基于实际图片的配置文件和成对细节，保留参考图中的有意偏色；测量值不直接等于 C1 调整参数。

## 安装

需要 Python 3.11+、已安装并有效授权的 Capture One。在 Mac 上解压源码到固定目录，打开终端执行：

```sh
./Install.command
./Run.command context --compact
```

如果系统 `python3` 版本不足，使用 `C1AI_PYTHON=/path/to/python3 ./Install.command` 指定已安装的 Python。授权 macOS 正常弹出的自动化权限。

在 Capture One 打开一个 Session 并选中照片。确认开启该 Session 的实验调整后运行：

```sh
.venv/bin/python scripts/configure_macos.py \
  --enable-current-session --enable-experimental-modules
```

实际预览需要用户明确允许临时低分辨率 JPEG 导出，再加 `--allow-preview-export`。预览是 C1 当前调整后的实际输出，最长边 1600px；不是相机内嵌缩略图。默认不导出正式成片。

安装器生成本机 `config/mcp-server.local.json`。将它接入支持本地 MCP 和图片块的视觉 AI 客户端，六个工具为 `context`、`images`、`read`、`adjust`、`preview`、`operations`。无需为不同模型另建服务。

## 配套 skill

将 `skills/capture-one-edit` 复制到客户端的技能目录；Codex 通常使用 `~/.codex/skills/capture-one-edit`。刷新后使用：

> 使用 $capture-one-edit，为当前选中照片制作一个电影胶片候选版本，先说明方向，再调整并查看实际预览。

skill 提供调色流程，执行依赖已安装的本机 MCP/CLI。安装 skill 不会自动开启照片写入或临时导出。[详细说明](docs/SKILL.md)

## 当前限制

Skin 逐项参数、图层排序／复制、渐变／组合／子蒙版选择、直接蒙版像素、预览与 RAW 坐标映射和无导出渲染暂不可用。AI 蒙版的实际覆盖需要看图判断；AI 层填充可能保留生成蒙版约束。参数成功回读不等于视觉效果合格。

[验证范围](docs/SUPPORT.md) · [实机报告](docs/MAC_VALIDATION.md) · [接口](docs/API.md) · [开发说明](docs/DEVELOPMENT.md)

照片、预览、Session、配置、候选资格、令牌和操作记录保留在本机，不进入公开分发包。运行状态位于 `~/Library/Application Support/CaptureOneAI/`。

[MIT License](LICENSE) · [来源与第三方声明](THIRD_PARTY_NOTICES.md)
