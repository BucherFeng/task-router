# 独立任务执行原型

更新：v0.5 已把执行器接入插件的对话工具，普通使用者不需要下面的终端命令。
本文保留为开发者说明，日常操作请看 [README](../README.md)。

该原型从命令行先接收并保存任务，再通过独立的 Codex app-server 进程调用模型。
选择候选、累计尝试、记录结果和决定重试由 Python 程序执行，不需要主 agent
先调用 skill。现有 v0.4 插件仍是原来的使用方式；此原型尚未接到 MCP 或桌面入口。

## 当前能力

- 明确指定任务类型，复用现有角色模型池，冻结任务的路由配置和目录声明。
- SQLite 保存原始目标、工作目录、执行尝试、模型/档位、会话/轮次 ID、结果和文件摘要。
- 对已确认结束的可重试服务错误选择下一候选，真实次数由程序累计，重启不重置。
- 执行前后保存有界文件摘要；重试携带此前结果和变化路径，要求继续剩余工作。
- 支持用户提供独立的验证命令；模型声称完成但验证失败时，不记作成功。
- 独立进程查询、取消或恢复任务；同一请求键不重复创建相同任务，已成功的任务不重跑。
- 同一状态目录只执行一个任务，未确认的任务会阻止该目录管理的重叠工作区再次执行。
- 停在 retrying 检查点的任务保留工作区占用，直到恢复完成或明确取消，防止其他受管任务混入改动。

原型暂不调用模型自动分类，必须提供 --task。这让第一版可以验证执行和恢复，
避免把分类错误、模型执行错误和状态错误混在一起。未知任务类型和 inline 类型
不发起模型请求。分类/规划 worker 可在后续接入相同状态机制。

## 环境与入口

需要 Python 3.11+、支持 app-server 的 Codex CLI 和当前用户可用的模型 provider。
本轮实测环境为 Linux、codex-cli 0.154.0；不承诺其他 CLI 版本或系统兼容。
执行接口仍为实验协议，升级 Codex 后应先跑兼容性测试。

从仓库根目录运行：

```bash
python3 scripts/run_task.py run \
  --task edit \
  --cwd /absolute/path/to/project \
  --prompt "修复指定函数，并保持现有测试不变" \
  --write \
  --timeout 300 \
  --request-key my-first-task \
  --verify-json '["python3", "-B", "-m", "unittest", "-v"]'
```

--write 明确授权工作目录内的本地修改。讨论、审查、读代码类型只允许只读；
编辑类型缺少 --write 会报错。--verify-json 是使用者明确提供的 argv 数组，
不经过 shell 解释，也不接受 worker 输出作为待执行命令。验证命令以当前用户
权限运行，不在模型 sandbox 内；应只填写自己希望执行的本地验证命令。

先提交后执行，可以跨程序启动继续：

```bash
python3 scripts/run_task.py submit --task read-code \
  --cwd /absolute/path/to/project --prompt "说明这个模块的输入和输出"
python3 scripts/run_task.py status TASK_ID
python3 scripts/run_task.py resume TASK_ID
python3 scripts/run_task.py cancel TASK_ID
```

程序输出 JSON/JSONL。run 会先输出 task_id 和数据库位置，再输出最终结果；
submit 只保存任务。status 不给 task_id 时列出最近任务。使用自定义状态位置时，
把 --state-dir 放在子命令前，并在后续命令中使用同一个目录。

```bash
python3 scripts/run_task.py --state-dir /private/path/controller-state status
```

默认状态位置为 $XDG_STATE_HOME/task-router/controller，未设置时使用
~/.local/state/task-router/controller。状态目录必须为私有目录（0700），且不能
位于 worker 工作目录内部。不同 --state-dir 不共享锁或状态；不要用多个状态
目录并发管理同一个工作区。状态库包含任务和结果，不应随插件公开分享。

## 恢复行为

| 状态 | resume 的行为 |
|---|---|
| queued | 首次启动模型 |
| retrying | 使用保存的配置、次数和文件状态选择下一候选 |
| succeeded | 返回已保存结果，不重新执行 |
| failed / cancelled | 返回终态，不自动清空预算或重启 |
| running / unknown | 标为 unknown 并阻止重放；需要先检查旧执行和副作用 |

--one-attempt 让程序在一次执行后返回。例如可重试故障会停在 retrying，随后
用另一个进程 resume 继续，便于验证持久化。正常 run 不加此参数，会在已确认
结束且预算允许时自动选择下一候选。

总时间预算从首次执行开始计算，覆盖模型内部重试和后续尝试；暂停和重启不会
自动延长。任务预算耗尽会失败，不无限轮询候选。真实服务内部 willRetry=true
时，程序等待其终态或请求中断，不同时启动第二个 worker。

取消当前任务会写入取消请求；执行进程轮询后请求 turn/interrupt。中断请求
已发送不等于已经停止。缺少最终事件或仍有未结束工具时，记录 unknown，
不会冒险重试。原型没有提供自动清除 unknown 或任意重放外部操作的命令。

程序每次启动自己拥有的 app-server，不操作用户的共享 daemon。每次尝试创建
新的 worker 会话；恢复的是任务目标和文件进度，不是承诺完整恢复某个已有
桌面线程或跨模型隐藏状态。

## 权限和验证边界

worker 使用 read-only 或 workspace-write sandbox；工具网络访问关闭，扩展
权限请求不会被自动批准。需要额外权限或交互时，原型拒绝并报告，而不是
为了完成任务使用 danger-full-access。多 agent 功能对受管 app-server 关闭。

原型限定于本地任务，worker 被要求不推送、部署或操作外部系统。已有 MCP
等运行时工具的外部副作用仍取决于宿主能力，不能把提示词当成完整的权限隔离。
任务输出为模型结果，只有显式验证命令通过时 completion_verified 才为 true。

文件摘要有数量和大小上限，跳过 .git、依赖目录等；incomplete 标记表示不完整。
摘要帮助续做，不提供完整文件回滚，也不能证明外部操作只执行一次。执行状态
未知、权限错误、验证失败等不会被当成“换模型就可以解决”的额度故障。

退出码：0 表示提交/查询成功或执行成功；2 表示输入/配置问题；3 表示执行失败；
4 表示执行结果未知；75 表示 --one-attempt 留下可恢复检查点；130 表示取消。

## 本轮验证

全仓库 `python3 -B -m unittest discover -s tests -q`：77 项测试通过，其中原有
v0.4 测试 41 项，新增执行、协议及恢复测试 36 项。独立审查发现的检查点
工作区占用问题已修复，并增加回归测试。

自动化覆盖包括协议成功、内部重试后成功、结构化 503、认证错误、启动确认
丢失、断线、未结束工具、中断和取消、权限请求拒绝、损坏协议、状态写入失败
清理，以及 SQLite 持久化、重复提交、配置冻结、次数/时间上限和独立验证。

独立 CLI 进程测试验证了部分修改后重启续做、成功后再次 resume 不重跑、
另一进程请求取消，以及强制结束 controller 后不会重放未确认的任务。

真实实验记录见 [原型验证结果](controller-prototype-validation.json)：

1. 先 submit 保存任务，再用新进程 resume。程序直接调用 glm-5.3/high，
   修复隔离目录中的 invoice.py；程序独立运行 4 项 unittest，全部通过。
2. 注入一次明确标记的模拟 503，并留下 prepared 一行。首次程序退出后，
   新进程 resume 选择真实 glm-5.2/high，补上 completed。独立验证确认每行
   恰好出现一次；再次 resume 没有增加执行次数。

第二项的失败是注入的，没有声称上游真实返回过 503。两项测试均未修改业务
仓库文件或操作外部系统。运行产物在临时目录，JSON 记录保留了必要证据。

## 下一阶段

先检查更多真实失败和跨模型上下文场景，再考虑自动分类、持久化冷却和交互式
权限接入。之后将同一执行核心通过插件/MCP 暴露出去。仅当用户请求先进入
此程序时，才能覆盖“主模型在调用插件之前就失效”的窗口。
