# Changelog

## [1.0.0] - 2026-09-18

### Added

- Codex conversation tools: `task_submit`, `task_status`, `task_wait`, `task_cancel`, `task_resume`, `router_diagnose`
- Task-type model routing: discussion prefers GPT, coding prefers GLM, with configurable candidate pools
- Family-level failover: any GPT or GLM 503 automatically switches to the other family
- Local model proxy (systemd user service): forwards request context while selecting an alternate model family
- 429 rate-limit passthrough: Codex handles retry with built-in backoff
- Mid-stream drop convergence: short family cooldown so Codex retry lands on the healthy family
- Model routing announcement and access metadata identify the requested upstream target
- Background task controller: SQLite persistence, bounded retries, file-change tracking, safe continuation
- Installer: config migration, backup, rollback, concurrent-install lock
- Automated coverage for routing, protocol, MCP entry, complete installation, rollback, and proxy failover
