# 原生选图与预览

本文中的原生预览、Skin、图层排序/复制及完整蒙版能力以 Windows 为基线。Mac 共用六工具，但必须读取当前 `context.capabilities`，其范围和差异见 [MACOS](MACOS.md)；不可由下面示例推定 Mac 支持。

2026-10-29，Windows C1 16.8.6.3784。继续使用现有六个工具，没有新增服务。

## 选图

1. `images(action="list", scope="filtered", limit=200)` 返回当前集合的变体，包括屏幕外照片；`scope="all"` 包含该集合中被过滤的变体，不表示整个Catalog。
2. 若有 `next_offset`，下一页传同一个 `collection_token`。集合内容或顺序改变会拒绝旧分页凭据。
3. `images(action="select", variant_uuid="列表中的UUID", target=列表.target, observation_token=列表.observation_token)`。列表凭据只允许选图，选择前核对文档、集合和原选中集合；不要求先取得单张照片的读取凭据。
4. 选择结果的 `after` 附带新的照片身份和读取凭据，可用于 `next`、`previous` 或创建副本。需要修改参数时，仍先 `read(module=...)`，并遵守仅登记候选副本可写的限制。

`variant_id` 保留兼容，其含义是原生数据库变体编号；新调用优先使用 `variant_uuid`。翻页不改变选择。列表可能包含同一RAW的多个变体，不把变体数量称为独立RAW数量。

## 预览

```python
observed = read(fields=[])
result = preview(target=observed["target"],
                 observation_token=observed["observation_token"],
                 kind="native", max_size=1200)
```

`native` 是默认值。最大长边128～1600像素；由C1根据当前调整快照异步渲染，完成后返回PNG。无需截图、导出窗口、焦点或样式文件。`kind="mask"` 对选中的调整图层返回完整灰度蒙版，白色为有覆盖、黑色为无覆盖；它不是当前子蒙版单独预览。

结果中的 `preview_id` 绑定实例、文档、变体、图层、调整版本、类型和大小。相同状态复用最近任务；变化后拒绝旧结果。后台只保留最近一个任务，磁盘只保留最近8张PNG；图片路径是临时缓存，不能当作永久导出文件。MCP直接发送PNG，不另生成JPEG。

## 坐标

```python
preview(action="map_points", kind="native", max_size=1200,
        preview_id=result["result"]["preview_id"],
        points=[{"x": 400, "y": 600}], direction="preview_to_raw",
        target=observed["target"], observation_token=observed["observation_token"])
```

输入/输出使用左上角为原点的像素边界坐标。`raw_to_preview` 支持反向转换。调用C1的非线性转换，包含当前方向、裁切和已有几何状态；不修改几何参数。返回RAW坐标除以 `geometry.raw_dimensions.Width/Height`，才是线性/径向蒙版API需要的归一化坐标。最多256个点；映射时沿用渲染的 `kind` 和 `max_size`。

旧 `kind="viewer", action="capture"` 仍是显示采样；旧 `kind="render"` 仍为显式导出兼容入口。新预览并不完成全尺寸成片导出或独立的ICC色彩准确性验收。
