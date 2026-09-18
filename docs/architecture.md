# Architecture

Task Router combines a Codex plugin, a background task controller, and a local
model proxy. The plugin exposes conversation tools, the controller owns execution
state, and the proxy handles configured model-family failover at the HTTP layer.

## Source layout

| Path | Responsibility |
|---|---|
| `plugins/task-router/.codex-plugin/plugin.json` | Plugin metadata |
| `plugins/task-router/.mcp.json` | MCP service launcher |
| `plugins/task-router/skills/task-router/SKILL.md` | Classification, submission, and result-handling workflow |
| `plugins/task-router/skills/task-router/scripts/` | Policy validation, migration, and model selection |
| `plugins/task-router/scripts/mcp_server.py` | Task-management tools |
| `plugins/task-router/scripts/background_worker.py` | Detached executor process |
| `plugins/task-router/scripts/task_router_runtime/` | Persistence, execution control, and Codex protocol adapter |
| `scripts/install.py`, `scripts/proxy_install.py` | Installation, configuration preservation, service deployment, and rollback |
| `scripts/model_proxy.py` | Local HTTP proxy, family cooldowns, and request metadata |
| `tests/` | Unit, protocol, and isolated installation tests |

The plugin package includes its runtime. Complete installation also deploys the
model proxy and a systemd user service.

## Task execution

1. Codex interprets the user's request and calls `task_submit` with a task type,
   project directory, goal, and read/write authorization.
2. `service.py` validates the request, deduplicates its stable request key, and
   stores the task and a routing-policy snapshot in SQLite.
3. A background process invokes `controller.py`, which selects a model using the
   saved task, role, and candidate order.
4. `codex_adapter.py` starts an owned Codex app-server session with an explicit
   model, reasoning effort, and sandbox policy.
5. Results, attempts, and file digests are persisted. Codex collects them through
   `task_wait` or `task_status`.

The foreground connection and executor have independent lifecycles. Submitted
work can continue after a frontend disconnect, and a later conversation can query
the saved task.

## Persistence and recovery

The controller stores task inputs, policy snapshots, deadlines, attempts, and
results in `tasks.sqlite3`. The state directory comes from `TASK_ROUTER_STATE_DIR`,
then `$XDG_STATE_HOME/task-router/controller`, or the default
`~/.local/state/task-router/controller`.

| State | Handling |
|---|---|
| `queued` | Await initial execution |
| `running` | Accept status queries and cancellation requests while executing |
| `retrying` | Continue from the saved checkpoint using the next compatible candidate |
| `succeeded` | Return the saved result |
| `failed` | Return the failure reason and execution history |
| `cancelled` | Return the cancellation result |
| `unknown` | Preserve the task until the previous execution can be reconciled |

Recovery decisions use structured execution results and confirmed terminal states.
Every attempt counts against the same budget. Continuation prompts include the
original goal, prior attempt summaries, and observed changed paths. Execution locks
and workspace checks coordinate work managed by the same state directory.

## Model selection and API failover

The controller selects workers from a frozen policy. The proxy independently
selects upstream targets according to family availability. A task's recorded model
is the controller's requested model; proxy access logs show any subsequent rewrite.

The proxy forwards context and tool information present in the request. Complete
installation switches the selected provider to a loopback endpoint while preserving
its API path. Installation metadata records the upstream URL and configuration backup.

## Related documentation

- [Configuration reference](../plugins/task-router/skills/task-router/references/routing-schema.md)
- [Proxy operations](model-proxy.md)
- [Development and testing](../CONTRIBUTING.md)
