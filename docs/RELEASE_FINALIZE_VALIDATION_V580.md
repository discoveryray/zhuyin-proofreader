# 永久當前證據政策與 v5.8.0 接續

2026-10-12 使用者採納 `validation-flow/current-evidence/1`，永久取消「CI歷史自動重播作為測試、合併、發布前置門檻」，立即適用未結案任務及後續一般工作。此文件沿用原發布說明位置，取代限定分支→main特例；正常PR→develop、PR→main、develop/main push及本機必要驗證都適用。

正常入口不呼叫restore-history、不要求舊CI artifact或hand-off/ledger/continuation新版閉包，coverage/gate/沿用也不間接要求。歷史工具只留查閱，不修parser/新增PR-run例外，舊原件/schema/失敗/判定保留原義，不補造或改標PASS。

## 現行必要條件與入口

Windows Python3.13.0、requirements-ci-lock.txt、完整有效非視窗pytest、inventory/JUnit verify-active、runtime/compile/diff/clean、repo/base/head/tested SHA/parents/tree、環境/命令/退出碼/raw附件及Python3.13 summary全部必要。當次失敗/漏跑/skip/錯SHA/缺附件仍拒絕，不删功能測試/放寬產品斷言/造空coverage，所有產品資料安全契約不變，自動Tk仍取消。

新版coverage為zhuyin-validation-coverage/3，short為zhuyin-merge-short/3，gate v4明示current政策；舊snapshots原義保持。Develop actualmerge push只核對對應PR必要當次原件、orderedparents/tree與短檢查，不遞迴歷史；main push另跑完整非視窗pytest/verify-active。

CI真實context中的新入口為 `validation_evidence.py current-coverage --evidence-root tmp --output tmp/current-coverage.json`、`verify-coverage tmp/current-coverage.json`、develop push `post-merge --current-only --evidence-root tmp/validation-evidence`。入口不授權本機自填CI身分/全集；詳見[TESTING](../TESTING.md)與[政策](VALIDATION_POLICY.md)。

## 同一任務及接續

Task release-v5.8.0-prep/machine-key release-v5-8-0-prep、baseline151b90f6f5f2a352e828fa6f73402dfb8c4f5617、source_ref/ledger/sealed handoff/所有歷史不變。Finalize來源e744不是新task；原prep494與五檔patch、PR53原BLOCKED/延期保留不混入候選。

已提交5輪，本次唯一第6輪完成提交後才累計6/剩餘0，不自動第7輪，原nativeSTOP不改標PASS。先短測/新候選完整適用審查，再finalize→develop唯一PR/正式CI/原兩位reviewers正式PASS、mergecommit/actualdevelop短push，再既有PR52 develop→main的新CI/兩PASS/actualmain完整push；本任務已明確授權自動接續。必要正式驗證失敗集中交付停止，不盲目rerun。

成功後從實際通過main SHA用既有allowlist產待發布ZIP/manifest/checksum/Release草稿。六項人工只有原aab候選USER_REPORTED_PASS，不冒稱新HEAD/代理實測、不重做Tk/教材整包/本機全集。完整私有原件本機，PR只另製無payload/base64/ledger內容/私人路徑的公開技術摘要。不授權tag/公開Release/daily替換/formal教材判定Global寫入。

## 已取代的分支特例設計（僅保留調整歷史）

以下為前次未提交特例方案，已由上述永久政策取代，不是現行操作要求；原變更與證據保留，不追認其為測試或審查成功。

本文件記錄使用者明確採納的同一 `release-v5.8.0-prep` 任務第六輪方案調整，原 task baseline、sealed handoff、ledger、所有原失敗和兩輪審查保留。`codex/release-v5.8.0-prep` 的歷史 runner 修復與 PR53 延期，PR52 留作歷史；本次從固定 develop e744a5477aa8a7d812890982b07769eb32e1257f 建立獨立 `codex/release-v5.8.0-finalize` 候選，不將原五檔未提交差異混入，也不重設修正計數。

## 精確路由與成功條件

僅 `pull_request` 事件、repository/head repository/base repository 都為 `discoveryray/zhuyin-proofreader`、head branch 精確為 `codex/release-v5.8.0-finalize`、base branch 為 `main` 使用此路由。其他 PR、develop push、精確 PR29／simplify legacy 條件與 capture 原義保持。

限定發布 PR 使用原 main push 的 Windows Python3.13.0／單一 Python3.13 job；固定 bootstrap pip24.2，安裝 `requirements-ci-lock.txt` 的相同直接與間接依賴，不升級解析版本。`PYTEST_ADDOPTS` 為空、UTF8環境維持。依序核 checkout、Python/version、pip/dependencies、runtime integrity、collection inventory、全部有效非視窗 pytest、JUnit `verify-active`、compile、當前 PR base...head committed diff、clean-tree、environment 與原件 upload。

不執行完整 unittest、Tk、GUI、歷史 restore-history／aggregate／verify-coverage。這是使用者採納的當前候選發布资格；一般 PR 仍走原 runner。不得刪 case、整類 skip 或降低 verifier 標準：active IDs 必須完整且成功恰一次，unknown／missing／duplicate／skip／failure／error 皆拒絕。Inventory 或 upload 成功不能代替實際執行。

Required `Python 3.13` summary 必須同時確認 Grouped 與 develop short jobs 在此 route 為 skipped，及全部上述必要 step outcome 都為 success；failure／skip／cancelled／空值／缺件都不能成功。環境 capture 或 upload 失敗也阻擋。精確 legacy route 保留原 unittest 與其他條件；main push 完整驗證保持，不能拿本次 PR CI 代替它。

## 原件與審查

沿用既有 main-full collector、有限 allowlist 與 `main-full-<run>-<attempt>-<python>` artifact 名稱：inventory、JUnit、pip/dependency/runtime/pytest command＋raw輸出＋exit code，以及 environment 的 event/tested SHA/tree/ordered parents/Python/packages。限定 PR 的 event 明列 pull_request，tested SHA 為實際 PR synthetic checkout，不改稱 actual main merge。失敗亦保留可取得原件，不上傳整個 tmp、教材、DB 或秘密。

完整 private reports／handoff／ledger／原 attachments 保留本機供原兩位 reviewer 核對。GitHub evidence 只另外製作技術摘要（report_ref、scope、verdict、finding處置、CI公開連結、checksum及限制），明示摘要不是完整原件，不傳被拒的封存 payload/base64/private資料。不修改通用 gate／GitHub protection，不把 native STOP 改為 PASS。

原兩位獨立 reviewer 核對完整適用累積差異與當前候選 CI。取得兩個正式 PASS、必要 CI 與保護条件後，以 reviewed HEAD 的 merge commit 合入 main，再核 actual ordered parents/tree、main ref 與 main push 完整非視窗 CI。正式結果若失敗，集中交付並停止，不自行第七輪或盲目 rerun 舊失敗。

發布包依實際通過的 main commit 由既有明確 allowlist 組候選 ZIP／manifest／checksum，交 Release 草稿；不 tag、不公開 Release、不替換日常程式、不寫教材／人工判定／Global。原六項人工驗收僅原候選 `USER_REPORTED_PASS`，不是代理或新候選 GUI實測，整份教材校對完成不是軟體發布門檻。

本機只做定向 workflow路由和真 PowerShell summary失敗傳播短測／compile／diff；這些 fixture 不稱正式 full 或 CI PASS。原 history runner 的所有 BLOCKED／失敗／auto-review拒絕及未提交差異保持，不藉新候選資格宣告那些問題已修復。
