# task-router

[![CI](https://github.com/BucherFeng/task-router/actions/workflows/ci.yml/badge.svg)](https://github.com/BucherFeng/task-router/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Route Codex tasks to GPT and GLM model pools, run work in durable background processes,
and use a local API proxy for configured model-family failover.

## Installation

### Complete setup

```bash
git clone https://github.com/BucherFeng/task-router.git
cd task-router
./install.sh
```

The installer deploys the Codex plugin, background executor, local model proxy,
systemd user service, and routing configuration. Open a new Codex conversation
after installation to load the plugin tools.

Run the installer as your normal user with an active `systemctl --user` session.
It reads your existing Codex provider configuration, preserves the API path and
credential environment-variable references, and checks the proxy before switching
the provider endpoint. The complete-install preset targets Responses API providers
where HTTP 503 signals that a GPT or GLM model family's quota is exhausted.

Choose fallback models and a loopback port during setup:

```bash
./install.sh --proxy-port 8787 --glm-fallback gpt-6-astra --gpt-fallback glm-5.3
```

When migrating an existing manually deployed proxy, pass the original API base URL
with `--upstream https://api.example.com/v1`. Run the installer again to upgrade;
your routing configuration is preserved. Installation metadata records the upstream
URL and the configuration backup location.

### Start using Task Router

Open a new Codex conversation and ask for work normally:

> Explain this project's architecture.

> Fix the login bug in src/auth.py.

> Review the latest changes.

Codex uses the plugin's workflow and tools to decide when to submit a routed task.
Add "use task-router" to explicitly request this workflow.

For installation diagnostics:

```bash
codex plugin list --marketplace fengbochao-plugins --json
systemctl --user status task-router-proxy
```

## Capabilities

### Conversation tools

| Tool | Purpose |
|---|---|
| `task_submit` | Submit and start a background task using its configured model pool |
| `task_status` | Inspect a task, list recent work, and page through long results |
| `task_wait` | Wait for progress or completion within a bounded interval |
| `task_cancel` | Request cancellation of an active task |
| `task_resume` | Continue a saved, recoverable task |
| `router_diagnose` | Inspect configuration sources, model assignments, and selection decisions |

### Task-based model selection

| Task | Preferred model | Fallback order |
|---|---|---|
| General discussion | gpt-5.5 | gpt-5.6-sol, then glm-5.3 |
| Planning and deep analysis | gpt-6-astra | gpt-5.6-sol, then glm-5.3 |
| Code review | glm-5.3 | glm-5.2, then glm-5.3-flash |
| Implementation, refactoring, and tests | glm-5.3 | glm-5.2, then glm-5.3-flash |
| Code reading and small edits | glm-5.3 | glm-5.2, then glm-5.3-flash |

Edit model IDs, enabled profiles, and candidate order in
`~/.config/task-router/routing.json` to match your provider and preferences.
See the [configuration reference](plugins/task-router/skills/task-router/references/routing-schema.md).

### Model-family failover

The local proxy sits between Codex and the upstream API:

- Configured family-failure status codes trigger a cooldown and one attempt with
  the other family before the response is sent downstream.
- HTTP 429 responses are forwarded for the client to handle with backoff.
- Upstream read errors trigger a short cooldown that subsequent requests can use.
- Cooldown state persists across proxy restarts.
- Rewritten responses include `X-Task-Router-Failover`, and access logs record the
  selected upstream target.

Service diagnostics:

```bash
systemctl --user status task-router-proxy
tail -f ~/.local/state/task-router/proxy-access.jsonl
cat ~/.local/state/task-router/proxy-state.json
```

### Durable background execution

SQLite stores task inputs, execution attempts, and results. Stable request keys
deduplicate submissions; resuming completed work returns the saved result.
File digests help subsequent workers inspect partial changes and continue remaining
work. Attempt counts and deadlines are managed by the controller and survive restarts.

## Requirements

| Component | Requirement |
|---|---|
| Python | 3.11+ |
| Codex CLI | Plugin, MCP, and app-server support; exercised with 0.154.0 and 0.155.0 |
| Operating system | Linux with an active systemd user session |
| API | A configured Responses API provider with the selected model IDs and valid credentials |

If the MCP SDK is missing, the launcher prepares a private environment with
`mcp==1.27.0`. Initial dependency setup requires network access.

## Configuration

User settings live in `~/.config/task-router/routing.json` and are preserved during
upgrades. Configure model IDs, enabled profiles, reasoning effort, candidate order,
and task-to-role mappings. New submissions read the updated configuration; existing
tasks retain the policy saved at submission.

## Documentation

- [Configuration reference](plugins/task-router/skills/task-router/references/routing-schema.md)
- [Model proxy operations](docs/model-proxy.md)
- [Architecture](docs/architecture.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

## License

[MIT](LICENSE)
