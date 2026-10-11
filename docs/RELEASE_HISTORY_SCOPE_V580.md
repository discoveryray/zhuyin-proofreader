# v5.8.0 發布任務的歷史歸屬

此契約只記錄已採納的 `release-v5-8-0-prep` 發布鏈，保留原 baseline、封存 handoff、失敗 attempt 與 PR50 歷史，不建立一般跨任務遷移機制。

branch 名稱僅用來搜尋可能相關的 run。所有已完成歷史 attempt（包括非空 `pull_requests`）一律由原 run/attempt checkout 原件、PR metadata 與 Git commit 的 ordered parents 確認唯一歸屬。API links 必須結構有效且唯一，但只是可漂移的關聯觀測；若與唯一原 checkout 歸屬不同，保留 run／attempt 原 API links、完整原件及差異解讀，不以最新 links 改寫原 PR。原件須包含完整 checkout action、repository input、實際 fetch／checkout ref、最後完整 Git SHA 及工作流程的原 PR ref，並跨核對 repository、run/attempt/jobs、canonical SHA／雙 ordered parents／tree。未知、原件矛盾、API 失敗一律拒絕；歷史 head 不必等於 PR 最新 metadata head。

尚在執行的當前 attempt 使用事件 checkout snapshot，並跨核對 attempt metadata、jobs、當前 PR metadata 與 Git commit ordered parents/tree；PR body 的 handoff／continuation 來源也須一致。此方式不能替代已完成歷史 attempt 的原 checkout logs。

確認為其他 PR 且不屬已採納 continuation 時，保存原 metadata、checkout proof 與 exclusion reason；不索取本任務 validation artifact，也不計為本任務 coverage、成功或 retry。當前 PR、同任務前序 PR50／PR51／main52 與明確 PR43 adapter 的所有舊 head／失敗 attempt 仍完整保留。適用 artifact 缺失、expired、損毀或欠完整執行原件均拒絕；唯下列有限 pre-core 失敗契約可保留非 execution 紀錄。不以最新 head、日期或成功 attempt 篩選。

普通 PR artifact 必須保留該 run/attempt 的完整 own manifest，candidate SHA／ordered parents／tree 須與原 checkout 一致；其中更舊 nested manifests 全部照原 reader 驗證與去重，不能以 nested 原件代替缺失的 own execution。既有唯一 pinned PR43 actual push-short adapter 仍依原完整 ZIP／short receipt／actual merge／reused coverage closure 契約驗證，該 typed short 不是 pytest execution，不要求造一份 push own pytest manifest；固定 archive 與 checksum 不變。

## 有限發布鏈 marker

PR body 保留原 `validation-local-history` marker，另外放唯一 `validation-release-continuation` marker，schema 為 `validation-release-continuation/1`。使用 `scripts.validation_runner.release_history_block()` 產生修正 PR marker；其 `corrective_pr` 為字面值 `event.pull_request.number`，由事件取得真實 PR number。main PR 使用 `release_history_block(corrective_pr=<實際已合併修正 PR number>)`，不可預填未知 number。

marker 欄位固定為 `schema`、`repository`、`task_id`、`baseline`、`main_baseline`、`source_ref`、`archive_sha256`、`original_pr`、`branch`、`original_head`、`original_merge`、`corrective_pr`。前十一個欄位必須完全等於 runner 的有限 `RELEASE_HISTORY` 登記；原 handoff archive SHA 及 source_ref 不變。原 PR50 body／metadata／actual merge ordered parents 必須獨立核對。修正 PR 經連續單 parent 提交鏈回到原 feature HEAD，base 為 PR50 actual develop merge；main PR base 為原 main baseline，head 為該修正 PR 的 actual develop merge，並核對修正 PR body、來源與 ordered parents。兩入口共用逐 node SHA／parent／tree 查核，最多十六個後繼加原起點；原起點 tree 必須等於 PR50 actual merge tree，鏈原件保存在 context。缺件、循環、非單 parent、斷鏈、矛盾、超界或 API 失敗拒絕，不只使用 ancestor 布林判斷；實際 merge 的雙 ordered parents 不變。這個查詢上限不是修正額度，不重設 task／baseline／original_head／sealed handoff／失敗歷史，也不授權任何追加修正。marker 本身不創造授權或 PASS。

## 第五輪明確採納的後繼鏈

舊 `/1` marker、handoff 與 reader 保留。新 `release_history_block(successor=True)` 使用 `/2`：原十一個 source 欄位的值與意義保持（僅 schema 改為 `/2`），另外固定 `predecessor_pr=51`、`predecessor_head=ae5298f5d8fdf4888df76406b66920d7cfacd8fa`、`predecessor_merge=e744a5477aa8a7d812890982b07769eb32e1257f`、`main_pr=52`。`corrective_pr` 在新 develop 修正 PR 使用字面 `event.pull_request.number`；main52 在該 PR 真實建立後填入其 number，必須在新 develop actual merge 觸發 main52 synchronize CI 前更新。不可預填未知 merge SHA。

兩入口逐段核對原 PR50／handoff，已合併 PR51 的舊 `/1` marker、base9c／head ae／actual e744 ordered parents 與 tree。新修正 PR base 固定 e744，feature 有界單 parent 鏈須實際經 ae 回原 aab；不能重設原 anchor、baseline、source_ref 或封存 handoff。main52 的新 head 從已合併新修正 PR metadata 取得 actual merge SHA，核對 `[e744,new-feature-head]` ordered parents、tree、PR body/source，未知或矛盾拒絕。pending 修正 PR 入口只核 main52 目前仍指 e744 的身份與原 handoff，允許其原 `/1` 宣告或指向真實當前新 PR number 的 `/2` 宣告；不將 pending marker 當作未來 merge 證明。

新修正及 main 入口均搜尋 feature 與 develop 的歷史 run，仍逐 attempt 以 original checkout 決定適用歸屬。此有限链不授權額外修正，不建立通用任務遷移框架，不改变 task／修正計數／失敗歷史。

## 有限 pre-core 失敗原件

原 `/1` release main52、head e744 的失敗 attempt 可用獨立 `zhuyin-pre-core-history/1` record 保存：原 run/attempt/jobs、完整 checkout/system logs 與 canonical Git proof，`Restore validation history` 原失敗 step/log，以及 `Run core group` 和 functional consumer 明確 skipped、無執行區間，共同證明 core 未開始。原 validation artifact ZIP 的 API SHA256、完整逐檔 bytes、原 event/current-checkout-proof、原 sealed handoff／ledger 均需一致。原 event base.sha 必須等於 checkout first ordered parent 與 declared main baseline，base.ref 必須為 main。artifact 的完整 ledger.json、executions/store.json、declarations/store.json 均逐 byte 對回已驗 archive SHA 的 sealed 三份 canonical members；缺件、store identity／schema／內容矛盾拒絕。此讀取依 artifact 內當時 `/1` 事件，不以 main52 最新 `/2` body 或後續 head 改寫原失敗。

record 保存 run/attempt/PR、原件相對 hash references、原 artifact member checksum 與解讀；原錯誤 attribution、失敗輸出與全部附件 bytes 原樣保留。它沒有 execution_id／core manifest／coverage，也不能取代當前 functional success、提供 PASS 或清除真正已開始的 core failure。存在 `started`、core raw/JUnit/events 等執行原件、core step 曾開始、只有不屬該 attempt 的 nested manifest 或原件不全時，不以沒有 manifest 猜測未開始。

## 原件閉包與搬移

每次取證保存於唯一 `history-scope-<UUID>` 目錄；API request／response 與 attribution 使用 `.json`，錯誤使用 `.log`，原 attempt workflow logs ZIP 使用 `pr-artifact.zip`。這些名稱符合既有 CI 上傳清單，不上傳額外工作目錄或正式資料。

history context 的 `scope_artifacts` 使用相對路徑及 SHA256，包含原件與辨識结果。pre-core logs ZIP 與 validation artifact ZIP 分別放不同唯一 scope 目錄，檔名均為 `pr-artifact.zip`，符合既有 upload 清單。`pre_core_failures` 只引用獨立非 execution record，不加入 `executions` 或 coverage 的 `history_manifests`。history context、manifest reader、history、aggregate／coverage verify 重新核對原件閉包與 pre-core 語義；重複 identity／來源污染、缺件、改 bytes 或衝突副本均拒絕。搬移後只允許原相對目錄結構，不依賴 producer 絕對路徑。舊 context 未含此新增欄位時保持原有限歷史 reader 契約，不能以此放寬新 producer 的要求。
