# CLI 參考（繁體中文）

`llmwikiops --help` 是命令的權威依據。需要倉庫內容的命令會解析最近祖先目錄中的 `.obsidian-wiki/config.toml`。目前支援的範圍只包含各命令 `--help` 列出的命令與選項；未列出的介面不屬於目前產品。

## 建立與驗證倉庫

```bash
llmwikiops setup ./team-knowledge
cd ./team-knowledge
llmwikiops info --json --pretty
llmwikiops doctor
llmwikiops check
```

`setup` 的目錄參數可以省略，此時使用目前目錄。`doctor` 檢查設定與受管理資產；`check` 驗證來源、受版本管理的來源快照、manifest v2 分片、頁面、技能鏡像與引導檔案。

## 技能維護

```bash
llmwikiops repo sync-skills --json --pretty
llmwikiops repo sync-skills --apply --json --pretty
```

第一條命令是唯讀檢查；加入 `--apply` 才會從 `.skills/` 重建完整鏡像。受管理技能的升級必須先完成下述版本相容流程。

## 升級流程

所有者在分支上從獨立的框架 clone 安裝新 CLI，接著讀取知識庫受版本管理的 `requires_cli`。如果 PEP 440 約束排除新版本，解析會失敗並停止。所有者必須先審查並編輯該約束，才能執行 `repo upgrade-skills`；這個命令不會改寫 `requires_cli`。完成 `check` 與 `git diff` 後，由協作者審查完整變更，再由所有者決定是否提交。

## 交易寫入與復原

```bash
llmwikiops transaction begin --source sources/example.md --json --pretty
llmwikiops transaction validate <transaction-id> --json --pretty
llmwikiops transaction commit <transaction-id> --json --pretty
llmwikiops transaction list --json --pretty
```

代理程式只在 `begin` 回傳的 `candidate_vault` 中寫候選頁。`validate` 在提升前檢查預期知識庫；交易審查確認候選、刪除與報告後，才執行 `transaction commit`。成功提交會提升頁面、更新 manifest 分片，並在最後附加一個規範區塊到受版本管理的權威操作日誌 `wiki/log.md`；JSON commit 與 retry 輸出會回傳 `log_path`。若保留復原狀態，依回報使用 `retry`、`restore`、`discard` 或 `abort`。

缺少凍結來源雜湊的舊版保留交易仍可列出、還原、中止或捨棄，但不可 commit 或 retry；請重新開始交易以綁定目前來源內容。

## Manifest 衝突調解

```bash
llmwikiops manifest resolve-conflict --keep-live [--json] [--pretty]
```

所有者檢查 live 分片與復原證據後，可以明確保留 live 版本。清理在中斷後可重入，且只移除記錄身份與內容仍相符的固定工件。若兩次嘗試之間 live 分片發生變更，自動復原會停止，直到所有者重新執行命令確認目前的 live 版本。

## 受版本管理的近期檢視

```bash
llmwikiops hot status --json --pretty
llmwikiops hot inputs --pages 50 --operations 10 --json --pretty
llmwikiops hot mark-current --json --pretty
```

`wiki/hot.md` 是受版本管理的衍生語義檢視。`hot status` 僅回報新鮮度，不得移除這個檔案；成功 commit 或 retry 後的語義刷新會成為可審查的工作樹差異。CLI 不執行 Git 發布；所有者在外部審查變更、解決 `log.md` 與 `hot.md` 中的一般 Git 衝突，並決定是否發布。

## 查詢、圖形與品質

```bash
llmwikiops query "topic" --public-only --json --pretty
llmwikiops context-pack "topic" --budget 8000 --json --pretty
llmwikiops graph-analyse --top 20 --pretty
llmwikiops lint --json --pretty
llmwikiops trust-check --json --pretty
llmwikiops cache-check sources/example.md --json --pretty
llmwikiops batch-plan --pretty
```

`context` 是 `context-pack` 的別名。工作階段旁路索引命令包括 `sessions-build`、`sessions-query`、`sessions-show`、`sessions-clusters` 與 `sessions-name`。程式碼結構可用 `ast-extract PATH` 擷取。完整選項以各命令的 `--help` 為準。

`wiki-context-pack` 流程是唯讀的。常用形式為 `llmwikiops context-pack "topic" --budget 8000 --public-only --metadata-only --json`；省略 `--budget` 會使用預設的 8000 個估算 token。技能透過所屬倉庫解析來源路徑，筆記不需移動。輸出包含完整前置資料結構與選定摘錄。知識庫摘錄會明確標示為不受信任的參考資料：下游代理程式不得執行筆記內嵌的指令。

目前套件不含儀表板或占位實作；未來若要加入，必須另行設計。
