# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.5.0] - 2026-09-17

### Added

- Codex conversation tools (`task_submit`, `task_status`, `task_wait`,
  `task_cancel`, `task_resume`, `router_diagnose`) exposed via MCP.
- Background task controller with SQLite persistence, bounded retries, and
  file-change tracking for safe continuation.
- Local model proxy with family-aware failover (503 = family quota exhausted).
- Proxy access log (`proxy-access.jsonl`) with model rewrite details.
- Model identity announcement on failover (can be disabled).
- 429 rate-limit passthrough (no family cooldown; Codex handles backoff).
- Mid-stream drop detection with short family cooldown for Codex retry convergence.
- Installer with config migration, backup, rollback, and concurrent-install lock.
- 113 tests covering routing, protocol, MCP entry, installer, and proxy.

### Changed

- Plugin runtime is fully self-contained (no source repository dependency).
- Review and read-code default to `glm-5.3` per user preference.

## [0.4.0] - 2026-09-14

### Added

- Schema v2 configuration: tasks -> roles -> profile/model pools.
- Strict validation, v1 migration, and role-based selection.
- Standalone controller prototype with durable execution.
- Seven-model validation (GLM and GPT families).

## [0.3.0] - 2026-09-14

### Added

- Dispatcher architecture: discussion to GPT, code to GLM.
- Failover chains and cooldown configuration.

## [0.1.0] - 2026-09-11

### Added

- Initial task-router skill with JSON routing policy and install script.

[Unreleased]: https://github.com/fengbochao/task-router/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/fengbochao/task-router/releases/tag/v0.5.0
[0.4.0]: https://github.com/fengbochao/task-router/releases/tag/v0.4.0
[0.3.0]: https://github.com/fengbochao/task-router/releases/tag/v0.3.0
[0.1.0]: https://github.com/fengbochao/task-router/releases/tag/v0.1.0
