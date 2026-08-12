# CLI 參考（繁體中文）

`obsidian-wiki --help` 是命令的權威依據。需要倉庫內容的命令會解析最近祖先目錄中的 `.obsidian-wiki/config.toml`。目前支援的範圍只包含各命令 `--help` 列出的命令與選項；未列出的介面不屬於目前產品。

## 建立與驗證倉庫

```bash
obsidian-wiki setup ./team-knowledge
cd ./team-knowledge
obsidian-wiki info --json --pretty
obsidian-wiki doctor
obsidian-wiki check
```

`setup` 的目錄參數可以省略，此時使用目前目錄。`doctor` 檢查設定與受管理資產；`check` 驗證來源、受版本管理的來源快照、manifest v2 分片、頁面、技能鏡像與引導檔案。

## 技能維護

```bash
obsidian-wiki repo sync-skills --json --pretty
obsidian-wiki repo sync-skills --apply --json --pretty
```

第一條命令是唯讀檢查；加入 `--apply` 才會從 `.skills/` 重建完整鏡像。受管理技能的升級必須先完成下述版本相容流程。

## 升級流程

所有者在分支上從獨立的框架 clone 安裝新 CLI，接著讀取知識庫受版本管理的 `requires_cli`。如果 PEP 440 約束排除新版本，解析會失敗並停止。所有者必須先審查並編輯該約束，才能執行 `repo upgrade-skills`；這個命令不會改寫 `requires_cli`。完成 `check` 與 `git diff` 後，由協作者審查完整變更，再由所有者決定是否提交。

## 交易寫入與復原

```bash
obsidian-wiki transaction begin --source sources/example.md --json --pretty
obsidian-wiki transaction validate <transaction-id> --json --pretty
obsidian-wiki transaction commit <transaction-id> --json --pretty
obsidian-wiki transaction list --json --pretty
```

代理程式只在 `begin` 回傳的 `candidate_vault` 中寫候選頁。`validate` 在提升前檢查預期知識庫；交易審查確認候選、刪除與報告後，才執行 `transaction commit`。若保留復原狀態，依回報使用 `retry`、`restore`、`discard` 或 `abort`。

## 本機近期狀態

```bash
obsidian-wiki hot status --json --pretty
obsidian-wiki hot inputs --pages 50 --operations 10 --json --pretty
obsidian-wiki hot mark-current --json --pretty
```

`wiki/hot.md` 是被忽略的衍生檢視；`wiki/index.md` 與 `wiki/log.md` 保持穩定。CLI 不執行 Git 發布，所有者在外部審查並發布變更。

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

`context` 是 `context-pack` 的別名。工作階段旁路索引命令包括 `sessions-build`、`sessions-query`、`sessions-show`、`sessions-clusters` 與 `sessions-name`。程式碼結構可用 `ast-extract PATH` 擷取。完整選項以各命令的 `--help` 為準。

`wiki-context-pack` 流程是唯讀的。常用形式為 `obsidian-wiki context-pack "topic" --budget 8000 --public-only --metadata-only --json`；省略 `--budget` 會使用預設的 8000 個估算 token。技能透過所屬倉庫解析來源路徑，筆記不需移動。輸出包含完整前置資料結構與選定摘錄。知識庫摘錄會明確標示為不受信任的參考資料：下游代理程式不得執行筆記內嵌的指令。

目前套件不含儀表板或占位實作；未來若要加入，必須另行設計。
