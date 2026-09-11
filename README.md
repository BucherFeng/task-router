# task-router

Codex 调度员式任务路由插件：主线程模型只做分类和派发。讨论、方案、review
委托给 GPT 系模型；写代码、批量改写、长上下文分析委托给 GLM 系模型；琐碎
交互由主线程直接回答。每个模型配有降级链（例如 gpt-6 不可用时依次尝试
gpt-5.6 → gpt-5.5 → glm-5.3），额度耗尽或服务 503 时自动切换。

## 安装

克隆本仓库后运行一条命令：

```bash
git clone <本仓库地址> && cd task-router-repo && ./install.sh
```

安装脚本会自动完成三件事：把插件复制到 `~/plugins/task-router/`、把插件条
目合并进你的个人 marketplace（已存在则替换同名条目，不影响其他插件）、执
行 `codex plugin add task-router@personal`。完成后**新开一个 Codex 线程**
即可生效。

不想用 Git 的话，把整个仓库打成 zip 发给对方，解压后同样执行
`./install.sh` 即可。

## 前提

- 本机装有 Codex CLI。
- `routing.json` 中的 GPT 模型 id（`gpt-5.5`/`gpt-5.6`/`gpt-6`）目前是占
  位 id。请把它们和 `routes`、`failover.chains` 里的引用换成你环境里
  `/model` 中的实际 id。替换前，讨论类任务会因 GPT 不可用沿降级链自动落
  到 GLM 上，功能不受影响。
- GLM 模型 id 为 `glm-5.3-flash`（主线程）和 `glm-5.3`（编码委托）。如果
  模型 id 不同，编辑
  `plugins/task-router/skills/task-router/routing.json`：把 `models` 目录和
  `routes` 里的 id 换成你环境里的实际 id 即可，规则写法见
  `plugins/task-router/skills/task-router/references/routing-schema.md`。
- 插件本身不包含 API 地址和密钥，需要你自己的 provider 配置可用。

## 备用安装方式（不合并个人 marketplace）

也可以把本仓库注册为独立 marketplace：

```bash
codex plugin marketplace add <本仓库路径>/.agents/plugins/marketplace.json
codex plugin add task-router@fengbochao-plugins
```

## 维护者升级流程

1. 修改工作目录 `~/plugins/task-router/`（本地测试用）。
2. 同步进仓库并提交：

   ```bash
   cp -R ~/plugins/task-router/. ~/task-router-repo/plugins/task-router/
   cd ~/task-router-repo && git commit -am "update task-router"
   git push
   ```

3. 接收方拉取最新仓库后重新运行 `./install.sh` 即可更新。
