# 开发与分发

接续开发先读根目录 [AGENTS.md](../AGENTS.md)；Mac 方向另读 [MAC_PORT.md](MAC_PORT.md)。从最新 Git 源码开始，0.2.0a1 Release ZIP 保持为原 Windows 交付快照，不随文档提交被替换。

MCP 和 CLI 共用 c1ai.client → owner → Core → WindowsBackend → internal/NativeEngine.cs。不要另建按 AI 厂商区分的执行器；参数模块复用 read/adjust，视觉判断由客户端负责。

开发安装：Install.ps1 -EnvironmentOnly。需要测试依赖时运行 `.venv\Scripts\python.exe -m pip install '.[test]'`，按改动范围选择已有 tests，不把整套测试当作每次修图前置步骤。真实写测只用登记候选，不在公共 CI 上假装存在 C1。

`internal/build.ps1 -Bootstrap` 编译启动钩子；不带参数编译并发布引擎到本机状态配置，会更新当前桥接指向，不要在修图中随意执行。程序集名取源文件哈希，加载时校验 SHA-256。

生成分享包：

```powershell
.\.venv\Scripts\python.exe scripts\package_release.py
```

脚本按明确清单选源码、测试、文档和入口，只附与当前 C# 源哈希对应的两份本项目 DLL（存在时）。不收集旧 DLL、.venv、私人配置、原始验证 JSON、照片、日志和数据库。缺少当前 DLL 时生成可本机构建的源码包，不自动切换运行引擎。包内 RELEASE-MANIFEST.json 记录文件 SHA-256，ZIP 摘要在 dist/SHA256SUMS.txt。

Git 不追踪编译产物；Release 可附本项目 DLL，不包含 C1 厂商程序集。MIT 只覆盖本项目，第三方说明在根目录。

必要发布检查为文件清单与敏感信息扫描、语法、干净目录安装、MCP 工具发现和只读预览、ZIP 哈希。分发检查不是全部调色功能重新验收，不虚构跨平台兼容性。

## 0.3.0a1 Mac 与 skill 打包

```sh
.venv/bin/python scripts/package_release.py --target macos --output dist/0.3.0a1
.venv/bin/python scripts/package_release.py --target skill --output dist/0.3.0a1
```

默认 `--target windows` 保留原打包选择；Mac/skill 包不附 Windows 编译 DLL。脚本拒绝覆盖同名 ZIP，历史发布不自动替换。Mac 包带完整项目源码和说明，skill 包只带独立技能及许可证；安装依赖与 C1 均由用户提供。隐私检查同时拒绝个人 home 绝对路径、私人配置名、符号链接和检测到的凭据，仍需人工检查新增文字。

`check_release.py` 默认不导出，Mac 实时渲染检查仅在用户明确授权后加 `--require-live --allow-preview-export`；参数不改变本机配置。没有选中照片时跳过预览，不自动打开文档或选图。
