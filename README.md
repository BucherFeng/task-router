# task-router

Codex 多模型任务路由插件，当前版本 v0.4.0。

主 agent 按任务选择角色，Python 解析器从角色的候选模型池中选择目标，再由主
agent 发起委托。默认偏好 GPT 处理讨论、方案与 review，GLM 处理代码阅读、
修改和测试。模型分工可配置，主模型不会被插件自动切换。

## 安装或升级

需要 Python 3.11+ 和支持 plugin 命令的 Codex CLI。安装器适用于 Linux/macOS
和 WSL；本轮在 Linux 验证。已有可用的 API/provider 配置由使用者自行提供。

克隆到任意目录后，在仓库内执行：

~~~bash
./install.sh
~~~

也可下载完整仓库压缩包，解压后运行 bash install.sh。

- 已有本插件的个人 marketplace 安装：检查原来源，暂存新插件，备份旧版本，
  再调用 Codex 安装命令。不会手工修改已有 marketplace。
- 新使用者：通过 Codex CLI 注册这个仓库为独立 marketplace，然后安装
  task-router@fengbochao-plugins。请保留仓库所在路径，用于后续更新。
- 用户配置独立保存在 ~/.config/task-router/routing.json，支持 XDG_CONFIG_HOME。
  已有有效 v2 配置保持原样；v1 配置备份后迁移。外部配置不存在时会迁移旧
  插件目录的配置，新安装才使用包内默认配置。
- 升级失败会尝试恢复配置及旧插件目录，并报告保留文件的位置。Codex 自身的
  注册/缓存状态不是文件系统事务的一部分；首次注册成功而安装失败时，可能
  留下 marketplace 条目，需要根据错误提示核对。
- 同时只允许一个安装过程。不修改密钥、主模型或其他插件配置。

成功后开一个新 Codex 线程加载新版 skill。解析器每次读取外部配置，之后调整
角色池无需重装；修改 skill 或 Python 脚本则需要重装。若设置了
TASK_ROUTER_CONFIG，安装器会提示显式处理这份配置，避免迁移到错误位置。

## 使用与配置

显式要求“用 task-router 处理”可以加载工作流；也允许模型自动选择此 skill。
skill 是模型遵循的指令，不能保证每次对话都强制路由。

从本仓库运行诊断或查看选择：

~~~bash
python3 plugins/task-router/skills/task-router/scripts/route.py --doctor
python3 plugins/task-router/skills/task-router/scripts/route.py --task edit --explain
python3 plugins/task-router/skills/task-router/scripts/route.py --list
~~~

所有结果包含实际配置路径、来源和摘要。退出码 0 表示成功或诊断报告生成；
2 表示输入/配置错误；3 表示没有可用候选或尝试次数达到上限。
doctor 返回 attention 时应查看 notes，即使命令退出码为 0。

配置分三层：

1. tasks 把任务映射到角色。
2. roles 列出有序的 profile 候选。
3. profiles 定义实际 model_id、是否启用及 reasoning_effort。

例如把 discussion-main 的 model_id 改成其他型号，所有引用它的角色都会
使用新型号，无须修改 skill。迁移旧配置会保留旧分工；采用新版默认分工应先
生成单独的候选文件并比较，不通过升级静默覆盖。

详细字段与迁移命令见
[配置文档](plugins/task-router/skills/task-router/references/routing-schema.md)。

## 默认分工

| 任务 | 角色 | 首选候选 |
|---|---|---|
| 普通讨论 | discussion | gpt-5.4 |
| 复杂方案、深度分析 | reasoning | gpt-6-astra |
| 代码审查 | review | gpt-5.5 |
| 编码、重构、测试 | coding | glm-5.3 |
| 读代码、机械小改 | coding-light | glm-5.3-flash |
| 无后续工作的简短回应 | 本地处理 | 当前主模型 |

这些是默认配置偏好，不是模型效果排名或可用性保证。目录之外的新模型不会
自动启用。当前主模型实际型号、认证方式和子 agent 可调用的型号需在使用者
自己的环境核实。配置中的 dispatcher.model_id 只是期望值。

## 能力与边界

v0.4 实现严格配置校验、v1 迁移、角色池、推理参数筛选、可选上下文/工具能力
检查、路由解释和安装备份恢复。支持宿主传入当前会话的能力快照、已失败模型
和尝试次数；模型目录仅作为声明，不能证明余额或实际连通性。

子任务失败后由主 agent 查询下一候选并重试。max_attempts 的检查依赖主
agent 传入真实次数；没有后台重试执行器、持久化冷却、余额监控或自动
HTTP 故障拦截。超时后先确认旧 worker 停止并检查已有改动，再决定是否续做。

主模型自身不可用时，插件无法执行恢复逻辑。未知候选、认证错误和限流应
如实报告，不能把固定回复或查表成功当作全链路通过。

本机真实验证结果及未通过项见 [v0.4 验证记录](docs/v0.4-validation.md)。

## 维护与测试

发布仓库是唯一维护源；~/plugins/task-router 和 Codex 插件缓存是安装产物。
不要通过修改缓存维护代码。日常更新仓库后重新运行安装脚本；发布时维护真实
版本号，开发迭代按 plugin-creator 的 cachebuster 流程重装。

~~~bash
python3 -B -m unittest discover -s tests -v
bash -n install.sh
~~~

测试覆盖无效配置、筛选、迁移、安装保留、并发安装拒绝和模拟 Codex 安装
失败恢复；使用临时目录和假的 Codex 命令，不访问真实账户或修改其配置。
后续任务状态与故障恢复设计见 [升级方案](docs/upgrade-plan.md)。
