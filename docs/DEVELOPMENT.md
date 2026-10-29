# Mac 开发与分发

先读 [AGENTS](../AGENTS.md) 和 [MAC_PORT](MAC_PORT.md)。开发环境使用 Python 3.11+，安装测试依赖可运行 `.venv/bin/python -m pip install '.[test]'`。没有 Capture One 的机器不能提供实机通过结论。

保留六工具与公共请求格式，Mac 原生调用优先。真实写测只使用用户选定 Session 的登记候选，参数回读后查看实际 C1 预览；不要为修图重跑全部开发测试。

## 打包

```sh
.venv/bin/python scripts/package_release.py --target macos --output dist/0.3.0a2
.venv/bin/python scripts/package_release.py --target skill --output dist/0.3.0a2
```

使用明确公开文件清单，扫描私人路径、配置名、符号链接与检测到的凭据。Mac 包不附其他平台启动入口或厂商组件；skill 包只带技能及许可证。脚本拒绝覆盖同名 ZIP，历史发布不自动替换。

包内 RELEASE-MANIFEST.json 记录摘要，SHA256SUMS.txt 核对最终 ZIP。上传前检查实际归档，而不只检查工作目录。照片、预览、配置、Session、候选资格、令牌、收据、运行状态及原始实测记录不分发。

`check_release.py` 默认不导出。用户已明确授权时才用 `--require-live --allow-preview-export` 验证实时图片块；没有选中目标时不自动选图或开文档。文档改动检查链接、清单与归档，不重复宣称进行了宿主功能验收。
