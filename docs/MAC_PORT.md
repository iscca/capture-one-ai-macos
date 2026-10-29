# macOS 开发交接

当前实现与安装见 [MACOS](MACOS.md)，实机范围、失败动作与证据边界见 [MAC_VALIDATION](MAC_VALIDATION.md)。原迁移计划已由实现替代；继续开发从最新源码和根目录 [AGENTS](../AGENTS.md) 开始。

## 调用与原生路线

`MCP/CLI → client/owner 平台入口 → macos_ipc → Core → MacOSBackend/MacTools → native/macos.js + macos_tools.js → Capture One Apple Events/JXA`。


## 当前限制与下一步

- Skin 逐项参数、Advanced 反选、渐变/组合/子蒙版、蒙版像素和无导出渲染尚未接通。
- 图层 move/duplicate 已实际失败并停用，不因代码中存在原生命令就标为可用。
- AI 层 fill 保留宿主生成/子蒙版约束；native_amount 羽化不等于像素 radius。
- 本机验证 profile 存于运行状态目录，不随发布包复制到别人的电脑。新安装不会继承别人的候选资格或本机验证标记。
- UI 辅助属于另外的显式选择，不自动作为原生失败后的降级方案。

修改一个真实模块后做代表性 read→adjust→回读→实际预览，必要时恢复候选。只读宿主字典或第三方代码不是本项目实机验证。不要写数据库/侧车文件、复制私人资格或重放结果未知的请求。

## 研究入口

- [Capture One 官方自动化说明](https://support.captureone.com/hc/en-us/articles/360002681418-Capture-One-Workflow-Automation-with-AppleScript)：核对官方接口后仍以本机脚本字典为准。
- [c1cmd](https://github.com/kiri11/c1cmd)：第三方实现，仅作为研究入口；其兼容与实测声明不继承到本项目，复用代码前固定提交并核对许可证。
