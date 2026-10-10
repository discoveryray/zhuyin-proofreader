# Expected 匯入業務衝突

本契約是使用者明確採納的本機功能第 4 輪。容量、PDF 頁面／bytes、canonical occurrence/review identity、來源封印、bound rows、expected fingerprint、原始所在行／局部詞境及來源事件有效性仍先完整驗證。結構、來源、交易／owner／CAS 失敗仍整批拒絕，不轉成可略過列。

帶完整內藏 proof 的同／不同 session Excel 共用分類入口。目標沒有正式 expected 時沿用合法事件匯入。目標已有正式 expected 時，以 canonical 讀音集合、已驗同位置及相同原始語境核對：一致者保留目標規則或人工事件，另存來源補充；不同或無法證明相同條件者保留兩份證據及原目標正式判定，僅新增待裁決工作流程。提案的自然語言說明不解析為義項，不從 actual 推導 expected，不製造目標人工事件、Global 規則或 quorum。

來源和目標原始所在行／局部詞境不符仍拒絕。歷史欄位皆空時，只有相同非空原 context_evidence 可證明相同條件；其他已通過必要契約而條件不明者保留待裁決。缺少 expected binding 必要位置／語境或未知 contract 仍拒絕。

`跨專案判定衝突.json` 新寫入 version 2、contract `expected-business-conflict/1`，固定欄位 `version, contract, conflicts, supplements, integrity_sha256`。每筆新記錄固定 source/target 身分、合法來源事件與 `target_basis`，後者明訂 `review_event` 或 `sealed_resolver_record`，保存原 expected snapshot、target、session、expected fingerprint、原事件（規則來源為 null）及歷史 manifest seal。整份 canonical JSON 摘要核對，未知版本／欄位、重複來源、錯誤位置、basis 或不一致補充均拒絕。

歷史 version 1 event/event 回執與 v2 中既有 v1 entry 僅沿用原有限 validator，不將規則轉成人工事件。既有 project-transfer writer 延伸 v2 時保留原 supplements 和完整舊衝突。歷史 seal 是稽核資訊；當前 manifest 仍完整驗封，適用性另依 identity、expected snapshot／fingerprint 與原事件檢查。合法 actual-only seal 更新不成為 expected 全域依賴。

整批分類完成後才使用既有 lock、owner presentation plan、receipt／DB CAS、發布與失敗回復。v2 conflict 不停用既有人工 expected；未裁決 receipt 是獨立完成門檻，待辦／GUI 顯示 workflow annotation，不修改 manifest、原 bbox／ID 或原 expected truth。人工入口顯示來源提案／依據與原目標規則／人工快照，使用真正 `ENTER_EXPECTED` 操作並引用原 receipt。已展示 receipt SHA 在 SAVE 前與寫入前核對，漂移要求明確 reload，不把未見的新衝突一起裁決。

同檔重匯只記已處理重複，不新增 supplements/conflicts，不重新啟用本地否決的舊來源。三值回傳保留相容性，另附已保存、新增、一致補充、重複、待裁決、保留待人工及未操作列統計。一般已保存衝突正常回傳；異常交易仍拋出錯誤並保留／回復既有資料。

功能第 5 輪追加明確人工選擇：已有 expected 或待裁決時，可由使用者確認有效的目前 actual 讀音作為本筆 expected。這只將當次選取讀音固定在既有本筆人工判定紀錄，不自動推導、不新增可重用規則；日後 actual 改變不改此 expected。保存前另核對已選讀音與顯示證據 snapshot、當前 sealed identity／來源及已套用 actual，未套用暫存或證據失效拒絕；`ENTER_EXPECTED` 不增加 actual 依賴。快捷裁決使用既有 receipt token／CAS 並保存原人工事件的單層 undo 來源，重匯不復活否決來源。完成項不因快捷重新加入待辦；既有明確 redecision 與 receipt pending 入口維持。

限制：proofless 同 session 歷史入口保持原嚴格行為。本輪不驗 GUI、真實教材匯入、完整套件、review／CI／PR／merge。短測 producer fixture 採有限 writer 入口 shim：顯式 `trusted_root(output_dir)`＋`binding_scope`，呼叫原 producer body，所有原 seal／SHA／bound-row 檢查仍執行，且在受測 import 開始前移除。既有 production writer 的 root-first decorator 收到 Workbook 的 TypeError 保留原失敗紀錄，未在本輪擴修，故不宣稱 production export 通過。
# 非 expected-only 依賴待重核

`review-dependency-recheck/1` 是 receipt v2 `conflicts` 的有限 entry 契約，僅接受已由完整 Excel proof、sealed identity、原始定位／語境、fingerprint 與來源事件重播驗證的 `確認現版差異`、`確認非校對範圍`。來源與目標基線的 state/actual/actual_evidence/expected_set/expected_evidence/context_evidence 不同，或差異確認的當前五項證據不同時，不套用來源動作，不重綁事件；其他合法列仍依原交易一起保存。

entry 保留原 action/event、來源完整單筆 snapshot、目標原基線及原當前 snapshot、來源封印 audit 與逐欄差異（明示 baseline/current）。讀取時核對 ledger 合法性、來源與目標身份／原始語境、來源事件重播、目標原事件對歷史基線的完整重播、差異重新計算，以及原 expected fingerprint；當前 manifest 仍須驗封。历史 actual snapshot 是稽核，不新增 actual→expected 的全域依賴。

`RECHECK_LOCAL_REQUIRED` 保留原目標事件與兩條正式證據，只有 workflow overlay；既有 review queue 顯示原動作與具體差異。可使用既有人工排除或本筆應標輸入，在 SAVE 時經原重播、receipt ownership/CAS 與本地裁決 token 保存。來源動作不會被改寫為 expected resolution。

`PRESERVED_LOCAL_DECISION` 僅適用原目標為無 portability_source 的 allowlisted 人工事件、可重播且已成 PASS/TEXTBOOK_ERROR_CONFIRMED/EXCLUDED_OUT_OF_SCOPE；只保存來源稽核，不重開完成項目。有事件但未完成、失效或來自先前匯入均不能用此分類。相同已驗證輸入重匯沿既有來源身分及 receipt 判重；裁決後不復活。未知契約、損壞 snapshot/differences、失效來源與交易異常仍拒絕整批；v1 與既有 business/1 entry 驗證保持原契約。
