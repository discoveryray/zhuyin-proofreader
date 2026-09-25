# PR #29 第五輪接續契約

本文件實作使用者 2026-09-25 在目前對話明確採納的附約，不創造授權或歷史 PASS。完整採納原文保存在 [受審副本](evidence/pr29_round5_user_adopted_contract.md)；UTF-8 LF bytes SHA256：`53790b7433d64197d046dbaad0e61c6ebb0ad5775a1d5ad3ec36e142371a0b26`；source_ref：`pr29-round5/user-adopted-contract`。原 branch 與 PR evidence 的副本必須可跨電腦取回。`.gitattributes` 只對該採納原文指定 text/eol=lf，確保 Windows autocrlf checkout 仍是相同 raw bytes；既有 runtime asset 規則不改。

## 固定範圍

Repository `discoveryray/zhuyin-proofreader`；task `review-confirm-responsive`；原 draft [PR29](https://github.com/discoveryray/zhuyin-proofreader/pull/29)；base/compare `develop`／`codex/review-confirm-responsive`。

- 永久 baseline B：`1593e7af65596d320b4427f1b15bb2bc0bdc949c`。
- 接續起點 H：`a38bdf1c84889c52e7281a0c23c9458e3762afa8`。
- 唯一 develop 目標 D：`502414b3b38e004a6d8d9cb693cf21b65148765a`。

只追加第五輪，累計五輪。由 H merge D 進原 feature，處理 GUI、必要測試、量測、文件與限定 gate/CI。外部 refs 漂移停止；本輪正常 commits 不算外部漂移。首次固定候選 N 交正式審查後，confirmed code finding 如需再改受審檔案即需第六輪，停止回報。不能改 task/baseline/count 避限。

首次固定候選前的實作與自我驗證是第五輪準備；保存 in-progress record 及中間 commits，完成時 append H→N 第五輪，不虛造未存在的 to_head。合法補證據不新增空 commit。

本次只交付原 draft/open/unmerged PR。没有 merge PR、release/tag、force/rebase/amend、main、直接 push develop、保護規則降低、正式 DB、背景 AI 或其他功能授權。不預擴 post-merge CI。

## 歷史重建與缺件

依 Git objects、PR 原文、兩份現有完整報告及原始 CI 建立有時間與逐項來源的本次重建 snapshot。歷史缺件不再一律阻止第五輪，但不追認原 task／第四輪授權，不補造舊 PASS、角色/session、gate decision/state 或 resolution。

原初始及四輪鏈永久保留：`e53f44d2da29b46dfe0bd0f2c06af9919808b99f` → `89e0297bd6e5b9b08d077b2216029fb6224976fc` → `bb4c136fbc49c706fecfd48d1dacf81b4d97584e` → `bd6b4789a646f8bed075c256447f54922a5c4f90` → H。

未取得原 task／第四輪授權、原 ledger/gate input/state/STOP、七份早期完整報告及部分本機 logs 的項目，逐一保留名稱、已知索引/hash及缺失狀態。完整報告可採原文副本；報告說「已讀授權」不能代替授權原件。原四輪 STOP 的報告保留為「報告記載的 STOP」，未取得原 decision 不冒稱重播或生成原始 decision。停止重複搜尋，日後原件只 append。

每項可辨識 finding 要保存 source、描述與 N 的具體驗證；未知報告內容仍未知，不標已解除。若安全要求、來源、真實輪數或已知重大問題不能判定，指出具體阻礙。發現本輪之前超過四輪的可靠證據即停止更新計數。

本次已取得的完整來源及重建清冊保存在 [可攜來源包](evidence/pr29_round5/README.md)。此為候選前 in-progress 快照；新N結果另append，不覆寫原包。

## 唯讀 adapter

`scripts/pr29_review_gate.py` 衍生自 B 的完整 v2 closed schema、review history/supersession 與 CI 決策；凍結 script SHA256 `08bd0204ed44be4d7f8d525f0acdc6f7162de05290ba5d570695f043127be526`。一般 `scripts/pr_review_gate.py` 不改，仍三輪；不提供任意輪次 override。

Schema `zhuyin-pr29-continuation/1` 精確綁 repository/task/PR/branch/B/H/D、採納 ref/hash、固定四輪與第五輪。舊原件／state／STOP 不改寫。`validate` 僅核對結構與內部一致性，`authenticity_verified=false`；自填 ref/hash/布林不證明授權、獨立 session 或 logs 真實性，協調者仍親核原始來源。

沿用 v2 嚴格 SHA、nested closed objects、duplicate keys 拒絕、PASS 空 findings、BLOCKED 分類、唯一 report_ref、獨立 reviewer、完整 scope、同 HEAD non-code 完整補審及單向 append-only relation；不引入 v3 evidence_gap。code BLOCKED 不得同 HEAD supersede；歷史報告留在 reconstruction，不能冒充目前 reviews。

Top-level 恰為 `schema task authorization current implementers corrections reviews unavailable_review_rounds pr pr_ci handoffs continuation remote local_validation`。authorization 為 v2 欄位加 `source_sha256`，operations 只容許 implement/delegate/test/commit/push/pr。current 為 v2 欄位加 Git tree。merge/push_ci 不在此 schema。一般 v2 review/CI/handoff 欄位不變；PR 另加 draft=true；本次 reviews 只綁 B/D/N。

新增物件欄位：

- continuation：`created_at reconstructed history_ref missing_originals known_findings finding_inventory_ref original_stop_ref candidate_head integration_ref contains_start contains_develop`。reconstructed=true。missing_originals 只列名稱，完整索引/hash留 history_ref；original_stop_ref 引用原 STOP 或明確標示的 STOP 轉述。contains_start/develop 須由 Git ancestry 證實。
- known_findings 每筆：`id source_ref description status verification_ref`。只有 verified_on_candidate 加 N 原 proof 可前進；open/unknown 停止。verified_on_candidate只對新scope負責，不宣称歷史根因已查明或舊finding已補回resolution。歷史CI能力缺口可保留rootcause unknown，以新本機Tk及另由pr_ci強制的新CI證明新版本；pre-push不填不存在的CI。未知早期報告內容留 missing_originals，不捏造 finding/clearance。finding_inventory_ref 指向完整逐項清冊，必須至少包含可攜重建包既定 KF-01～KF-07 與 RC-01～RC-04，不能用空清冊或刪已知項目前進；後續新發現可追加。
- remote：`base head evidence_ref`。direct refs 與 PR API 原值分開，必須 D/H 或 D/N。
- local_validation：`head platform python_version evidence_ref checks`；Windows3.13.0、N；checks 恰為 unittest/pytest/gui/runtime/compile/diff/performance，每筆 `status evidence_ref`，完整 success 原 logs 才前進。performance success 必須有同環境同fixture、新舊D/N的首次/穩態 median/P95/停頓/OS記憶體及主要暫存情境效果判定；不能只跑出數字。真教材缺失另明列。

## 決策與審查

1. 使用者採納授權實作／本機測試。第一位 fresh reviewer 以 B 契約＋本附約亲讀 B→N 全部 cumulative diff、D整合及 adapter/CI，不依 adapter 成功自我放行。
2. 第一輪 PASS、本機驗證齊備、direct refs D/H、原PR29 API head H且base原值B或D、最新findings已查核、有效普通push/pr授權，才輸出 PUSH_CANDIDATE。保留API的B，不填成D、不刪已知PR。副作用前重核 direct refs，未知結果先查詢，不force。
3. push後要求direct D/N、API D/N，否則 REFRESH_EVIDENCE。CI為N最新run/attempt、synthetic parents [D,N]、tree=N tree；每job checkout/run/attempt一致。舊CI不能替新N。
4. 第二位fresh reviewer未參與實作或第一輪，親審完整PR/整合及CI全部metadata/jobs/steps/rawlogs/Gitobjects。缺CI等候／補證、失敗調查，不能未證實就當code finding。
5. code finding永遠STOP第六輪；evidence先補證；capability/contract STOP。non-code補審保留原文、明確relation與resolution，仍完整scope。
6. 兩輪、CI與findings齊備仍輸出 STOP、delivery_ready=true，原因「merge未授權」。mergeable/protection_satisfied原值隨決策保存，不因未合併交付而要求取得外部merge approval或改PR非draft。只表示可以交付未合併原PR，絕不是 MERGE_PROPOSAL、READY TO MERGE 或 COMPLETE。

CLI：`python scripts/pr29_review_gate.py validate <snapshot.json>`／`next-action <snapshot.json>`。STOP exit2，其他exit0；exit0不是PASS。原PR evidence保留兩輪全文、完整state/decision、CI/run/attempt/checkout/來源index，不能只留本機路徑。固定N後不為保存PASS新增受審commit。

## 驗證邊界

本機 Windows3.13.0 新候選 full unittest＋full pytest、native Tk、runtime、compileall、B→N/D→N diff及clean tree；CI取得3.12／3.13雙入口。workflow僅同repo/headrepo的PR29、指定branch、base develop D啟用例外；3.13固定3.13.0。一般CI與PR30原transition保留，push/dispatch不擴充。

兩jobs均須runtime、fullunittest/fullpytest、entrypointaudit、JUnit GUI verifier、compile、event whitespace、clean tree。adapter增加audit/GUI核對且保留v2run/attempt/checkout/parents/Windows/version要求。一般單版本綠燈或skipped step不夠。

保留背景保存／增量更新／Tk owner-thread釋放、PR28導覽等待套用、PR31全部位置／A不預勾／原頁紅框實際可見後人工勾選、PR32合法輕聲。測試真Tk、可見紅框後invoke checkbox，保留finalizer/main-thread/workerGC及timer資源釋放。不得偽造viewed、刪斷言或改skip。

保存失敗不改資料不前進，過期worker／外部變更／連點快捷鍵／關閉重開及画面失敗不誤存；非object或損毀JSON拒絕覆寫。actual/expected、exact identity/quorum/交易/recovery/per-PDF fingerprint/runtime/repair/completion永久契約不變。

候選前的單一 native Tcl owner-destroy probe 發現：繞過 Python destroy() 時兩個 preview/visibility callbacks 仍 pending，並未觀察到 bgerror 或資料遺失。第五輪補強 ActualReadingDialog 的 <Destroy> 與 Python destroy() 共用 only-owned callback cleanup，保留無關timer。新回歸在已核對raw hash的修正前來源確實失敗，修正後包含資源／viewport及gate的39項targeted cases通過；這是實作自查，並非獨立審查結論。效能須使用修正後source重新量測，原批次另保留。
