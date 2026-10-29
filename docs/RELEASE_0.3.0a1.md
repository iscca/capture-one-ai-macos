# 0.3.0a1 / v0.3.0-alpha.1

本次为 **Mac 实验性预发布**，新增可独立安装的 `capture-one-edit` skill；Windows 保留原有实现与 0.2.0a1 验证基线。本次没有 Windows 实机回归，不替换原 Windows 发布包。

## 变化

- Mac 原生 Apple Events 后端、私有 Unix socket 单执行器、安装入口和本机配置。
- 基础调色、五通道曲线、四通道色阶、Basic/Advanced、色轮、六类背景层颗粒。
- 受限图层/蒙版与集合导航；用户明确启用的临时 JPEG 实际渲染及 MCP 图片块。
- skill 采用看图→候选→调整→回读→实际预览流程，不携带私人修图参数。
- 修复集合范围校验与导出队列竞态；严格保留不确定请求的原编号查询、不重放与目标保护。

Mac 仍不支持 Skin 逐项参数、图层排序/复制、渐变/组合/子蒙版、直接蒙版像素、坐标映射和无导出原生预览。图层排序/复制实测失败，已关闭。详见 [Mac 验证报告](MAC_VALIDATION.md)。

## 下载选择

- `CaptureOne-AI-macOS-Experimental-0.3.0a1.zip`：本项目源码、安装入口、文档、测试与 skill；不含 Python、Capture One 或厂商 DLL。适合 Mac 用户从固定目录安装。
- `capture-one-edit-0.3.0a1.zip`：仅 skill 与许可证。仍需单独安装并连接本机 MCP/CLI。
- `SHA256SUMS.txt`：下载包校验值。包内 `RELEASE-MANIFEST.json` 记录文件摘要与平台资格。

## 验证及边界

Mac 代表性真实读写、回读、预览与恢复见验证报告；非全组合验收。38 项 Mac 专项保护测试通过；此前合并选择性检查为 144 passed、43 个已存在的历史失败、26 subtests passed。没有将旧测试失败隐藏为全套通过。发布分发检查单独记录在 [RELEASE_CHECKS](RELEASE_CHECKS.md)。

只分发明确文件清单中的通用代码与资料。不附原照片、预览、Session、用户名/私人路径、生成的连接配置、候选资格、令牌、原始实测收据或操作数据库。使用时这些内容保留在运行用户的本机状态目录。MIT 只覆盖本项目，不授予 Capture One 厂商组件的分发权。
