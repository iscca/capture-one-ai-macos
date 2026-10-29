# macOS 实验版

已在 Capture One **16.8.6.25** 接入原生 Apple Events / JXA 调整。提供六个 MCP / CLI 工具；不写 Capture One 数据库或侧车文件，不使用界面辅助。

最新实机结果、限制和失败项目见 [MAC_VALIDATION](MAC_VALIDATION.md)。Mac 功能仍为实验性，版本资格和性能仅以 Mac 实测为准。

## 当前可用

- 曝光、对比度、亮度、饱和度、白平衡、HDR。
- 五通道曲线、四通道色阶、Basic/Advanced、四个色轮。
- 原生颗粒六类型、强度、大小，仅用于背景层。
- 全局与局部参数；候选图层的创建、选择、透明度、开关、工具层改名与移除。
- AI 主体/背景、复制、反相、清除、位图填充、原生羽化、亮度范围。蒙版存在宿主语义和回读限制，必须检查实际预览。
- 当前集合列表、分页、按原生 ID 选择、上一张/下一张；模块重置及已读快照粘贴，支持范围以能力表为准。
- C1 实际低分辨率 JPEG 预览与 MCP 图片块，使用明确启用的临时导出。

Skin、图层排序/复制、渐变/组合/子蒙版、直接蒙版像素、坐标映射及无导出渲染不可用。图层排序和复制已本机验证失败并停用。白平衡不提供猜测的相机默认值重置，可粘贴已有快照。

## 安装与启用

需要 Python 3.11+ 和有效 C1 授权。已有 `.venv` 时复用；系统自带 Python 3.9 不满足要求。

```sh
C1AI_PYTHON=/absolute/path/to/python3 ./Install.command
./Run.command context --compact
```

默认只读。用户明确选择 Session 后开启该文档的实验写入和预览导出：

```sh
.venv/bin/python scripts/configure_macos.py \
  --enable-current-session --enable-experimental-modules --allow-preview-export
```

配置写入当前用户的 `~/Library/Application Support/CaptureOneAI/`；不进入 Git。换 Session 或升级 C1 后需核查并重新配置。当前不允许 Catalog 写入。macOS 自动化权限按正常系统提示授予，不规避检查。

本地 MCP 配置生成在 `config/mcp-server.local.json`：命令是本项目 `.venv/bin/python`，参数 `-m c1ai mcp`。安装器不自动修改客户端全局配置。

## 颗粒调用

```python
import uuid
from c1ai.client import call

r = call({'command': 'read', 'module': 'film_grain'})
assert r['status'] == 'ok', r
result = call({
    'command': 'adjust', 'module': 'film_grain',
    'patch': {'type': 'fine', 'impact': 25, 'granularity': 50},
    'target': r['target'], 'observation_token': r['observation_token'],
    'request_id': uuid.uuid4().hex,
})
print(result['status'])
```

类型为 `fine / silver_rich / soft / cubic / tabular / harsh`；强度、大小 0..100，impact=0 关闭颗粒。支持 `reset` 和已读同模块快照的 `paste`。颗粒只允许背景层。

## 使用与保护

先 context/read，创建一次候选后编辑该副本。每次写入使用新 request_id，绑定当前实例、文档、照片、图层和读取前态。切图、换层或参数变化后重新读取。unknown/partial 先查原编号，不换编号重放。

Mac 预览使用 `kind=render`，长边 128..1600，是 C1 实际 JPEG 导出；不冒充无导出 native 渲染。只保留最近 8 个成功缓存，重要前态由客户端另存。Apple Events 不是同一 UI 线程事务；导出前后核对脚本暴露的曲线、色阶、混色器、色轮、标量、图层和亮度范围。没有完整蒙版修订计数、独立 ICC 验收或原尺寸颗粒验收。

蒙版写入没有像素回读时会返回 partial 并保留命令收据；通过 operations reconcile 只读核对已完成命令，之后仍要看实际图像。填充在 AI 生成层上可能保留生成/子蒙版约束；全画幅填充只在空白位图调整层验证。羽化用 `native_amount` 0..100，不能把它当成像素半径。

私人参数、预览、文档路径与操作记录留在本机。公开验证文档不包含原照片或私人收据。
