# Task Router

Codex 多模型任务路由与故障恢复系统。

## 能力

- 在 Codex 对话中提交、查询、取消和恢复任务。
- 按角色配置 GPT/GLM 模型池、优先顺序和推理档位。
- 本地代理根据已配置的家族故障信号选择另一类模型，转发请求中的上下文。
- SQLite 保存执行记录、重试预算、任务结果和文件变化摘要。
- 一次安装部署插件与用户级代理服务，健康检查后切换 API 地址。
- 升级保留个人路由配置，并提供配置备份和安装失败恢复。

## 完整安装

运行环境：Linux、Python 3.11+、可用的 systemd 用户会话，以及已配置 Responses
API provider 的 Codex CLI。沿用现有密钥环境变量，无需在安装命令中填写密钥。

```bash
git clone https://github.com/BucherFeng/task-router.git
cd task-router
./install.sh
```

也可以下载完整源码包，校验 SHA256SUMS，解压后运行 `./install.sh`。
安装后新开 Codex 对话，按通常方式提出讨论或编码任务即可。

## 验证

发布前完成 128 项本地自动化测试，涵盖路由、后台控制器、MCP 入口、安装迁移、
完整代理部署和故障恢复。完整安装测试使用模拟 Codex/systemd 命令与真实本地
HTTP 代理，避免测试访问个人账号。
