# v5.8.0 發布任務的歷史歸屬

此契約只記錄已採納的 `release-v5-8-0-prep` 發布鏈，保留原 baseline、封存 handoff、失敗 attempt 與 PR50 歷史，不建立一般跨任務遷移機制。

branch 名稱僅用來搜尋可能相關的 run。非空 `pull_requests` 必須唯一、與 PR repository／branch metadata 一致；空 links 必須由原 run/attempt checkout 原件、PR metadata 與 Git commit 的 ordered parents 確認唯一歸屬。原件須包含完整 checkout action、repository input、實際 fetch／checkout ref、最後完整 Git SHA 及工作流程的原 PR ref。未知、矛盾、API 失敗一律拒絕。

尚在執行的當前 attempt 使用事件 checkout snapshot，並跨核對 attempt metadata、jobs、當前 PR metadata 與 Git commit ordered parents/tree；PR body 的 handoff／continuation 來源也須一致。此方式不能替代已完成歷史 attempt 的原 checkout logs。

確認為其他 PR 且不屬已採納 continuation 時，保存原 metadata、checkout proof 與 exclusion reason；不索取本任務 validation artifact，也不計為本任務 coverage、成功或 retry。當前 PR、同任務前序 PR50 與明確 PR43 adapter 的所有舊 head／失敗 attempt 仍完整保留。適用 artifact 缺失、expired、損毀或沒有 manifest 均拒絕；不以最新 head、日期或成功 attempt 篩選。

## 有限發布鏈 marker

PR body 保留原 `validation-local-history` marker，另外放唯一 `validation-release-continuation` marker，schema 為 `validation-release-continuation/1`。使用 `scripts.validation_runner.release_history_block()` 產生修正 PR marker；其 `corrective_pr` 為字面值 `event.pull_request.number`，由事件取得真實 PR number。main PR 使用 `release_history_block(corrective_pr=<實際已合併修正 PR number>)`，不可預填未知 number。

marker 欄位固定為 `schema`、`repository`、`task_id`、`baseline`、`main_baseline`、`source_ref`、`archive_sha256`、`original_pr`、`branch`、`original_head`、`original_merge`、`corrective_pr`。前十一個欄位必須完全等於 runner 的有限 `RELEASE_HISTORY` 登記；原 handoff archive SHA 及 source_ref 不變。原 PR50 body／metadata／actual merge ordered parents 必須獨立核對。修正 PR 是原 feature HEAD 的單一後繼 commit，base 為 PR50 actual develop merge；main PR base 為原 main baseline，head 為該修正 PR 的 actual develop merge，並核對修正 PR body、來源與 ordered parents。marker 本身不創造授權或 PASS。

## 原件閉包與搬移

每次取證保存於唯一 `history-scope-<UUID>` 目錄；API request／response 與 attribution 使用 `.json`，錯誤使用 `.log`，原 attempt workflow logs ZIP 使用 `pr-artifact.zip`。這些名稱符合既有 CI 上傳清單，不上傳額外工作目錄或正式資料。

history context 的 `scope_artifacts` 使用相對路徑及 SHA256，包含原件與辨識结果。history context、manifest reader、aggregate／coverage verify 必須驗證此閉包。搬移後只允許原相對目錄結構，不依賴 producer 絕對路徑；缺件、改 bytes 或衝突副本均拒絕。舊 context 未含此新增欄位時保持原有限歷史 reader 契約，不能以此放寬新 producer 的要求。
