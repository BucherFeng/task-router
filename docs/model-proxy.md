# 模型代理（家族级故障切换）

日期：2026-09-17。对应组件：scripts/model_proxy.py，systemd 用户服务
task-router-proxy.service。

## 解决什么问题

账号的额度按家族计：GPT 类与 GLM 类各自共享额度。任何一个模型返回 503，
即代表该类额度耗尽，同类其他模型也不会可用。Codex 主模型自身的 API 请求
无法由插件拦截——插件运行在主模型回合内部。代理位于 Codex 与上游 API 之间，
在网络层拦截请求，主模型 503 时自动改写为另一类模型重发，对 Codex 完全透明。

## 工作方式

Codex 的 base_url 指向 127.0.0.1:8787（本地代理）。代理读取请求 JSON 中的
model 字段并判断家族：

- 该家族处于冷却期（此前发生过家族级失败）：直接改写为另一类的首选模型
  （GLM 耗尽 -> gpt-6-astra；GPT 耗尽 -> glm-5.3），不再请求已耗尽的类。
- 否则原样转发。上游返回 502/503 时：将该家族加入冷却（默认 600 秒），
  改写为另一类首选并重发一次。
- 429 默认按临时限流处理：原样透传，不改写模型、不冷却家族，由 Codex
  自带的退避重试恢复。这与账号语义一致——额度耗尽表现为 503，而 429 是
  短时速率限制。如需恢复旧行为，可通过 --fail-status 显式加入 429。
- 重发也失败：将另一类也加入冷却，并把第二次的错误原样返回给 Codex。
- 两类同时冷却：所有请求直接得到明确错误，等待冷却结束或额度恢复。

冷却状态持久化在 ~/.local/state/task-router/proxy-state.json，代理重启不会
丢失。成功重试不会冷却备选家族——只有仍然失败的家族才进入冷却。

非 JSON 请求体、无法识别家族的模型、以及 /v1/models 等无 model 字段的请求
按原样透传，不做改写。流式（SSE）响应在收到错误状态且未开始输出前可以
安全重试；一旦开始输出则原样透传，中断无法透明重放，这与所有代理方案一致。

### 流式中断的收敛

上游已经开始输出后连接中断时，代理无法重放该响应，但会把该家族加入
短冷却（默认 120 秒，--stream-drop-cooldown 可调），并在访问日志中记录
result=upstream_stream_drop。Codex 对流断开有内建重试；重试请求再次经过
代理时会被直接改写到另一类，因此大多数中断对用户表现为一次自动恢复，
而不是反复撞同一类已故障的模型。

### 模型身份提示

发生改写时，代理会在请求 instructions 末尾追加一条事实说明：请求的原始
模型不可用、本次响应实际由哪个模型生成、被问及时应如实回答。这样模型
自述与真实路由一致。--no-announce-model-rewrite 可关闭该行为（仍会改写
模型，但不追加提示）。

Codex 界面左上/会话标签显示的仍是用户选择的模型，这是 Codex 会话元数据，
代理无法修改；真实路由以 proxy-access.jsonl 的 model_in/model_out 为准。

## 当前部署

- 服务：systemd 用户单元 task-router-proxy.service（崩溃自动重启、开机自启）。
- 监听：127.0.0.1:8787；上游：https://api.infiniplan.xyz。
- 降级方向：GLM 耗尽 -> gpt-6-astra；GPT 耗尽 -> glm-5.3。
- Codex config.toml 的 base_url 已切换为 http://127.0.0.1:8787/v1，原配置
  备份于 config.toml.before-proxy-20260917。

## 常用操作

~~~bash
systemctl --user status task-router-proxy      # 查看服务状态
systemctl --user restart task-router-proxy     # 重启（冷却状态保留）
curl -sS http://127.0.0.1:8787/health          # 健康检查
cat ~/.local/state/task-router/proxy-state.json  # 查看当前冷却
tail -f ~/.local/state/task-router/proxy-access.jsonl  # 观察每次请求的模型改写
~~~

回退直连：将 config.toml 的 base_url 改回 https://api.infiniplan.xyz/v1，
然后 systemctl --user disable --now task-router-proxy（可选）。

## 验证记录

- 13 项本地 socket 测试通过（假上游）：按类切换、冷却跳过与过期、双类
  耗尽透传、SSE 流式保序透传、非 JSON 透传、未知家族不改写、状态持久化、
  健康检查；另有成功重试不冷却备选家族、访问日志记录改写且不含凭据的
  回归测试。
- 真实验证：/v1/models 透传返回真实模型列表；glm-5.3-flash 真实请求经代理
  正常返回；codex exec 端到端（含流式）经代理完成并返回 PROXY-E2E-OK。
- 未验证：上游真实 503 的在线注入（需要真实耗尽场景）；macOS/WSL；Codex
  CLI 升级后的协议兼容性。

2026-09-18 修正：GPT 额度耗尽后切换到 GLM 的首波请求曾因并发触发 429，
旧逻辑将 429 误判为家族额度耗尽，导致两类同时冷却、全部请求 503。已改为
429 透传不冷却，并清除了当时的错误冷却状态。修正后实测：gpt-6 请求经代理
自动由 glm-5.3 完成，返回 200 及 X-Task-Router-Failover 头。

注意：503 被视为家族额度耗尽是当前账号的运维结论；若未来供应商改为按单
模型计费，需重新评估家族级冷却语义。
