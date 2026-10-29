# 色彩与色阶接口

本文中的原生预览、Skin、图层排序/复制及完整蒙版能力以 Windows 为基线。Mac 共用六工具，但必须读取当前 `context.capabilities`，其范围和差异见 [MACOS](MACOS.md)；不可由下面示例推定 Mac 支持。

所有调整先 `read(module)`，使用返回的 `target`、`observation_token` 和新 `request_id` 调用 `adjust`。只可写登记的测试副本；Background 和当前调整层共用同一接口。

## 色阶

```json
{"module":"levels","patch":{"channel":"rgb","input_black":2,"input_white":253,"midtone":0.02,"output_black":0,"output_white":255}}
```

channel支持rgb/red/green/blue。五项可部分提供；与当前值合并后在C1内验证。端点界面单位0..255，底层/255；中间调-1..1，不接收直接Gamma。原生float精度下输入黑<白、输出黑<=白。

## 色区

read(module=color_mixer)返回Basic的colors/indexes，以及advanced/skin的ranges、serialized、selected_index。每项包含index、type、原生Device RGB、HSL、smoothness、均匀度、范围、enabled/inverted。

```json
{"module":"color_mixer","patch":{"mode":"advanced","action":"add","rgb":[70,140,50],"hue":1.5,"saturation":-3,"lightness":2,"smoothness":15}}
```

```json
{"module":"color_mixer","patch":{"mode":"skin","action":"add","rgb":[180,120,95],"uniformity_hue":5,"uniformity_saturation":4,"uniformity_lightness":3}}
```

```json
{"module":"color_mixer","patch":{"mode":"advanced","index":0,"hue_low":-18,"hue_high":18,"saturation_low":-35,"saturation_high":35,"enabled":true,"inverted":false}}
```

```json
{"module":"color_mixer","patch":{"mode":"advanced","action":"remove","index":0}}
```

默认action为update；add需要rgb，update/remove需要本次read里的index。RGB是原生Device输入0..255，不是屏幕坐标或承诺带ICC的屏幕采样值。Skin只有一项，已有时更新，不追加第二项。Advanced容量由C1宿主判断，当前基本九区并预留肤色的宿主预算为25。

| 界面参数 | Advanced | Skin |
|---|---|---|
| hue | -30..30 | -5..5 |
| saturation | -100..200 | -30..30 |
| lightness | -100..100 | -10..10 |
| smoothness | 1..30 | 1..30 |
| uniformity_hue/saturation/lightness | 不提供 | 0..100 |

Lightness内部/10。hue_low/high是相对源色相的有符号角偏移，low<=-2、high>=2、跨度<=359度。saturation_low/high是相对源饱和度的百分点偏移，low<=-2、high>=2；未把±100说成原生硬限制，按有限float32域处理和回读。删除色区会保持选中索引有效，空列表=-1。

Basic仍使用 `{mode:basic,color:red|orange|yellow|green|cyan|blue|purple|magenta|all,hue?,saturation?,lightness?}`；全部颜色不接收lightness。八区及全部颜色均已写测。超时只查原编号和当前模型，不重发可能已执行的新增命令。

## 模块重置与复用

曝光、白平衡、HDR、色阶、曲线、混色器、色轮都支持：

```json
{"module":"curves","patch":{"action":"reset"}}
```

直接调用 C1 `ResetProperties(false, keys)`，由宿主决定全局/局部默认值，不手写一套零值。每次只重置指定模块。

复制时保存完整 `read(module)` 的 observation_token，选定目标照片/图层并重新读取目标模块，再提交：

```json
{"module":"curves","patch":{"action":"paste","source_observation_token":"源模块读取返回的token"}}
```

外层 target、observation_token 指向目标；patch 中的 source_observation_token 指向源。粘贴使用那次读取的参数快照，不会跟随源照片后来的改动。只复制所选模块，不复制图层、蒙版、评分或其他模块。混色器按模块复制 Basic/Advanced/Skin 完整范围列表。C1 的处理引擎版本必须一致；目标仍必须是登记副本。没有样式导入、临时预设、系统剪贴板或额外快照文件。请求中的回读直接复用同次 C1 调用结果。

## 图层管理

`read(layers)` 返回 layers、selected_layer 和 details（启用、透明度）。除 select 外，下列动作操作已选中的调整层；layer_ref 使用本次返回的完整对象。

| action | 附加字段 | 含义 |
|---|---|---|
| rename | layer_ref, name | 重命名，1～128个字符 |
| set_enabled | layer_ref, enabled | 启用/停用 |
| duplicate | layer_ref, name可省略 | 原生复制参数和蒙版，选中新层 |
| move | layer_ref, index | 调整层原生列表的零起始位置，不包含Background |
| remove_created | layer_ref, creation_operation_id | 只移除由本工具创建/复制的层 |

创建、选择、透明度接口仍可用。重命名或复制蒙版后，返回新的 layer_ref；不要重复使用旧对象。原生蒙版 GUID 可能因复制而改变，删除资格同时核对同进程内的原生图层对象身份。

## 蒙版细化

`read(masks)` 的 state.mask 返回当前蒙版、原生子蒙版索引、组合方式、亮度范围、渐变几何和内容版本。内容版本来自原生蒙版句柄，不是像素哈希。以下动作使用当前调整层；source_layer 只能引用同一照片本次清单中的另一调整层。

| action | 附加字段 | 作用 |
|---|---|---|
| invert | 无 | 原生反相 |
| copy | source_layer | 替换目标整张蒙版，保留源层 |
| combine | source_layer, mode | add / subtract / intersect；保留源层并新增原生子蒙版 |
| feather | radius | 原生羽化，0～300；参数化蒙版可能由C1栅格化 |
| fill / clear | fill需要density | density 0～1；原生填充/清除 |
| select_submask | index | 使用返回的原生index，-1指整体；顺序不等同界面倒序列表 |
| linear_gradient | start, stop, midpoint | 两端点与中间位置 |
| radial_gradient | center, radius_x, radius_y, falloff, angle, inverted | 椭圆渐变 |
| luma_range | shadow, shadow_falloff, highlight, highlight_falloff, radius, sensitivity, inverted | 替换现有亮度组件，保留其他组件，相交应用 |

渐变端点/中心是相对于原始RAW宽高的归一化 `[x,y]`；半径也按原始宽高归一化，范围0.001～1。它们不是经过旋转、裁切后的查看器坐标。midpoint为0.01～0.99，falloff为0～1，angle为−360～360度，inverted为布尔值。填充、清除、反相、羽化和渐变遵循C1当前子蒙版选择语义；copy明确替换整张蒙版。

```json
{"module":"masks","patch":{"action":"linear_gradient","start":[0.25,0.25],"stop":[0.75,0.75],"midpoint":0.5}}
```

```json
{"module":"masks","patch":{"action":"luma_range","shadow":40,"shadow_falloff":20,"highlight":200,"highlight_falloff":230,"radius":2,"sensitivity":50,"inverted":false}}
```

亮度四端点0～255，顺序必须为 shadow_falloff ≤ shadow ≤ highlight ≤ highlight_falloff；radius 0～300，sensitivity 0～100。主体/背景AI蒙版入口不变。所有功能调用宿主内部对象及Undo通知，不写侧车文件，不通过界面按钮或样式文件执行。
