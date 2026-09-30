# Expected resolution 重播契約

GPT／人工 expected 匯入後，GUI 確認會取代同一 review_id 的 expected 事件。即使基礎列原本已有差異、讀音相同，確認仍須先重建原已保存的獨立 expected resolution，再使用原 GUI validator 驗證 actual、位置及 expected snapshot。

`expected_resolution_binding` version 1 保存原 expected action 的 canonical payload（讀音、來源、語境、reason、note）、套用 reusable rules 後的 baseline expected snapshot、完整 target、session、expected fingerprint、既有 resolver semantics epoch 與 integrity SHA。這些資料不含 actual。producer 在已驗證的 expected import/save transaction 建立 binding；GUI 只攜帶既有 binding，不從 confirmation snapshot 建立來源。

`standalone_proofread._apply_review_event` 共用 binding 格式、integrity、payload 及 applicability 驗證。已知 baseline expected 讀音／來源／語境、target、session 或 expected fingerprint 漂移時，保留目前列並標示 `INVALIDATED_EXPECTED_BINDING_DRIFT`。缺欄位、未知版本或 malformed binding 拒絕重播。適用的 binding 才重建原 expected action，隨後仍由既有 GUI validator 檢查 actual 與 confirmation snapshot。actual 漂移撤銷確認，但不污染獨立 expected 來源。

既有 `manual_expected_decision` 有自己的 occurrence-local 人工判定契約，繼續使用原 validator。沒有 fingerprint 的歷史 session 仍可沿用既有 unbound expected-only 匯入；它不取得此來源重建能力。未附 binding 的 GUI 確認，expected snapshot 必須與目前獨立 expected 相同，不能靠事件或 snapshot 補造來源。既有六項確認 gate 的判定及 portability 拒絕條件保留。

保存、prepare、repair 使用同一重播路徑。撤銷 actual-dependent confirmation 時，保留 binding 內原 expected action/payload。跨專案 expected transfer 先通過原 source proof、mapping、target priority、snapshot 與 transaction 檢查，僅在兩端 expected fingerprint 及 baseline expected snapshot 相同時，以已验证 target/session 重新 binding；undo 內已有 binding 的事件使用相同檢查。此契約不修改 identity、session/workbook/ledger schema version、semantics epoch、actual fingerprint 或 runtime manifest truth。

## 明確限定的歷史 adapter

`standalone_proofread.bind_legacy_gui_expected_resolutions(output_dir, workbook, *, review_ids, expected_manifest_sha256, expected_db_sha256, expected_workbook_sha256, dry_run=False)` 只處理具有原 13 欄位、已知 GUI writer/import source、基礎差異且同 expected 讀音的歷史事件。呼叫者必須明確選定原已填 workbook、唯一 review_ids 及三份原始 SHA；不搜尋檔案或信任 source 自由字串。

adapter 檢查 workbook schema/session/fingerprint、唯一 identity、列本身與目前 rule-applied baseline 的 expected/target digest，以及提出的 expected action/payload 與保存事件的一致性。最後使用完整原重播、GUI/actual validator、conflict、PDF、artifact 與 prewrite snapshot 檢查。任何一列失敗整批拒絕；成功只增加 binding，原 event 欄位、identity、confirmation time 均保留。binding 記錄 adapter 名稱、原 workbook/manifest/DB SHA 與 source row。

先使用 `dry_run=True` 核對。實際寫入採既有 project serialization 與 DB compare-and-swap；adapter 不發布新報表或宣稱完成。其後按原流程 repair、匯出待判清單及檢查 completion gate。確認數字只是各份資料的驗收證據，不是通用 gate。

## 隔離操作

原教材、PDF 與 Global DB 應唯讀；操作驗收在完整副本與 process-local `LOCALAPPDATA` 的 Global DB 副本執行。副本 manifest 中既存的原 PDF／artifact／dynamic evidence 路徑必須先逐項映射至已核對 bytes 的副本；不能讓 repair 依存在的原路徑讀回正式教材。只更新操作路徑，使用既有 `seal_manifest` 保存副本 manifest 的 payload integrity；保留 source digest、target、session、expected/actual fingerprint、artifact hash 與 bytes。這是副本 relocation，不能重算 runtime asset manifest truth。

本次任務 evidence 的 `copy_only_acceptance.py` 提供明確的 prepare、bind、repair-export 分段入口及原始 hashes／path changes／event invariants 報告；須由協調者指定已驗證的操作副本、原始 SHA 與 review_ids。它不是 production 的自動 legacy 掃描流程。
