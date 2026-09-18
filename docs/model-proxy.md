# Model proxy

The model proxy provides GPT/GLM family failover, persistent cooldowns, and routing
logs for requests sent through its local endpoint. The implementation is in
`scripts/model_proxy.py`; `scripts/proxy_install.py` handles complete deployment.

## Complete installation

Run from the repository root:

```bash
./install.sh
```

The installer reads the current Codex provider URL, deploys the proxy and
`task-router-proxy.service`, and verifies the new service instance before switching
`base_url`. It preserves the API path, credential environment-variable references,
and unrelated provider settings, and saves a configuration backup.

Set the listening port and cross-family targets:

```bash
./install.sh --proxy-port 8787 \
  --glm-fallback gpt-6-astra \
  --gpt-fallback glm-5.3
```

`--glm-fallback` selects the GPT model used when GLM is unavailable.
`--gpt-fallback` selects the GLM model used when GPT is unavailable. Complete
installation uses HTTP Responses requests and disables the selected provider's
WebSocket transport so requests pass through the local HTTP proxy.

## Routing behavior

The complete-install preset targets providers where HTTP 503 signals exhausted
quota for an entire model family.

| Condition | Action |
|---|---|
| Requested family is available | Forward using the requested model |
| Configured family-failure status received | Cool the family and attempt the other eligible family before sending the response |
| Requested family is cooling and the other is available | Rewrite directly to the other family's target |
| HTTP 429 | Forward the response for client backoff |
| Upstream read error | Record the error and apply a short cooldown to the selected family |
| Fallback family is also cooling | Return the current upstream response instead of cycling candidates |
| No recognized model family | Forward the request unchanged |

Complete installation sets the family-failure code to `503` and the cooldown to
600 seconds. Standalone proxy arguments `--fail-status` and `--cooldown-seconds`
control these values; the standalone CLI defaults to `502,503`. Upstream read
errors use a 120-second cooldown, configurable with `--stream-drop-cooldown`.

Context and tool information in the request are forwarded. If reading a response
fails after output has begun, the proxy closes that response and records the error.
A subsequent client retry can use the updated family selection.

## Service and files

| Path | Purpose |
|---|---|
| `~/.local/share/task-router/model_proxy.py` | Installed proxy executable |
| `~/.config/systemd/user/task-router-proxy.service` | systemd user service unit |
| `~/.local/state/task-router/original-base-url` | Saved upstream URL |
| `~/.local/state/task-router/proxy-install.json` | Provider, local endpoint, and configuration backup metadata |
| `~/.local/state/task-router/proxy-state.json` | Family cooldown state |
| `~/.local/state/task-router/proxy-access.jsonl` | Request routing log |

The service starts with the user's systemd session and restarts after abnormal
exits. Cooldowns persist across restarts. The listener binds to loopback, using
port `8787` by default.

```bash
systemctl --user status task-router-proxy
systemctl --user restart task-router-proxy
curl -sS http://127.0.0.1:8787/health
tail -f ~/.local/state/task-router/proxy-access.jsonl
```

## Observing model selection

Access records include `model_in`, `model_out`, `upstream_status`, and `result`.
`model_in` is the requested model. A non-null `model_out` is the rewritten target;
null means the original model was retained. The status code describes the final
upstream response.

| `result` | Meaning |
|---|---|
| `passthrough` | Forwarded without rewriting |
| `family_failover` | Attempted a cross-family request after a family failure |
| `rewritten_family_cooldown` | Rewritten according to existing cooldown state |
| `rate_limit_passthrough` | Forwarded a rate-limit response from the initial upstream request |
| `fallback_family_also_cooling` | The fallback family is also cooling |
| `upstream_stream_drop` | Reading the upstream response failed |

Rewritten responses include `X-Task-Router-Failover`. The proxy can also append a
target-model note to request `instructions`; use `--no-announce-model-rewrite` to
disable that note. The Codex session label remains the user's selection, while
proxy logs record the target sent upstream.

## Upgrades and direct connections

Rerun complete installation to update the proxy while preserving user routing
settings and the saved upstream URL. Use `--upstream` with the original API base
URL when migrating a manually configured proxy.

To restore a direct connection, read `provider` and `upstream` from
`proxy-install.json`, restore that provider's `base_url`, and restart Codex.
After confirming the direct connection, stop the proxy with:

```bash
systemctl --user disable --now task-router-proxy
```

Use the saved configuration backup to compare the original connection settings.
See [Contributing](../CONTRIBUTING.md) for development and testing commands.
