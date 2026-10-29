# 0.3.0a2 / v0.3.0-alpha.2

Capture One AI for Mac 的说明与分发整理版。实测范围为 Apple Silicon、Capture One 16.8.6.25 和 Session，仍标为实验性预发布。

- 首页、安装、API、支持范围、开发文档和 skill 统一面向 Mac。
- 本机 MCP 配置示例使用 Mac Python 路径。
- Mac 分发移除其他平台启动入口和构建组件，保留必要的来源及 MIT 版权声明。
- 调色运行逻辑保持此前已验证实现；本次不重新宣称完成宿主功能验收。

在 Releases 下载 Source code 即可取得完整源码和 skills/capture-one-edit。解压后按 [安装说明](INSTALL.md) 使用 Install.command，再接入本机 MCP；skill 需另按 [SKILL](SKILL.md) 放入客户端目录。

基础调色、曲线/色阶、Basic/Advanced、色轮、颗粒、受限图层/蒙版及实际 JPEG 预览的证据见 [MAC_VALIDATION](MAC_VALIDATION.md)。Skin 参数、排序/复制图层、渐变/组合/子蒙版、蒙版像素、坐标映射和无导出渲染暂不可用。

发布内容不携带照片、预览、Session、私人路径/配置、候选资格、令牌或操作数据库。
