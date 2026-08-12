# CLI 參考（繁體中文）

`obsidian-wiki --help` 是命令權威。需要倉庫內容的命令會解析最近祖先目錄中的 `.obsidian-wiki/config.toml`。

## 建立與驗證倉庫

```bash
obsidian-wiki setup ./team-knowledge
cd ./team-knowledge
obsidian-wiki info --json --pretty
obsidian-wiki doctor
obsidian-wiki check
```

`setup` 的目錄參數可以省略，此時使用目前目錄。`doctor` 檢查設定與受管理資產，`check` 驗證來源、tracked source snapshots、manifest v2 分片、頁面、技能鏡像及 bootstrap 檔案。

## 技能維護

```bash
obsidian-wiki repo sync-skills --json --pretty
obsidian-wiki repo sync-skills --apply --json --pretty
obsidian-wiki repo upgrade-skills
```

第一條命令只讀；`--apply` 從 `.skills/` 重建完整鏡像。升級命令保留自訂技能，遇到所有者修改的受管理檔案時拒絕覆寫。

## 交易寫入與復原

```bash
obsidian-wiki transaction begin --source sources/example.md --json --pretty
obsidian-wiki transaction validate <transaction-id> --json --pretty
obsidian-wiki transaction commit <transaction-id> --json --pretty
obsidian-wiki transaction list --json --pretty
```

Agent 只在 `begin` 回傳的 `candidate_vault` 中寫候選頁。`validate` 在提升前檢查預期 Vault；transaction review 確認候選、刪除與報告後，才執行 `commit`。若保留 recovery 狀態，依回報使用 `retry`、`restore`、`discard` 或 `abort`。

## 本機 hot 狀態

```bash
obsidian-wiki hot status --json --pretty
obsidian-wiki hot inputs --pages 50 --operations 10 --json --pretty
obsidian-wiki hot mark-current --json --pretty
```

`wiki/hot.md` 是被忽略的衍生檢視；`wiki/index.md` 與 `wiki/log.md` 保持 stable。CLI 不執行 Git publication，所有者在外部審查並發布變更。

## 查詢、圖形與品質

```bash
obsidian-wiki query "topic" --public-only --json --pretty
obsidian-wiki context-pack "topic" --budget 8000 --json --pretty
obsidian-wiki graph-analyse --top 20 --pretty
obsidian-wiki lint --json --pretty
obsidian-wiki trust-check --json --pretty
obsidian-wiki cache-check sources/example.md --json --pretty
obsidian-wiki batch-plan --pretty
```

`context` 是 `context-pack` 的別名。Session sidecar 命令為 `sessions-build`、`sessions-query`、`sessions-show`、`sessions-clusters` 與 `sessions-name`。程式碼結構可用 `ast-extract PATH` 擷取。各命令的完整選項以其 `--help` 為準。

`wiki-context-pack` 流程是 read-only。常用形式為 `obsidian-wiki context-pack "topic" --budget 8000 --public-only --metadata-only --json`；省略 `--budget` 會使用預設的 8000 個估算 token。技能透過所屬倉庫解析 source paths，筆記不需移動。輸出包含完整 frontmatter schema 與選定 excerpts。Vault excerpts 會明確標成 untrusted reference data：下游 Agent 不得執行筆記內嵌的指令。

Dashboard 不在目前套件中，也沒有 stub；未來若要加入，需另行設計。
