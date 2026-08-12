# obsidian-wiki

> 这是 [Ar9av/obsidian-wiki](https://github.com/Ar9av/obsidian-wiki) 的独立维护 Fork，基于提交 [`5ef66b6bec8b26bab6594ac37fb4d8371469fbab`](https://github.com/Ar9av/obsidian-wiki/commit/5ef66b6bec8b26bab6594ac37fb4d8371469fbab)。本项目不是上游官方版本，也不会持续跟踪上游后续变更。详见 [Fork 关系与动机](docs/fork.md)。

[English](README.md) | [简体中文](README_ZH.md)

一个基于 Skills 的框架，用于将来源资料编译为由 AI 维护的 Obsidian 知识图谱。

## 为什么维护这个 Fork

这个 Fork 聚焦于采用 Git 原生工作流的多人知识库：来源资料和编译后的 Vault 位于同一个仓库中；协作者在分支上工作；生成的变更通过 Pull Request 接受审查。

## Fork 新特性

- 采用仓库相对配置的便携式仓库模式
- 仓库内受版本管理的 Skills 与多 Agent 引导文件
- 稳定的仓库相对 Source ID 与分片 Manifest 状态
- 可恢复的本地事务、不可变操作页面以及可重建的本地 hot 状态
- 先 dry-run、配有按字节回滚快照的旧知识库迁移
- 跨 clone 稳定的来源字节与便于合并的并行分支
- 可在任意 CI 平台运行、无需 LLM 的确定性校验

## 安装

唯一受支持的安装方式，是从本地 clone 以非 editable 方式构建安装：

```bash
git clone https://github.com/evanzlh/obsidian-wiki.git
cd obsidian-wiki
uv tool install --link-mode copy .
```

安装后的 CLI 不依赖 clone 目录继续存在。升级时，在 clone 目录中拉取更新，然后运行 `uv tool install --force --reinstall --link-mode copy .`。

## 创建便携式团队知识库

```bash
obsidian-wiki setup --portable ./team-knowledge
cd ./team-knowledge
obsidian-wiki doctor
obsidian-wiki check
```

在 Obsidian 中将 `team-knowledge/wiki/` 作为 Vault 打开。每位协作者都从自己的框架 clone 用 uv tool 安装 CLI；clone 知识库仓库后运行 `obsidian-wiki doctor`，再通过偏好的 Agent 使用受版本管理的仓库内 Skills。知识库仓库不包含 `.venv` 或内置 CLI 副本。

便携仓库中的 `.skills/` 是规范技能树。六个完整的普通文件镜像让受支持的
Agent 获得相同的技能描述与资源。请先检查漂移，再显式重建镜像、校验并审查
受版本管理的结果：

```bash
obsidian-wiki repo sync-skills --json --pretty
obsidian-wiki repo sync-skills --apply --json --pretty
obsidian-wiki check --json --pretty
git diff -- .skills .claude/skills .cursor/skills .windsurf/skills .agents/skills .pi/skills .kiro/skills
```

第一条命令是只读的。只编辑 `.skills/`，不要直接编辑 Agent 镜像。

升级框架时，请遵循这个两步便携式 CLI 升级协议。首先在分支上安装新版
CLI，并审慎修改受版本管理的 `.obsidian-wiki/config.toml` 中的
`requires_cli`，将其设为接受当前已安装版本、经过审查的 PEP 440 约束。
然后刷新受管理文件、校验仓库并审查全部变更：

```bash
git switch -c upgrade-portable-cli
# 修改 .obsidian-wiki/config.toml，使 requires_cli 接受已安装的版本。
obsidian-wiki repo upgrade-skills
obsidian-wiki check
git diff
```

每位协作者都必须安装满足仓库更新后约束的 CLI 版本。
`repo upgrade-skills` 不会绕过兼容性检查，也不会自动改写
`requires_cli`；它会升级受管理的内置技能、保留自定义技能，并拒绝漂移或
未知的旧版改动。请通过常规 Pull Request 工作流提交审查后的配置与受管理
文件 diff。

便携模式下，Agent 会在被忽略的本地事务工作区中暂存写入，再将已审查的候选内容提升到工作树。普通写入会保持 `wiki/index.md` 与 `wiki/log.md` 稳定，将 `wiki/hot.md` 保持为被忽略的本地状态，并追加一个不可变操作页面。事务命令不会提交或推送；请审查 Git diff，并通过常规分支和 Pull Request 工作流发布。详见 [架构](docs/architecture.md#portable-write-lifecycle)与 [CLI 事务参考](docs/cli.md#portable-transactions-and-local-hot-state)。

## 迁移现有仓库

如果旧 Vault 和来源资料已经是同一仓库中的两个独立目录，请先运行只读分析：

```bash
obsidian-wiki repo migrate --root . --vault wiki --sources sources
```

应用之前，必须确认外围 Git 根目录等于 `--root`、完整旧基线已经提交且工作树干净。然后再运行 dry-run 输出的准确 apply 命令：

```bash
obsidian-wiki repo migrate --root . --vault wiki --sources sources --apply
```

迁移不会导入仓库外的来源，也不会发布 Git 变更。详见 [dry-run、blocker 与回滚参考](docs/cli.md#legacy-to-portable-migration)。

## 个人模式

通过源码安装的 CLI 仍保留既有的个人工作流：

```bash
obsidian-wiki setup --vault ~/brain
```

## 文档

- [安装](docs/installation.md)
- [便携式配置](docs/configuration.md)
- [Agent 兼容性](docs/agents.md)
- [CLI 参考](docs/cli.md)
- [架构](docs/architecture.md)
- [Skills](docs/skills.md)
- [Fork 关系与动机](docs/fork.md)

## 上游项目与许可证

原始工作由 Ar9av 及其贡献者完成。这个 Fork 保留了上游 Git 历史与 MIT 许可证。归属与兼容性详情见 [docs/fork.md](docs/fork.md)。
