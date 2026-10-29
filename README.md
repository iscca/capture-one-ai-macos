# Capture One AI · macOS Experimental

本仓库派生自 [ggssghatsys-beep/capture-one-ai-windows](https://github.com/ggssghatsys-beep/capture-one-ai-windows)，保留上游 MIT 版权及 Windows 后端。Mac 和 skill 下载见 [本仓库预发布](https://github.com/iscca/capture-one-ai-macos/releases)。

**0.3.0a1 新增 macOS 实验实现与可安装 skill**：已在 Capture One 16.8.6.25 验证基础调色、曲线/色阶、Basic/Advanced、色轮、颗粒、受限图层/蒙版和显式临时导出预览。功能尚未与 Windows 对齐，范围和安装见 [macOS 实验版](docs/MACOS.md)。下文原 Windows 支持声明保留。

让视觉 AI 在 Windows 上读取和调整 Capture One，并取得 C1 实际渲染的预览。提供 **MCP stdio + JSON CLI**，不绑定 AI 厂商、账号或聊天客户端。

**Windows 基线：0.2.0a1，实测 Capture One 16.8.6.3784 / Windows / Session。** 使用 C1 内部托管对象和原生处理内核，不是官方公开稳定 SDK。其他 C1 版本、Catalog 和多照片批量任务尚未完成兼容验证；启动器拒绝未验证版本。

Native Capture One editing for Windows through MCP and a JSON CLI. Vision models plan edits; the local bridge executes them and returns real Capture One previews. Unofficial, version-specific, MIT-licensed.

## 平台与 skill

| 交付内容 | 范围 |
|---|---|
| Windows | 保留既有后端及已验证版本；本次未做 Windows 实机回归 |
| macOS 实验版 | Apple Silicon / C1 16.8.6.25 / Session；[安装与限制](docs/MACOS.md) |
| capture-one-edit skill | 视觉调色流程，依赖单独安装的本机 MCP/CLI；[安装说明](docs/SKILL.md) |

本次提供分开的 Mac 源码包和 skill 包，见 [发布说明](docs/RELEASE_0.3.0a1.md)。skill 不内置模型密钥，也不携带照片或私人连接配置。

## Windows 功能

- 曝光、对比度、亮度、饱和度、白平衡、HDR。
- RGB / R / G / B 色阶；RGB / R / G / B / Luma **五通道原生曲线**。
- Basic、Advanced、Skin 混色器；主色轮和阴影／中间调／高光色彩平衡。
- 调整图层创建、选择、透明度、改名、开关、复制和排序。
- 主体／背景 AI 蒙版；反相、复制、组合、羽化、渐变和亮度范围。
- 模块重置与快照复用；当前集合内按 UUID 选图和翻页。
- C1 原生照片／灰度蒙版预览，长边 128～1600 像素；预览与 RAW 坐标映射。

全局和局部调整共用同一接口。正常流程不导入预设、不截图识色、不发送键鼠输入，不要求展开面板。清晰度、锐化、降噪、镜头／几何及人像精修不在当前范围；默认不切文档、不导出。

## 安装与启动

需要 64 位 Windows、有效授权的 C1 **16.8.6.3784**、64 位 Python 3.11+、PowerShell **7.6+** 与 .NET 10 Windows Desktop Runtime。本机验收 Python 3.14.7 / PowerShell 7.6.5，其他 Python 版本未逐项实测。安装依赖需要联网，运行库和 C1 均不随包分发。

下载 [Releases](https://github.com/ggssghatsys-beep/capture-one-ai-windows/releases) 的 Windows ZIP，解压到固定目录。在该目录的 PowerShell 7 中执行：

```powershell
.\Install.ps1 -SessionPath 'D:\Photos\MySession\MySession.cosessiondb'
.\Start-CaptureOne.ps1
.\Run.ps1 context --compact
```

在 C1 打开配置的 Session，选中照片。首次加载桥接时应先正常退出 C1，再用本项目启动器启动；仅打开普通 C1 快捷方式不会加载桥接。启动器默认不会关闭已运行的 C1；确需正常重启时显式传 `-Restart`。见 [安装说明](docs/INSTALL.md)。

安装器生成 `config/mcp-server.local.json`，把它加入支持本地 MCP 的 AI 客户端。核心字段是本机 Python 路径和 `-m c1ai mcp`。刷新 MCP 连接即可取得六个工具定义；客户端须支持图片块并使用视觉模型。见 [AI 接入](docs/AI_CLIENTS.md)。

## 一条编辑通道

```text
视觉 AI → MCP / CLI → 串行执行器与操作记录 → C1 内部命名管道
       → C1 原生参数／图层／蒙版对象 → 参数回读 + C1 原生预览
```

1. context 确认环境；read(fields=[]) 获取当前照片；preview(kind=native) 实际看图。
2. images(create_candidate) 创建并登记变体副本，保留原版本。
3. 按画面选择模块：read → adjust → preview，使用读取返回的目标和观察凭据。
4. 全局完成后再决定是否加局部蒙版；达到目标即停止。

参数使用绝对值，同模块相关参数合并提交。每次写入有唯一编号；超时先查询原编号，不重复派发。只有登记候选副本可写，参数回读不等于视觉效果合格。

## 包内文档

继续开发可直接克隆本仓库最新源码，先读 [开发 AI 入口](AGENTS.md)。计划开发 Mac 版时阅读 [macOS 开发交接](docs/MAC_PORT.md)；正式支持基线仍为上述 Windows 环境，Mac 为独立标注范围的实验实现。

| 文档 | 内容 |
|---|---|
| [INSTALL](docs/INSTALL.md) | 安装、启动、配置、升级和排错 |
| [AI_CLIENTS](docs/AI_CLIENTS.md) | 通用 MCP / CLI 接入与 AI 调色流程 |
| [API](docs/API.md) | 六个工具、请求示例、单位和返回值 |
| [COLOR_API](docs/COLOR_API.md) | 混色器、色阶、图层、蒙版和模块复用 |
| [NAVIGATION_PREVIEW](docs/NAVIGATION_PREVIEW.md) | 选图、原生预览和坐标映射 |
| [SUPPORT](docs/SUPPORT.md) | 已验证范围与未完成事项 |
| [ARCHITECTURE](docs/ARCHITECTURE.md) | 原生调用与原图保护 |
| [DEVELOPMENT](docs/DEVELOPMENT.md) | 开发和发布包生成 |
| [AGENTS](AGENTS.md) | 开发 AI 接手顺序、原生优先和原图保护原则 |
| [SKILL](docs/SKILL.md) | skill 安装、依赖与使用 |
| [MACOS](docs/MACOS.md) | Mac 安装、可用功能与限制 |
| [MAC_VALIDATION](docs/MAC_VALIDATION.md) | Mac 实机验证范围与失败项 |
| [MAC_PORT](docs/MAC_PORT.md) | Mac 迁移入口、可复用代码、已知缺口与验收顺序 |

运行状态在当前用户 `%LOCALAPPDATA%\CaptureOneAI`，不进入仓库。分发包不含照片、预览、Session、私人配置、连接令牌或历史操作数据库。预览只保留最近 8 张缓存，不是永久成片导出。

[MIT License](LICENSE) · [Third-party notices](THIRD_PARTY_NOTICES.md)
