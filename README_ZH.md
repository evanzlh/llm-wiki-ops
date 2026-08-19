# LLMWikiOps

> 面向持久 Markdown 知识库的 LLM 运维框架。

LLMWikiOps 在 [evanzlh/llm-wiki-ops](https://github.com/evanzlh/llm-wiki-ops) 独立维护。它保留了 [Ar9av/obsidian-wiki](https://github.com/Ar9av/obsidian-wiki) 的历史和 MIT 许可证，Fork 基点为提交 [`5ef66b6`](https://github.com/Ar9av/obsidian-wiki/commit/5ef66b6bec8b26bab6594ac37fb4d8371469fbab)。详见 [Fork 说明](docs/fork.md)。

[English](README.md) | [简体中文](README_ZH.md)

一个便携、Git 原生的框架，用于将受版本管理的来源资料编译为由 AI 维护的 Obsidian 知识图谱。

## 产品模型

每个知识库采用一种仓库布局、一份仓库相对配置和一棵受版本管理的技能树。来源资料、来源快照、manifest v2 分片、生成页面、权威的 `wiki/log.md`、派生的 `wiki/hot.md` 和 Agent 指令会一起经过分支与 Pull Request。只有本地事务与恢复状态会被忽略。

## 安装

支持的主机系统为 Linux 或 macOS。仓库与 vault 的安全边界依赖 POSIX
描述符相对文件系统操作；不受支持的平台会失败并停止。

从本地框架 clone 安装非 editable 构建：

```bash
git clone https://github.com/evanzlh/llm-wiki-ops.git
cd llm-wiki-ops
uv tool install --link-mode copy .
```

这是从本地 clone 完成的全新安装；本项目不支持从软件包索引安装。安装后的命令不依赖 clone 目录继续存在。从框架 clone 强制重装属于下述受审查的升级与开发流程。详情见[安装说明](docs/installation.md)。

在 wiki 内部，仓库感知命令使用基于当前工作目录的最近祖先发现。在 wiki 外部，请使用显式安装的全局 Adapter，并在每条仓库感知命令上强制提供 `-C` / `--repo`。每次调用都必须提供仓库根目录；不存在默认或记忆的 wiki。

```bash
llmwikiops agent install-adapter --agent codex
llmwikiops -C /absolute/path/to/wiki info --json
llmwikiops -C /absolute/path/to/wiki query --mode find --term "topic" --json
llmwikiops -C /absolute/path/to/wiki transaction list --json
```

安装 CLI 不会安装 Adapter，也不会在主目录中写入 Agent 集成文件。显式的 `agent install-adapter` 命令只为一个 Agent 安装一个可选的全局路由器；详见[安装说明](docs/installation.md#install-the-external-wiki-adapter)。

## 创建知识库仓库

```bash
llmwikiops setup ./team-knowledge
cd ./team-knowledge
llmwikiops doctor
llmwikiops check
```

在 Obsidian 中打开 `wiki/`。命令解析最近祖先目录中的 `.llmwikiops/config.toml`，因此可以从仓库根目录或其子目录运行。初始化还会安装规范 `.skills/` 技能树及完整的 Agent 镜像。

**协议不兼容。** 旧的 `.obsidian-wiki/` 状态不会检测、读取、迁移或删除。仅有该目录的仓库视为未初始化；两个目录同时存在时，只有 `.llmwikiops/` 是权威。请显式运行 `llmwikiops setup` 并审查新文件；不要手工复制旧状态。

Setup 不会初始化 Git。协作前，所有者需要初始化知识库仓库，审查、暂存并提交 scaffold；详见[安装说明](docs/installation.md#create-a-repository)。

## 安全协作

来源快照先由所有者审查，并在 `transaction begin` 之前纳入版本管理。此后 Agent 通过本地事务写入。候选内容提升前必须通过校验；失败时保留恢复状态。成功提交会提升候选页面、更新 manifest 分片，最后向受版本管理的权威操作日志 `wiki/log.md` 追加一个规范区块；JSON 输出返回其 `log_path`。事务绝不会修改受版本管理的来源快照。受版本管理的 `wiki/hot.md` 是派生语义视图：`hot status` 只读，绝不能删除它。CLI 不会提交、推送或创建 Pull Request：仓库所有者审查工作树 diff，解决 `log.md` 与 `hot.md` 中的普通 Git 冲突，并在外部完成 Git 发布。

Manifest 分片更新通过仓库本地锁和有界恢复日志执行。同一工作树中的所有写入者都必须遵守协议，通过仓库事务接口及其锁写入。检测到身份或内容不同的变更时，框架会保留冲突，而不会将其删除。POSIX 没有可移植的、按 inode 条件删除名称的系统调用，因此同一用户下绕过锁的进程仍可能在最终名称检查与清理系统调用之间制造竞态；这个不受支持的竞态不具备内核级 CAS 保证。

`transaction begin` 会冻结所选来源的哈希；如果来源在候选内容准备期间发生变化，commit 会失败并要求重新开始事务。若 manifest 冲突留下固定恢复日志，所有者应先检查 live 分片和工作树 diff，再通过 `llmwikiops manifest resolve-conflict --keep-live` 明确保留当前版本。该命令只删除身份和内容仍与日志记录一致的恢复工件。若清理中断后 live 分片又发生变化，自动恢复会停止，直到所有者重新运行该命令以确认当前 live 版本。

请采用这套两步 CLI 与仓库升级协议。所有者先创建分支，从框架 clone 安装新 CLI，再读取受版本管理的 `requires_cli` 约束。如果该 PEP 440 约束尚未包含新版本，仓库命令会失败并停止；因此所有者必须先显式审查并编辑 `.llmwikiops/config.toml`，让约束接受过渡版本，再运行维护命令。`repo upgrade-skills` 不会改写 `requires_cli`。完成校验与差异检查后，由协作者审查完整变更，所有者决定是否提交。

```bash
git switch -c upgrade-llmwikiops
cd /path/to/llm-wiki-ops
uv tool install --force --reinstall --link-mode copy .
cd /path/to/team-knowledge
${EDITOR:?} .llmwikiops/config.toml
llmwikiops repo upgrade-skills
llmwikiops doctor
llmwikiops check
git diff
git commit -m "Upgrade LLMWikiOps"
```

当前产品界面仅包含本文与 `docs/` 所述的仓库工作流。Dashboard 有意不提供；未来若要加入，必须另行设计与实现，本版本不包含占位实现。

查询必须先探索语法：请在查询前运行 `llmwikiops query --describe --json`。Agent 执行 `llmwikiops query --mode find --term "注意力机制" --json --pretty`；query-language/v1 固定英文外壳，同时允许任何语言的运算元。详见 [CLI 参考](docs/cli.md)。

## 文档

- [文档索引](docs/README.md)
- [安装](docs/installation.md)
- [配置](docs/configuration.md)
- [架构](docs/architecture.md)
- [CLI 参考](docs/cli.md)
- [Agent 协议](docs/agents.md)
- [Skills](docs/skills.md)
- [贡献指南](docs/contributing.md)
- [Fork 关系](docs/fork.md)

## 许可证

原始工作由 Ar9av 及其贡献者完成。这个 Fork 保留了上游 Git 历史与 MIT 许可证；详见 [LICENSE](LICENSE)。
