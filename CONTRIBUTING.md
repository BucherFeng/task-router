# Contributing

Contributions should keep installation, routing, and recovery behavior reproducible.

## Development setup

```bash
git clone https://github.com/BucherFeng/task-router.git
cd task-router
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install mcp==1.27.0
python3 -B -m unittest discover -s tests -q
```

Requirements:

- Python 3.11+
- Automated tests use temporary directories, fake Codex/systemd commands, and local
  loopback servers. A real deployment requires Codex CLI.
- Linux with local socket access for the test suite.

## Repository layout

```text
plugins/task-router/       Self-contained plugin distribution
  .codex-plugin/           manifest
  .mcp.json                MCP service configuration
  skills/task-router/      Workflow instructions and routing resolver
  scripts/                 MCP tools, background executor, and controller
scripts/                   Complete installer and standalone model proxy
tests/                     Automated tests and isolated fixtures
docs/                      Current architecture and proxy operations
```

## Testing

Run the full suite before submitting a change:

```bash
python3 -B -m unittest discover -s tests -q
```

- Routing and configuration tests run locally.
- MCP integration tests use the SDK installed during development setup.
- Proxy tests bind local loopback ports and use a fake upstream.

The packaged task runner is available for developer diagnostics:

```bash
python3 plugins/task-router/scripts/run_task.py --help
```

Tests import runtime code directly from the plugin package, matching installed
execution paths. Credentials and live model requests are not needed by the suite.

## Commit messages

Use Conventional Commits and describe the concrete behavior or technical change:

```text
feat: add stream drop cooldown
fix: stop treating 429 as family exhaustion
docs: update proxy troubleshooting
refactor: simplify installer rollback state
test: add 429 regression
```

Keep subjects focused on the change. Describe installation, routing, recovery,
configuration, or documentation updates rather than the editing process.

## Releases

1. Update `CHANGELOG.md`, the plugin manifest, and installer version consistently.
2. Update version fixtures and run the complete test suite.
3. Validate repository text, decoded strings, and documentation links.
4. Commit, create a new version tag, and push the branch and tag after checks pass.
5. Create a GitHub Release with the matching changelog entry, full source archives,
   and SHA-256 checksums. Preserve previously published tags and release artifacts.
