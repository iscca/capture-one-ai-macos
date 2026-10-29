# Mac 接口

MCP 与 CLI 共用六工具。MCP 调用省略 `command` 字段；Python `c1ai.client.call` 和 CLI `request --file` 使用完整 JSON。

| 工具 | 用途 |
|---|---|
| context | 实例、Session、当前目标、能力与未决操作 |
| images | 当前集合、分页、选择、上/下一张、创建候选 |
| read | 读取标量或结构化模块，返回 target 与 observation_token |
| adjust | 在登记候选上设置绝对参数或执行受支持模块动作 |
| preview | 显式授权后的 C1 临时 JPEG 渲染，kind=render |
| operations | 用原 request_id 查询或只读核对结果 |

Mac P0 新增 `operations(action=task,task_action=...)` 管理摄影师目标、参考图分析、
真实预览检查点、比较、反馈和用户认可。`task_action=restore` 是显式参数写入，
沿用现有 adjust 保护；`get/list/reconcile` 仍为原操作记录入口。详见 [修图任务](EDITING_TASKS.md)。

P1 开发版本另含 `task_action=reference_plan|recognize`，feedback 支持 interpretation，
review 按检查点 intent 增加逐项结果。六工具不变，详见 [P1 契约](P1_EDITING.md)。

参考分析新增 `task_action=anchor_analysis`，读取实际预览的明度段、灰色偏色及固定细节颜色对应，
只写本机任务记录；不会把测量值直接作为 C1 参数。见 [固定细节映射](REFERENCE_MATCHING.md)。

## 目标与写入

所有修改绑定当前实例、文档、照片、变体及图层。先 read，将返回的 `target`、`observation_token` 原样传入 adjust，使用唯一 `request_id`。切图/层或前态变化后重新读取。未知或部分结果先查原编号，不重放。

```python
import uuid
from c1ai.client import call

before = call({'command': 'read', 'module': 'exposure'})
result = call({
    'command': 'adjust', 'module': 'exposure',
    'patch': {'exposure': -0.15},
    'target': before['target'],
    'observation_token': before['observation_token'],
    'request_id': uuid.uuid4().hex,
})
```

示例数值只展示格式，不是通用调色建议。仅在已核对登记候选和能力后写入。曝光是 EV，其余参数范围与单位以运行时能力/指导为准。

标量模块：`white_balance`、`exposure`、`hdr`。结构化模块：`curves`、`levels`、`color_mixer`、`color_balance`、`film_grain`、`layers`、`masks`。模块格式见 [COLOR_API](COLOR_API.md)。

## 预览与操作结果

预览前读取目标，调用 `preview(kind=render,max_size=128..1600,target=...,observation_token=...,request_id=...)`。必须已获用户临时导出授权并启用对应配置。MCP 返回实际 JPEG 图片块，CLI 返回本机路径。

`control_confirmed` 或参数回读只证明执行状态，不代表视觉效果通过。`partial` 或 `outcome_unknown` 保留原收据，使用 `operations(action=get,request_id=...)` 查询，必要时 `reconcile`。恢复参数应使用新观察与已保存的值或受支持的快照 paste；operations 不是通用撤销。

[能力与限制](MACOS.md) · [导航和预览](NAVIGATION_PREVIEW.md)
