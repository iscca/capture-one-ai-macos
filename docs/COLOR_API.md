# Mac 调色模块

所有修改先 read，带原目标、观察凭据和新 request_id 调用 adjust。参数为绝对目标。可用动作以 context 为准。

| 模块 | patch 约定 |
|---|---|
| white_balance | temperature / tint |
| exposure | exposure / contrast / brightness / saturation |
| hdr | highlight_recovery / shadow_recovery / white_recovery / black_recovery |
| curves | channel=rgb/red/green/blue/luma；points 为完整通道的 0..1 控制点 |
| levels | channel=rgb/red/green/blue；input_black/input_white/output_black/output_white 为 0..255，midtone 为 −1..1 |
| color_mixer | Basic 使用 mode=basic、color、hue/saturation/lightness；all 色区没有 lightness。Advanced 按已读原生 index 修改，增删需核对当前清单 |
| color_balance | wheel=master/shadows/midtones/highlights，x/y 和适用 lightness；主轮没有 lightness |
| film_grain | type、impact、granularity；强度/大小 0..100，仅背景层 |

颗粒类型为 fine、silver_rich、soft、cubic、tabular、harsh；impact=0 关闭。颜色轮原生 hue=0 对应 +x，不读取界面色轮旋转偏好。不要将预览 sRGB 取色当成精确原生 Device RGB 选色。

## 图层与蒙版

图层支持创建、选择、透明度、开关、工具层改名和按创建凭据移除。工具创建层带唯一名称标记；不改名未标记既有层。排序和复制已停用。

蒙版支持 AI 主体/背景、复制、反相、清除、受限填充、原生羽化和亮度范围。羽化 native_amount=0..100 不是像素 radius。AI 生成层 fill 可能保留宿主生成/子蒙版约束；命令完成不能证明已形成全画幅蒙版。

缺少蒙版像素回读时返回 partial；按原请求只读核对后仍要实际查看渲染。Skin 逐项参数、Advanced 反选、渐变/组合/子蒙版选择和直接蒙版像素不可用。

## 模块复用

受支持的模块可 reset，或 paste 先前读取的完整模块观察快照。白平衡没有相机中性默认重置，可粘贴已保存值。混色器快照只覆盖可见 Basic/Advanced，不含隐藏 Skin 参数。

[实机范围](MAC_VALIDATION.md) · [六工具接口](API.md)
