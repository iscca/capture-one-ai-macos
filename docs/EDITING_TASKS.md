# P0 摄影师修图任务与参考调色

在现有六工具内新增 `operations(action=task)`，MCP 与 JSON CLI 共用本机 Core。
任务保存在私人操作数据库，预览保存在私人 `task-previews` 目录，不进入仓库或发布包。
不新增 AI 账号、云端服务或模型依赖；照片判断由支持图片的客户端完成。

## 使用流程

1. `diagnose` 检查连接、单选照片、Session、候选写入资格、预览授权和未决操作。
   它不改变授权。图片显示能力仍需客户端确认。
2. `read(fields=[])` 后 `start`，记录当前版本上的目标与保护项；不自动重置已有修图。
3. 得到已授权的实际 C1 预览，用该预览请求编号 `checkpoint` 保存起点。
4. `images(create_candidate)` 一次后 `bind`，绑定该任务起点创建的登记候选。
5. 用原有 read → adjust → 回读 → preview 调整，再 `checkpoint` 保存结果。
6. `compare` 比较起始/上一轮/当前/已认可版本；MCP 回传标注图片，CLI 返回本机路径。
7. 实际看图后 `review`；收到用户反馈用 `feedback`，仅用户明确认可时 `accept`。
8. `restore` 恢复声明范围内的参数后，重新预览、保存检查点和看图复核。

任务写操作需 `request_id`、当前 `target` 和 `observation_token`。
`stop` 仅改任务状态，不要求 C1 仍在线。`get/compare/diagnose` 为只读。
重复编号只返回原收据；编号内容变化拒绝。超时查询原编号，不换号重放。
`operations(get/list/reconcile)` 原语义保留；任务 `restore` 是显式写入入口，不能与只读 reconcile 混淆。

## 参数

```json
{
  "command": "operations",
  "action": "task",
  "task_action": "start",
  "payload": {
    "mode": "reference_match",
    "goals": ["借鉴柔和高光和低饱和绿色"],
    "protections": ["保留人物肤色和现场暖光"],
    "subject": "环境人像",
    "strength": "close",
    "usage": "屏幕展示",
    "starting_point": "current",
    "allowed_local": true,
    "acceptance": ["面部自然，绿色克制，高光仍有层次"],
    "references": [{"id": "client-attachment-1", "role": "影调及绿色", "kind": "cross_scene"}],
    "reference_analysis": {
      "tone": "柔和高光，较深黑位",
      "color": "阴影偏冷，绿色低饱和",
      "texture": "轻微颗粒观感",
      "limitations": "原片没有参考中的逆光轮廓",
      "priority": "肤色和暖光优先于参考偏色"
    }
  }
}
```

示例省略必须从实际 read 取得的目标和凭据，以及本次生成的唯一请求编号。
`mode=optimize|reference_match`；`strength=light|close|maximum` 是意图，不是参数倍数。
`references.kind=same_scene|cross_scene`，每张参考必须有唯一客户端 ID 和用途。
桥接程序不根据附件 ID 获取参考图，也不宣称完成自动图像匹配；客户端实际看图并保存分析。
不提供缺乏测量依据的相似度百分比。具体摄影师判断流程见
[skill 工作流](../skills/capture-one-edit/references/photographic-workflow.md)。

| task_action | payload | 作用 |
|---|---|---|
| start | 上述 brief | 创建任务，返回 `result.task.id` |
| get | 无 | 用 task_id 读取 `task` |
| diagnose | 无 | 首次连接诊断，不改权限 |
| bind | candidate_request_id | 核对成功的候选创建收据后绑定 |
| update | brief 部分字段 | 合并目标；列表字段整体替换，保护项仅按用户明确修改 |
| checkpoint | preview_request_id, label | 保留真实预览、修订及当前层八个调色模块快照；局部层排除颗粒 |
| compare | roles 数组 | 最多三项 initial/previous/current/accepted，返回保留的历史画面 |
| review | checkpoint_id, seen_sha256, improved, assessment | 当前版本的客户端视觉声明 |
| feedback | kind, message, protect?, checkpoint_id? | 记录用户反馈，追加保护项 |
| accept | checkpoint_id, user_confirmation | 记录用户明确认可当前已复核版本 |
| restore | checkpoint_id | 在绑定候选中恢复快照所覆盖的当前层调色参数 |
| stop | 无 | 停止任务；不取消已被宿主接受的操作 |

`review.assessment` 必须包括 goal/protections/light_color/edges/remaining 的文字判断，
复刻任务另需 reference。`seen_sha256` 使用检查点原图哈希；显示代理可能经过色彩转换。
这只标识客户端声明看过哪张图，不能证明模型确实看图或审美正确。
同一检查点只记录一次 review；连续两轮 improved=false 后进入 needs_direction，skill 停止自动试调。
`feedback.kind` 为 brightness/temperature/strength/subject/background/prefer_previous/custom。
保护项是计划及复核约束，不是精确区域锁定。只有摄影师确认才调用 accept。

## 版本及恢复边界

支持 `context.editing_tasks.features` 中 P1 能力的开发版本可继续使用
[参考目标、定向反馈与局部认可](P1_EDITING.md)。旧任务按原格式读取。

检查点使用与实际导出相同的脚本可见修订指纹，保存前后核对目标和状态。
手动修改后旧预览不能保存为新检查点；旧检查点不能作为当前效果 review/accept。
原始任务起点保留；候选上的新轮次不反复创建副本。新方向使用新任务和新候选。
参考优先级、题材检查、两轮无改善停止等审美规则由 skill 执行，不伪装成参数执行器的硬保证。

当前恢复范围为 `selected_layer_color_parameters`：白平衡、曝光、HDR、曲线、色阶、
Basic/Advanced 混色器、色轮，以及背景层颗粒。必须仍选中快照对应层，层清单/设置保持一致。
起点在原版本时，允许将其参数粘贴到绑定候选，不允许写回原版本。
恢复通过原 `adjust(paste)`、候选资格、当前目标、旧值检查和独立子请求编号完成。
父收据持久记录子请求；中断后只查询，不能换号重跑整段恢复。

不恢复图层结构、蒙版像素、其他层参数、隐藏 Skin 参数、其他工具或完整 C1 历史。
即使整图预览相同，也不把单层快照称为整图撤销。恢复完成进入 needs_preview，
参数恢复和画面恢复分开说明。不存在多模块事务保证。
当前没有独立 GUI；比较和进度通过 AI 客户端呈现。参考图由客户端展示。
本机存储不等于云端视觉处理时照片不离开设备。低分辨率预览不验收印刷或原尺寸细节。

## 验证

自动检查覆盖持久化、候选绑定、过期预览、文件哈希、切图保护、原请求去重、
反馈保护项、视觉声明与认可区分、停止和恢复范围。它们不证明 C1 宿主或审美效果。
本次开发实际运行的检查与实机证据见 [P0 验证记录](P0_VALIDATION.md)。
