# Changelog

## [1.0.0] - 2026-09-18

### Added

- Codex conversation tools: `task_submit`, `task_status`, `task_wait`, `task_cancel`, `task_resume`, `router_diagnose`
- Task-type model routing: discussion prefers GPT, coding prefers GLM, with configurable candidate pools
- Family-level failover: any GPT or GLM 503 automatically switches to the other family
- Local model proxy (systemd user service): transparent request rewriting with full context preservation
- 429 rate-limit passthrough: Codex handles retry with built-in backoff
- Mid-stream drop convergence: short family cooldown so Codex retry lands on the healthy family
- Model identity announcement: after failover, the model truthfully reports its actual identity
- Background task controller: SQLite persistence, bounded retries, file-change tracking, safe continuation
- Installer: config migration, backup, rollback, concurrent-install lock
- 114 tests covering routing, protocol, MCP entry, installer, and proxy
