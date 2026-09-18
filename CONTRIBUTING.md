# Contributing

感谢你对 task-router 的关注。

## 开发环境

```bash
git clone https://github.com/BucherFeng/task-router.git
cd task-router
python3 -B -m unittest discover -s tests -q
```

要求：

- Python 3.11+
- 自动化测试使用临时目录、模拟 Codex/systemd 命令和本地回环服务；真实部署需要 Codex CLI。
- Linux（当前唯一已验证平台；macOS/WSL 欢迎贡献验证）

## 项目结构

```text
plugins/task-router/       插件源码（分发单元）
  .codex-plugin/           manifest
  .mcp.json                MCP 服务配置
  skills/task-router/      skill 指令和路由解析器
  scripts/                 MCP 服务、后台执行器、控制器
scripts/                   安装器、模型代理（独立于插件分发）
tests/                     全部测试
docs/                      设计文档和验证记录
```

## 测试

提交前运行全部测试：

```bash
python3 -B -m unittest discover -s tests -q
```

- 路由和配置测试不需要网络。
- MCP 集成测试需要 `mcp` SDK（`pip install mcp==1.27.0`）。
- 模型代理测试绑定本地回环端口。

## 提交规范

使用 conventional commits：

```text
feat: add stream drop cooldown
fix: stop treating 429 as family exhaustion
docs: update proxy troubleshooting
refactor: extract rewrite helper
test: add 429 regression
```

## 发布流程

1. 更新 `CHANGELOG.md`。
2. 更新 `plugins/task-router/.codex-plugin/plugin.json` 的版本号。
3. 运行全部测试。
4. 测试通过后提交；创建对应版本的 tag 并推送指定 tag。
5. 在 GitHub 创建 Release 并附上 CHANGELOG 对应段落。
