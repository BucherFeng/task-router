# 运行结构

Task Router 由 Codex 插件、后台任务控制器和本地模型代理组成。
插件管理对话中的任务入口，控制器管理执行生命周期，代理处理 API 请求的家族级切换。

## 源码职责

| 目录或文件 | 职责 |
|---|---|
| `plugins/task-router/.codex-plugin/plugin.json` | 插件元数据 |
| `plugins/task-router/.mcp.json` | MCP 服务启动入口 |
| `plugins/task-router/skills/task-router/SKILL.md` | 任务分类、提交和结果处理工作流 |
| `plugins/task-router/skills/task-router/scripts/` | 配置校验、迁移和模型候选选择 |
| `plugins/task-router/scripts/mcp_server.py` | 任务管理工具接口 |
| `plugins/task-router/scripts/background_worker.py` | 独立后台执行进程 |
| `plugins/task-router/scripts/task_router_runtime/` | 持久化、执行控制和 Codex 协议适配 |
| `scripts/install.py`、`scripts/proxy_install.py` | 安装、配置保留、服务部署和失败恢复 |
| `scripts/model_proxy.py` | 本地 HTTP 代理、家族冷却和请求日志 |
| `tests/` | 单元测试、协议测试和隔离安装测试 |

插件运行代码随插件包分发；完整安装额外部署模型代理和用户级 systemd 服务。

## 任务执行

1. 前台 Codex 根据用户意图调用 `task_submit`，传入任务类型、项目目录、目标和读写授权。
2. `service.py` 校验请求，使用稳定请求键去重，并将任务与路由配置快照写入 SQLite。
3. 后台进程调用 `controller.py`，按配置中的任务、角色和候选顺序选择模型。
4. `codex_adapter.py` 启动独立的 Codex app-server 会话，显式指定模型、推理档位和执行权限。
5. 执行结果、尝试次数和文件摘要写回数据库，前台通过 `task_wait` 或 `task_status` 收集结果。

前台连接与后台进程具有独立生命周期。任务提交后，后台可以继续执行并保存结果，
后续对话可以查询已保存的任务。

## 持久化和恢复

控制器将任务目标、配置快照、时间预算、模型尝试和结果写入 `tasks.sqlite3`。
状态目录依次使用 `TASK_ROUTER_STATE_DIR`、`XDG_STATE_HOME/task-router/controller`，
或 `~/.local/state/task-router/controller`。

| 状态 | 处理方式 |
|---|---|
| `queued` | 等待首次执行 |
| `running` | 正在执行，接受进度查询和取消请求 |
| `retrying` | 保存检查点后选择下一兼容候选 |
| `succeeded` | 返回保存的结果 |
| `failed` | 返回失败原因和已有执行记录 |
| `cancelled` | 返回取消结果 |
| `unknown` | 保留现场，等待核实旧执行状态 |

服务错误的恢复依据结构化执行结果和已确认的终态。每次尝试都计入同一预算，
续做任务携带原始目标、先前尝试摘要和已变化的文件路径。
同一状态目录内的执行锁以及工作区占用检查用于协调写入任务。

## 模型选择与 API 切换

任务控制器根据冻结的配置选择 worker；代理则在 HTTP 请求层根据家族可用性选择上游模型。
记录中的 worker 模型代表控制器请求的型号，代理访问日志可以进一步显示请求是否被改写。

代理转发请求携带的上下文和工具信息。完整安装为选定 provider 配置本地代理地址，
同时保留原始 API 路径；对应的上游地址和配置备份位置由安装元数据记录。

## 相关说明

- [配置参考](../plugins/task-router/skills/task-router/references/routing-schema.md)
- [模型代理运维](model-proxy.md)
- [开发与测试](../CONTRIBUTING.md)
