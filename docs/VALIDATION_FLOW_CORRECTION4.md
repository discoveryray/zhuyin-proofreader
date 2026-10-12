# validation-flow-reduction 第 4 輪限定授權

本記錄只摘要 2026-10-03 使用者對原 task／PR #41 的明確接續授權；不建立新授權，不追認舊 PASS，不刪除前三輪或重設計數。

| 綁定 | 唯一適用值 |
|---|---|
| task / repository | `validation-flow-reduction` / `discoveryray/zhuyin-proofreader` |
| baseline / PR base | `457707b4c4109c1b10a0da76d8f8a884aca10341` |
| 第 3 輪 to HEAD / 第 4 輪 starting HEAD | `a0e647952ae5d973ea30130264294eee4e6982fa` |
| branch / PR | `chore/validation-flow-reduction` / `41` |
| 追加 / 最終上限 | 一輪；`4/4`，其他 task 仍為三輪 |
| 原始 UTF-8/BOM 授權 bytes SHA256 | `29b2869d5e6584bab8efbf022f697c8ee7331b7e1241ef0c18df7f4a647ec7fc` |

原文保留於 ignored task evidence `tmp/pr-review-automation/validation-flow-reduction/correction4-tcl-wiring/user-authorization.txt`；`authorization-record.json` 保存來源及 hash。協調者須讀原文並確認未撤銷；gate 只能核對 saved file hash 與固定 identity，不能認證授權、人類身分或結果真實性。

範圍僅同 Python 安裝的 process/job-local Tcl/Tk 路徑、資源與版本核對、core 前的 60 秒 hosted fd preflight、對應回歸／直接規範與 task-bound gate 例外。不得修改產品、分類器、正式資料、依賴、capture、retry allowlist 或 PR39／PR40 歷史；不新增跨 HEAD／跨環境 core adoption 或增量框架。

第 4 輪保存一次新 corrective commit；新 HEAD 必須重新取得兩輪完整 scope 的獨立審查。第一位 reviewer 無 confirmed code blocker 才更新原 PR41；新候選只執行一次正式 core 全組及一次 GUI 全組，使用當次 collection，舊 core 結果保留原 SHA 不採納為新結果。Hosted early preflight 使用正式 interpreter／job environment／fd capture，完成原 root／Combobox／Spinbox 並保存實際載入 Tcl/Tk 版本、路徑及 raw evidence；失敗不啟動 core／GUI。原 GUI 自身 preflight 保留。

本次 preflight／正式測試若失敗，保存原始原因並 STOP，不自動重開 CI；第 4 輪 commit 後若仍需改受審來源，依 `4/4` STOP，不進第 5 輪。合法 non-code evidence supplement 仍保留原 HEAD／計數與 append-only relation。只有必要 coverage、CI、兩輪正式 PASS、保護規則及原 merge 授權全部成立，才 merge commit；實際 merge 的 push 仍執行原非視窗短驗證，同安裝環境接線一致，不執行真實 Tk 或全套。
