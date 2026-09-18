# task-router

[![CI](https://github.com/fengbochao/task-router/actions/workflows/ci.yml/badge.svg)](https://github.com/fengbochao/task-router/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-114%20passing-brightgreen)](tests/)

Codex 多模型任务路由与故障恢复系统。在 Codex 对话中自动按任务类型分配模型，额度耗尽时透明切换到另一类模型继续服务，上下文完整保留。

```mermaid
flowchart LR
    U[用户] --> C[Codex]
    C --> P[本地模型代理]
    P -->|按家族切换| G[GPT 模型]
    P -->|按家族切换| L[GLM 模型]
    C --> M[task-router 插件]
    M --> B[后台任务控制器]
    B --> G
    B --> L
```

## 安装

### 完整安装（推荐）

```bash
git clone https://github.com/fengbochao/task-router.git
cd task-router && ./install.sh
```

完整安装包含 Codex 插件（对话工具、任务路由、后台执行）、本地模型代理（systemd 用户服务，自动故障切换）和个人配置文件。安装完成后新开一个 Codex 对话即可使用。

### 通过 Codex 插件市场安装

```bash
codex plugin marketplace add https://github.com/fengbochao/task-router.git
codex plugin add task-router@fengbochao-plugins
```

此方式安装插件功能；模型代理需按[模型代理文档](docs/model-proxy.md)单独配置。

### 验证

在 Codex 对话中说：

```text
用 task-router 诊断一下配置
```

或从终端运行：

```bash
codex plugin list --marketplace fengbochao-plugins --json
systemctl --user status task-router-proxy
```

## 能力

### 对话内任务管理

| 工具 | 能力 |
|---|---|
| `task_submit` | 在对话中提交后台任务，自动按类型分配模型 |
| `task_status` | 查询任务状态、列出最近任务、分页读取长结果 |
| `task_wait` | 有界等待任务完成 |
| `task_cancel` | 请求取消正在执行的任务 |
| `task_resume` | 恢复保存的可重试任务 |
| `router_diagnose` | 检查配置来源、模型分工和候选选择原因 |

### 按任务类型路由模型

| 任务类型 | 默认首选 | 候选链 |
|---|---|---|
| 普通讨论 | gpt-5.5 | gpt-5.6-sol → glm-5.3 |
| 方案规划、深度分析 | gpt-6-astra | gpt-5.6-sol → glm-5.3 |
| 代码审查 | glm-5.3 | glm-5.2 → glm-5.3-flash |
| 编码、重构、测试 | glm-5.3 | glm-5.2 → glm-5.3-flash |
| 读代码、机械小改 | glm-5.3 | glm-5.2 → glm-5.3-flash |

在 `~/.config/task-router/routing.json` 中修改模型 ID、启用状态或候选顺序即可调整分工。详见[配置参考](plugins/task-router/skills/task-router/references/routing-schema.md)。

### 家族级故障切换

本地模型代理运行在 Codex 与 API 之间：

- GPT 或 GLM 任一类返回 503 时，代理自动将该类加入冷却并改写请求为另一类的首选模型，对 Codex 完全透明。
- 429 限流透传，Codex 按内建退避机制重试。
- 流式响应中断时，代理将该类短冷却，Codex 重试请求自动切换到另一类。
- 冷却状态持久化，代理重启后保留。
- 切换发生时在响应中携带 `X-Task-Router-Failover` 头和事实性模型身份提示。

运维命令：

```bash
systemctl --user status task-router-proxy
tail -f ~/.local/state/task-router/proxy-access.jsonl
cat ~/.local/state/task-router/proxy-state.json
```

### 后台任务持久化

任务和执行记录存储在 SQLite 中，进程重启后可查询和恢复。相同请求键的任务自动去重，已完成的任务再次恢复只返回结果。文件变化在每次尝试前后保存摘要，续做任务基于实际文件状态。尝试次数和时间预算由程序管理，跨进程重启保留。

## 环境要求

| 组件 | 要求 |
|---|---|
| Python | 3.11+ |
| Codex CLI | 0.154.0+ |
| 操作系统 | Linux |
| API | OpenAI 兼容接口，提供 GPT 和 GLM 两类模型 |

缺少 MCP SDK 时，插件自动在私有环境安装 `mcp==1.27.0`。

## 配置

个人配置位于 `~/.config/task-router/routing.json`，升级时自动保留。可调整模型 ID、启用状态、推理档位、候选顺序和任务到角色的映射。修改后立即生效。

## 文档

- [配置参考](plugins/task-router/skills/task-router/references/routing-schema.md)
- [模型代理](docs/model-proxy.md)
- [候选模型验证](docs/model-check-20260914.md)
- [控制器架构](docs/controller-design.md)
- [CONTRIBUTING](CONTRIBUTING.md)
- [CHANGELOG](CHANGELOG.md)

## License

[MIT](LICENSE)
