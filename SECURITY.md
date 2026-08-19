# Security Policy

## Reporting a vulnerability

请不要在公开 Issue 中披露可直接利用的漏洞、个人密钥或包含隐私的日志。优先通过
GitHub 的 Security Advisories / Private vulnerability reporting 联系维护者。

报告应包含受影响版本、影响范围、最小复现步骤和建议修复方向，但不要附带游戏资源
或第三方受版权保护的文件。

## Secrets

本项目不会要求把 API Key 写入仓库。密钥只应保存在环境变量或本机私有配置中。
如果密钥曾被提交到 Git 历史，应立即在服务商控制台吊销并轮换；仅删除当前文件并
不能消除泄露。

## Binary safety

开源仓库不托管 UE4SS、repak、UAssetGUI 或其他可执行文件。请从上游项目下载，
核对发布者、许可证和校验值，不要使用来源不明的代理 DLL。

