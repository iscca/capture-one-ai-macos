# P1-A / P1-B：参考目标与定向反馈

在 [P0 修图任务](EDITING_TASKS.md) 上增加参考特征跟踪、反馈解释、局部认可和逐项复核。
继续使用六工具，新增动作仍在 `operations(action=task)` 内，不新增宿主执行器或云服务。
长期偏好库属于后续 P1-C，本次不实现。客户端通过 `context.editing_tasks.features` 检查能力。

## 参考图复刻

实际看过参考图和当前 C1 检查点后，提交 `task_action=reference_plan`：

```json
{
  "checkpoint_id": "从当前任务取得的检查点编号",
  "targets": [{
    "id": "soft-highlights",
    "reference_id": "当前 brief 中的参考图 ID",
    "observation": "参考图亮部过渡柔和，仍有清楚的受光感",
    "uncertainty": "目标照片光线更硬，不能直接比较全图直方图",
    "region": "受光墙面和主体亮部",
    "direction": "减轻生硬反差，保留亮感",
    "protect": ["白色表面不灰暗", "肤色自然"],
    "priority": 1,
    "limitation": "调色不能重建参考图的布光"
  }]
}
```

任务必须为 reference_match。一次计划保存 1..12 项，每项 ID 唯一，参考 ID 必须存在于当前 brief。
priority=1..5，1 最高。region 为语义区域，不承诺选区、像素锁定或 RAW 坐标。
observation、uncertainty、limitation 均记录文字判断，没有数值相似度。
计划返回本机生成的 id；后续检查点冻结计划及参考角色。
再次规划保留旧计划历史，更新 brief 的 mode/references 则停用当前计划，旧轮次的依据保持不变。

客户端先处理影响最大的少数差异，同场景可比较对应区域，跨场景借鉴关系。
参考图由视觉客户端提供，桥不根据附件 ID 自动获取图片。跨会话必须检查参考图是否仍可访问；
仅有文字分析时说明限制。光线、景深、雾气、材质差异不通过无限调参掩盖。

## 定向反馈

原反馈 message 和 protect 兼容保留，P1 另提交 interpretation：

```json
{
  "checkpoint_id": "当前检查点编号",
  "kind": "background",
  "message": "脸现在可以，背景绿色再灰一点，整张不要变暗",
  "protect": ["整体亮度"],
  "interpretation": {
    "problem": "背景绿色过于抢眼",
    "region": "背景植被",
    "direction": "降低绿色抢眼程度",
    "method": "color_range",
    "risks": "检查相近肤色、衣服和亮度是否受到影响"
  }
}
```

method=global/color_range/local/inspect_only。它是客户端选择工具的意图，不是自动执行参数或生成蒙版。
allowed_local=false 时拒绝保存 local 方案；实际调用仍检查宿主能力。
反馈必须关联当前检查点，手动修改导致画面变化时先重新预览，不能沿用旧图反馈。
解释后的反馈获得 id 和 pending 状态，后续检查点冻结未解决反馈，并关联原反馈版本。
旧式纯文字反馈不自动转换成新的结构化解释，不补造历史。

## 局部认可

用户明确说“喜欢这一版的肤色”时，提交 `task_action=recognize`：

```json
{
  "checkpoint_id": "用户认可的已复核版本编号",
  "aspect": "当前面部肤色和明暗关系",
  "user_confirmation": "脸现在可以"
}
```

可以引用同任务历史上已 review 的检查点。记录包含 id、版本、原用户确认、active 和 pixel_lock=false。
它不等于整图 accept、不自动混合不同版本、不保存长期偏好，也不保证面部像素不变。
后续检查点将认可项列入保护检查。只有用户明确替换旧认可时，添加 supersedes=[旧认可 ID]；
原记录保留为 inactive，不静默删除。

`compare` 除原 roles 外支持 `payload={checkpoint_ids:[...]}`，最多三项，不能与 roles 同用。
可展示认可的历史版本；返回各图参数恢复范围、快照所在层和模块，不冒充整图恢复。

## 每轮复核：改善、保持与退化

新检查点的 intent 保存本轮依据：reference_targets、protections、feedback 和 reference_plan_id。
review_required=true 表示本轮需要 P1 逐项保护检查。
逐项结果和原 review 的 assessment、seen_sha256、improved 一起提交：

| 字段 | 对应 intent 清单 | status |
|---|---|---|
| target_results | reference_targets | pending / close / conflict / limited |
| protection_results | protections | preserved / changed / uncertain |
| feedback_results | feedback | resolved / improved / unchanged / worse / conflict |

每项为 `{id,status,note}`，必须按检查点捕获的 ID 完整、无重复地提交。
缺项、未知 ID、重复 ID、缺少说明都拒绝，不写入半份 review。
客户端实际比较原反馈版本、认可版本和本轮结果，再说明获得了什么、保住了什么、还差什么。
桥验证声明结构和版本关系，不能证明模型真的看图或判断正确。

参考计划记录最近复核状态；已 resolved 的反馈不再进入下一轮待解决项。
保护项 changed、反馈 worse/conflict 或参考特征 conflict 进入 needs_direction；
保护项 uncertain 进入 needs_verification。即使整体 improved=true，也不应继续盲目试调。
停止自动试调由 skill 遵守；底层通用 adjust 不变，不声称语义保护是任意客户端都不能绕过的像素锁。
用户反馈可以重新定向，明确的整图认可仍用 accept。

## 兼容、隐私与验证

旧任务和检查点按原格式读取，不补造参考计划、局部认可和已完成反馈。
使用 P1 能力后的新轮次按 intent 增加逐项复核要求。原始观察、参数写入、request_id 去重和未知结果查询不变。
本次没有新增 Capture One 参数能力、图层复制/排序或蒙版像素读取；恢复仍是快照当前层调色参数。
数据只保存在本机私人任务数据库和预览目录，不进入分发包，不自动上传参考图。

验证覆盖计划与参考来源关系、过期画面拒绝、局部认可和整图接受区分、反馈前后版本关系、
逐项复核原子性、退化停止、历史图比较和旧任务重连。实机结果见 [P1 验证](P1_VALIDATION.md)。
