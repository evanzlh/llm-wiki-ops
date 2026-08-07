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
- 事务化写入、便于合并的操作日志以及可重建的 hot 状态
- 可在任意 CI 平台运行、无需 LLM 的确定性校验

## 安装

唯一受支持的安装方式，是从本地 clone 以非 editable 方式构建安装：

```bash
git clone https://github.com/evanzlh/obsidian-wiki.git
cd obsidian-wiki
uv tool install .
```

安装后的 CLI 不依赖 clone 目录继续存在。升级时，在 clone 目录中拉取更新，然后运行 `uv tool install --force .`。

## 创建便携式团队知识库

```bash
obsidian-wiki setup --portable ./team-knowledge
cd ./team-knowledge
obsidian-wiki doctor
obsidian-wiki repo upgrade-skills  # 安装新版框架 CLI 后运行
```

在 Obsidian 中将 `team-knowledge/wiki/` 作为 Vault 打开。每位协作者都从自己的框架 clone 用 uv tool 安装 CLI；clone 知识库仓库后运行 `obsidian-wiki doctor`，再通过偏好的 Agent 使用受版本管理的仓库内 Skills。知识库仓库不包含 `.venv` 或内置 CLI 副本。

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
