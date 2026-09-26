我採納以下 PR #29 限定第六輪與合併接續授權。

一、固定任務與版本

Repository：discoveryray/zhuyin-proofreader\
Task：review-confirm-responsive\
原 PR：#29\
base／compare：develop／codex/review-confirm-responsive

B＝1593e7af65596d320b4427f1b15bb2bc0bdc949c\
H＝a38bdf1c84889c52e7281a0c23c9458e3762afa8\
D＝502414b3b38e004a6d8d9cb693cf21b65148765a\
N＝0249c44636857739a262d10bd7f4e7ed1f713d1c

先 fetch、核對直接 refs、原 PR、working tree，讀取最新 develop 規範、B 的凍結契約及第五輪採納原文。接續起點須為 base D、feature N。保留原 task、baseline、五輪歷史、全部報告、原 STOP 及14項歷史缺件；不重新搜尋已確認缺失的原件，不補造授權或 PASS。

二、限定授權

追加第六輪，累計上限六輪。允許委派實作代理及兩位獨立 reviewer、建立隔離 worktree、修改本節限定檔案、測試、commit、普通 push、更新原 PR、公開經檢查的去識別化證據，以及通過下述門檻後解除草稿、使用 Create a merge commit 合併至 develop 並驗證。

修改限於 PR29 merge 接續附約、scripts/pr29_review_gate.py、其必要測試、.github/workflows/ci.yml 及直接相關限定文件／測試。第五輪採納原文及封存決策保持原樣；新階段使用可區分的新契約與證據，舊第五輪輸入仍維持原本不允許合併的結果。

不修改一般任務的輪次或保護規則，不修改產品功能及永久安全契約。需要超出此範圍的修正時停止回報。

第六輪首次固定候選 C 交正式審查後，若再需修改受審程式、測試、設定或規範，即涉及第七輪，停止。純補證據、有具體原因的 CI 重跑及 PR evidence 更新不另計輪次，不新增空 commit。

三、實作與驗證

gate 必須精確綁定本 repository、task、PR、branch、B/H/D/N、本次有效採納來源及候選 C，並保留 fail-closed、完整報告、獨立性及 append-only 歷史。

明確支援以下不同階段：

1. 本機 C、遠端仍 N：第一輪 PASS 及必要本機驗證後允許普通 push。
2. 遠端 C、PR 仍 draft：取得新 CI 與第二輪 PASS。
3. 兩輪、CI、授權及其餘適用檢查齊備：允許解除草稿。
4. 解除草稿後重新讀取真實狀態：全部門檻通過才輸出 MERGE_PROPOSAL。
5. 實際合併後：完成該 merge SHA 的驗證才可輸出 COMPLETE。

不得要求 MERGE_PROPOSAL 才允許解除草稿，也不得以 draft 狀態輸出可立即合併的決策。gate 輸出不能代替原始證據或授權核對。

先跑受影響的 gate／workflow 測試，涵蓋缺授權、錯版本、非獨立 reviewer、缺失或失敗 CI、draft／ready 過渡、外部漂移、錯誤 merge parents/tree、錯 run/attempt 及第七輪拒絕等情境。

固定 C 後依本任務凍結契約取得必要 Windows Python 3.13.0 本機完整 unittest／pytest、GUI、runtime、compile、B→C／D→C diff 及 clean-tree 證據；PR CI 必須取得 Windows 3.12／3.13 雙版本、雙完整入口及既有清冊／GUI 核對。不得以目前一般任務的較寬政策取代。

產品程式與效能 harness 未變時，可保留 N 的效能證據並附來源與影響評估，不冒稱已在 C 重測，也不無理由重跑 benchmark。其他證據沿用須符合凍結契約。

四、CI 的限定範圍

保留 PR29 在指定 repository、head repository、PR number、branch、base D 的雙版本／雙入口檢查。

另為本次實際 merge 的 develop push 增加有限條件，可用同 repository、push refs/heads/develop、event.before=D 限定排程窗口；這僅是排程條件，不能單獨證明是 PR29。

合併後必須另核對 GitHub 的實際 PR 合併紀錄、merge SHA、ordered parents=[D,C]、tree=C，以及該 SHA 的 develop push run／attempt／checkout／完整 jobs 和必要 steps。不要把尚未產生的 C 或 merge SHA 硬寫入會構成自我引用的受審來源。

push 驗證須實際執行 Windows 3.12／3.13、full unittest／pytest、GUI、runtime、compile、push diff 與 clean tree；保留 Python 3.13.0、既有依賴及本案必要 capture 設定。不得使用 PR CI、workflow_dispatch 或假成功 context 代替。

五、兩輪審查與合併順序

第一位未參與實作的 fresh reviewer 親審 B→C 全部 cumulative diff、D 整合、接續契約及必要證據。PASS 後，核對遠端仍為 D／N，以原分支普通 push C，更新唯一原 PR，維持 draft。

取得 C 的最新適用 PR CI 後，由另一位未參與實作或第一輪的 fresh reviewer，獨立審查完整 PR、base/head/merge-base、合併接續機制，以及 CI 原始 metadata、logs、jobs、steps、tested SHA、parents/tree。

兩轮 PASS、必要 CI 及其他適用檢查齊備後，先取得 gate 的解除草稿決策，將原 PR 標為 ready。隨即重讀 PR、直接 refs、findings、保護規則及最新 CI；僅在 base=D、head=C、授權有效且完整 gate 輸出 MERGE_PROPOSAL 時，使用 expected head SHA 綁 C，以 Create a merge commit 合併。

合併後驗證實際 merge SHA、ordered parents=[D,C]、tree=C、develop HEAD，以及該 merge SHA 的 develop push CI。若 develop 隨後前進，分別回報已驗證 merge SHA 與目前 HEAD。任何必要結果失敗、缺失或不符時，不宣告 COMPLETE，不自行 revert。

六、限制與交付

我知悉真教材、真人操作、長時間使用及打包 EXE 尚未驗收；本次保留為明確揭露的後續事項，不新增為 merge 前置門檻，也不冒稱已完成。

非預期的 base／HEAD 變更須停止受影響操作；本次授權產生且已記錄的 commits、普通 push、解除草稿與預期合併不算外部漂移。

代理不可用、必要證據不足或新重大問題未釐清時，依 code／evidence／capability／contract 分類處理；不得繞過 gate 或自動新增第七輪。

不授權修改 main、直接 push develop、squash／rebase／force push、降低保護規則、tag／release、正式 DB mutation、背景 AI／額外 API 計費或其他後續功能。

保存兩輪完整報告、唯一 report_ref、角色/session、B/D/C、原始 logs、gate 決策、merge 及 post-merge 證據。新公開副本先去識別化並核對清冊與 SHA256，原件保持私人保存，不為保存 PASS 新增受審 commit。最後回報完整修改清單、驗證與限制、PR／merge／post-merge CI、歷史缺件及 working tree。
