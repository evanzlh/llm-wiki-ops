# obsidian-wiki

> 这是 [Ar9av/obsidian-wiki](https://github.com/Ar9av/obsidian-wiki) 的独立维护 Fork，基于提交 [`5ef66b6`](https://github.com/Ar9av/obsidian-wiki/commit/5ef66b6bec8b26bab6594ac37fb4d8371469fbab)。详见 [Fork 说明](docs/fork.md)。

[English](README.md) | [简体中文](README_ZH.md)

一个便携、Git 原生的框架，用于将受版本管理的来源资料编译为由 AI 维护的 Obsidian 知识图谱。

## 产品模型

每个知识库采用一种仓库布局、一份仓库相对配置和一棵受版本管理的技能树。来源资料、来源快照、manifest v2 分片、生成页面和 Agent 指令会一起经过分支与 Pull Request。事务工作区和 `wiki/hot.md` 只保留在本地并被忽略。

## 安装

支持的主机系统为 Linux 或 macOS。仓库与 vault 的安全边界依赖 POSIX
描述符相对文件系统操作；不受支持的平台会失败并停止。

从本地框架 clone 安装非 editable 构建：

```bash
git clone https://github.com/evanzlh/obsidian-wiki.git
cd obsidian-wiki
uv tool install --link-mode copy .
```

这是从本地 clone 完成的全新安装；本项目不支持从软件包索引安装。安装后的命令不依赖 clone 目录继续存在。从框架 clone 强制重装属于下述受审查的升级与开发流程。详情见[安装说明](docs/installation.md)。

## 创建知识库仓库

```bash
obsidian-wiki setup ./team-knowledge
cd ./team-knowledge
obsidian-wiki doctor
obsidian-wiki check
```

在 Obsidian 中打开 `wiki/`。命令解析最近祖先目录中的 `.obsidian-wiki/config.toml`，因此可以从仓库根目录或其子目录运行。初始化还会安装规范 `.skills/` 技能树及完整的 Agent 镜像。

Setup 不会初始化 Git。协作前，所有者需要初始化知识库仓库，审查、暂存并提交 scaffold；详见[安装说明](docs/installation.md#create-a-repository)。

## 安全协作

来源快照先由所有者审查，并在 `transaction begin` 之前纳入版本管理。此后 Agent 通过本地事务写入。候选内容提升前必须通过校验；失败时保留恢复状态。成功提交会提升候选页面、更新 manifest 分片并写入操作记录，同时保持 `wiki/index.md` 与 `wiki/log.md` 稳定。事务绝不会修改受版本管理的来源快照。CLI 不会提交、推送或创建 Pull Request：仓库所有者审查工作树 diff，并在外部完成 Git 发布。

Manifest 分片更新通过仓库本地锁和有界恢复日志执行。同一工作树中的所有写入者都必须遵守协议，通过仓库事务接口及其锁写入。检测到身份或内容不同的变更时，框架会保留冲突，而不会将其删除。POSIX 没有可移植的、按 inode 条件删除名称的系统调用，因此同一用户下绕过锁的进程仍可能在最终名称检查与清理系统调用之间制造竞态；这个不受支持的竞态不具备内核级 CAS 保证。

`transaction begin` 会冻结所选来源的哈希；如果来源在候选内容准备期间发生变化，commit 会失败并要求重新开始事务。若 manifest 冲突留下固定恢复日志，所有者应先检查 live 分片和工作树 diff，再通过 `obsidian-wiki manifest resolve-conflict --keep-live` 明确保留当前版本。该命令只删除身份和内容仍与日志记录一致的恢复工件。若清理中断后 live 分片又发生变化，自动恢复会停止，直到所有者重新运行该命令以确认当前 live 版本。

请采用这套两步 CLI 与仓库升级协议。所有者先创建分支，从框架 clone 安装新 CLI，再读取受版本管理的 `requires_cli` 约束。如果该 PEP 440 约束尚未包含新版本，仓库命令会失败并停止；因此所有者必须先显式审查并编辑 `.obsidian-wiki/config.toml`，让约束接受过渡版本，再运行维护命令。`repo upgrade-skills` 不会改写 `requires_cli`。完成校验与差异检查后，由协作者审查完整变更，所有者决定是否提交。

```bash
git switch -c upgrade-obsidian-wiki
cd /path/to/obsidian-wiki
uv tool install --force --reinstall --link-mode copy .
cd /path/to/team-knowledge
${EDITOR:?} .obsidian-wiki/config.toml
obsidian-wiki repo upgrade-skills
obsidian-wiki doctor
obsidian-wiki check
git diff
git commit -m "Upgrade obsidian-wiki"
```

当前产品界面仅包含本文与 `docs/` 所述的仓库工作流。Dashboard 有意不提供；未来若要加入，必须另行设计与实现，本版本不包含占位实现。

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
