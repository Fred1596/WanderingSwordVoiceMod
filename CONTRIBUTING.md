# 贡献指南

仓库已经一次性公开项目最终使用的对白目录、角色画像和声线规划。除此之外，提交前
请确认改动不再增加游戏资源或对白，也不包含生成音频、模型权重、第三方二进制
文件、运行日志或任何密钥。

## 开发流程

1. 从 `main` 建立功能分支；
2. 只提交能够公开分发的原创代码、测试与文档；
3. 为行为变更补充最小测试或可复现步骤；
4. 在仓库根目录运行：

```powershell
python -m compileall bridge catalog scripts tests
python -m unittest discover -s tests -v
python scripts\check_repository_hygiene.py
```

5. 提交 Pull Request，解释问题、修改范围和验证结果。

## Issue 内容边界

可以提交最小化、匿名化的日志片段。请不要上传完整解包文件、成段游戏台词、WAV、
本机用户名、服务器地址或 API Key。若问题只能通过私有输入复现，请用合成示例描述
数据形状。

## 代码风格

- Python 使用 4 空格缩进和类型标注；
- 文件路径优先使用 `pathlib.Path`；
- 密钥只从环境变量读取；
- 新增输出目录时同步更新 `.gitignore`；
- 生成过程必须支持断点续跑，并在覆盖文件前做明确校验。
