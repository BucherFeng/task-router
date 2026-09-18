# 模型代理

模型代理为经过本地地址的 Codex API 请求提供 GPT/GLM 家族切换、冷却状态保存
和路由日志。实现位于 `scripts/model_proxy.py`，完整安装由 `scripts/proxy_install.py` 部署。

## 完整安装

在仓库根目录运行：

```bash
./install.sh
```

安装器读取当前 Codex provider 的 API 地址，部署代理和 `task-router-proxy.service`，
验证本次服务实例的健康状态后再切换 `base_url`。原始 API 路径、密钥环境变量引用
以及其他 provider 设置得到保留；安装前配置另存备份。

可指定监听端口和跨家族目标：

```bash
./install.sh --proxy-port 8787 \
  --glm-fallback gpt-6-astra \
  --gpt-fallback glm-5.3
```

`--glm-fallback` 指定 GLM 家族不可用时使用的 GPT 模型；`--gpt-fallback` 指定
GPT 家族不可用时使用的 GLM 模型。完整安装采用 HTTP Responses 接口，并关闭
所选 provider 的 WebSocket 传输，以使请求经过本地 HTTP 代理。

## 路由行为

完整安装的预设适用于“HTTP 503 表示某个模型家族额度耗尽”的 API 服务。

| 请求情况 | 处理方式 |
|---|---|
| 请求家族正常 | 使用原始模型转发请求 |
| 收到配置中的家族故障状态码 | 冷却该家族，在响应输出前尝试另一个未冷却家族 |
| 请求家族已冷却、另一家族可用 | 直接改写为另一个家族的目标模型 |
| 429 限流 | 透传响应，供调用方退避重试 |
| 读取上游响应发生异常 | 记录异常，并对该请求实际目标家族设置短冷却 |
| 候选家族也处于冷却期 | 保留当前上游响应，不循环重试候选 |
| 请求没有可识别的模型家族 | 原样转发 |

完整安装指定家族故障码为 `503`、冷却时间为 600 秒；独立启动代理时可通过
`--fail-status` 和 `--cooldown-seconds` 配置。直接运行代理 CLI 的默认故障码为
`502,503`。上游读取异常的短冷却默认 120 秒，由 `--stream-drop-cooldown` 控制。

代理转发请求携带的上下文和工具信息。已开始输出的响应发生异常时，代理关闭
该响应并记录状态；调用方后续重试可以使用更新后的家族选择。

## 服务与文件

| 路径 | 用途 |
|---|---|
| `~/.local/share/task-router/model_proxy.py` | 完整安装部署的代理程序 |
| `~/.config/systemd/user/task-router-proxy.service` | systemd 用户服务单元 |
| `~/.local/state/task-router/original-base-url` | 保存的原始上游地址 |
| `~/.local/state/task-router/proxy-install.json` | provider、代理地址和配置备份位置 |
| `~/.local/state/task-router/proxy-state.json` | 家族冷却状态 |
| `~/.local/state/task-router/proxy-access.jsonl` | 请求路由日志 |

服务随用户 systemd 会话启动，异常退出后自动重启。冷却状态在重启后继续使用。
监听地址为本地回环地址，默认端口为 `8787`。

```bash
systemctl --user status task-router-proxy
systemctl --user restart task-router-proxy
curl -sS http://127.0.0.1:8787/health
tail -f ~/.local/state/task-router/proxy-access.jsonl
```

## 观察模型选择

访问日志记录 `model_in`、`model_out`、`upstream_status` 和 `result`。
`model_in` 是原始请求模型；非空的 `model_out` 是代理改写后的目标，为空表示沿用
原始模型。状态码用于判断最终上游响应是否成功。

| `result` | 含义 |
|---|---|
| `passthrough` | 原样转发 |
| `family_failover` | 家族故障后尝试跨类请求 |
| `rewritten_family_cooldown` | 根据冷却状态直接改写请求 |
| `rate_limit_passthrough` | 透传首次上游响应的限流状态 |
| `fallback_family_also_cooling` | 候选家族也在冷却中 |
| `upstream_stream_drop` | 读取上游响应发生异常 |

改写响应包含 `X-Task-Router-Failover` 头。代理也可在请求 `instructions` 中追加
目标模型提示，帮助解释路由；`--no-announce-model-rewrite` 用于关闭此提示。
界面中的会话型号是用户选择的模型，路由日志记录代理实际发送的目标。

## 更新与恢复连接

重新运行完整安装会更新代理程序，保留个人路由配置，并使用保存的上游地址。
迁移已有手动代理时，通过 `--upstream` 提供原始 API base URL。

需要恢复直连时，读取 `proxy-install.json` 中的 `provider` 和 `upstream`，将对应
provider 的 `base_url` 恢复为原始值，然后重新启动 Codex。确认使用直连后，可执行：

```bash
systemctl --user disable --now task-router-proxy
```

保留的配置备份可用于核对原始连接设置。开发和测试方式见 [CONTRIBUTING](../CONTRIBUTING.md)。
