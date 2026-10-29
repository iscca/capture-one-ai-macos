# 接口说明

本文中的原生预览、Skin、图层排序/复制及完整蒙版能力以 Windows 为基线。Mac 共用六工具，但必须读取当前 `context.capabilities`，其范围和差异见 [MACOS](MACOS.md)；不可由下面示例推定 Mac 支持。

MCP 与 CLI 共用六个命令。MCP 省略 JSON 的 command 字段；Python c1ai.client.call 和 CLI request --file 使用完整 JSON。只写登记候选，不能伪造目标、凭据和图层引用。

| 工具 | 用途和主要字段 |
|---|---|
| context | 环境、目标、能力、未决操作、workflow；include_dialogs 可显式查询弹窗 |
| images | action=list/create_candidate/select/next/previous；scope、offset、limit、collection_token；选图优先 variant_uuid |
| read | fields 或 module 或 modules，target 可传预期目标；返回 values/state、target、observation_token、evidence、guidance |
| adjust | module、patch、target、observation_token、request_id；绝对目标值 |
| preview | kind=native/mask、max_size；action=capture（默认）或 map_points；返回图片、preview_id 与几何 |
| operations | action=get/list/reconcile，按 request_id 或 operation_id 查询；reconcile 不重放写入 |

images 的 prepare_tool/prepare_adjustments、preview 的 viewer/render 是显式兼容入口，不用于正常原生流程。

## 最小闭环（会创建副本）

示例展示调用顺序，不是通用预设。必须先查看 before 图片，再决定曝光值。

```python
import uuid
from c1ai.client import call

def ok(r):
    if r.get('status') not in ('ok', 'completed', 'control_confirmed'):
        raise RuntimeError(r)  # 结果未知先查原编号，不能自动重发
    return r

print(ok(call({'command': 'context'})))
r = ok(call({'command': 'read', 'fields': []}))
before = ok(call({'command': 'preview', 'kind': 'native', 'max_size': 1200,
    'target': r['target'], 'observation_token': r['observation_token'],
    'request_id': uuid.uuid4().hex}))
print(before['result']['path'])  # 在客户端实际查看

clone = ok(call({'command': 'images', 'action': 'create_candidate',
    'target': r['target'], 'observation_token': r['observation_token'],
    'request_id': uuid.uuid4().hex}))
target = clone['after']['target']
r = ok(call({'command': 'read', 'module': 'exposure', 'target': target}))
request_id = uuid.uuid4().hex
edited = ok(call({'command': 'adjust', 'module': 'exposure',
    'patch': {'exposure': 0.2}, 'target': r['target'],
    'observation_token': r['observation_token'], 'request_id': request_id}))
r = ok(call({'command': 'read', 'fields': [], 'target': target}))
after = ok(call({'command': 'preview', 'kind': 'native', 'max_size': 1200,
    'target': r['target'], 'observation_token': r['observation_token'],
    'request_id': uuid.uuid4().hex}))
print(after['result']['path'])
```

结果未知时：`{"command":"operations","action":"get","request_id":"原编号"}`。变体沿用同一 RAW，不复制或覆盖原始像素；创建后仍须重新读模块。

## 模块与单位

| module | patch 参数及接口范围 |
|---|---|
| white_balance | temperature 800～14000 K，tint −50～50 |
| exposure | exposure −4～4 EV，contrast/brightness −50～50，saturation −100～100 |
| hdr | highlight_recovery/shadow_recovery/white_recovery/black_recovery −100～100 |
| levels | channel=rgb/red/green/blue；input/output_black/white 0～255；midtone −1～1 |
| curves | channel=rgb/red/green/blue/luma；完整 points 数组，2～64 个 [x,y]，均 0～1；x 严格递增，首尾 x 为 0、1 |
| color_mixer | mode=basic/advanced/skin，量程见 COLOR_API |
| color_balance | wheel=master/shadows/midtones/highlights；x/y −50～50 且半径≤50；lightness −50～50，master 无亮度 |
| layers | action 和本次返回的完整 layer_ref；透明度 0～100 |
| masks | create_subject/create_background 或细化动作，见 COLOR_API |

范围不是推荐幅度，省略字段保持原值。色轮 x/y 受 C1 色轮方向设置影响，不是色相角或饱和度百分比。

```json
{"module":"curves","patch":{"channel":"luma","points":[[0,0],[0.25,0.23],[0.7,0.73],[1,1]]}}
```

```json
{"module":"color_mixer","patch":{"mode":"basic","color":"yellow","hue":-8,"saturation":5,"lightness":-2}}
```

```json
{"module":"color_balance","patch":{"wheel":"highlights","x":2,"y":1,"lightness":0}}
```

以上片段仍需 target、observation_token、新 request_id 才能执行。不要为用遍工具增加无目的调整。

## 图层和蒙版

read(layers) 返回 state.layers；用其中完整 layer_ref 选择图层。选层后 target.layer 改变，重新读参数，再用相同 adjust。masks 的 create_subject/create_background 调用 C1 AI 蒙版，返回创建结果；结果未知不重复创建。用 mask 预览检查覆盖，再看 native 效果。

create_empty 新建空调整层。remove_created 还须提供 creation_operation_id，只移除工具创建的层。图层／蒙版操作后引用可能变化，以新 read 为准。渐变坐标属于 RAW，不能用预览宽高直接归一化替代 map_points。

## 返回值

- ok：读取成功；control_confirmed：写入回读一致；completed：该操作完成。都不自动证明审美质量。
- refused：条件不满足，检查 error 和派发标记；不能一概理解为未触碰模型。
- partial/outcome_unknown：部分或未知结果，查原编号并重新读取，不自动重放或承诺事务回滚。
- 浮点回读存在微小误差；多 setter 中途失败不等于已写值自动恢复。

预览只保留最近 8 张缓存，重要前态由客户端自行保存。原生预览不是全尺寸导出或独立 ICC 验收。详见 [COLOR_API](COLOR_API.md) 和 [NAVIGATION_PREVIEW](NAVIGATION_PREVIEW.md)。
