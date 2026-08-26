# v5.6.2 Implementation Audit

## 修正目標

- 讓 v5.6.1 已完成人工判定、待處理為 0 的專案可直接續作，不要求整批重解碼。
- 修正三上來源限定「轉動」規則造成 `M25-ZHUAN-DONG` 強制回歸失敗。
- 區分「同冊其他分檔尚未定位」與「歷史來源版本不適用」，避免舊頁碼案例永久阻擋現版教材。
- 讓 actual 待判定為 0 成為正常 no-op，而不是例外與代碼 1。

## 實作

1. `standalone_proofread.py`
   - 新增 `--repair-project`／`repair_project_state()`。
   - 由現有 sealed session 找回相同 PDF，交給既有增量 pipeline；actual fingerprint 相容時 reuse，expected 資產變更時重建 candidate，再重播人工事件並重算 completion gate。
   - `export_actual_pending_for_gpt()` 在 0 組時回傳 `None`；CLI 顯示明確訊息並正常結束。
   - expected／差異匯入完成後不再以舊 manifest 顯示中途 actual 數量；只引導使用本次報告／`pipeline_status.json`，升級規則時再執行修復。

2. 歷史回歸適用性
   - `pronunciation_regressions.csv` 使用 `applicability_mode`、`source_pdf_sha256`、`target_occurrence_index`、`locator_context` 分離來源版本與 occurrence identity。
   - `hard_gate` 指定 SHA 時只對 exact PDF bytes 適用；SHA 不符為 `SOURCE_NOT_APPLICABLE`。既有未綁 SHA 的 hard gate 維持原頁碼 locator 相容行為，新跨版本 gate 不再沿用此隱式模式。
   - `reference_only` 不再於函式入口直接跳過。現版仍依頁碼、完整詞語、目標字、詞內序號與必要 context 嘗試定位；找到後記錄 PASS／FAIL，找不到記錄 `REFERENCE_NOT_FOUND`，所有結果均不進 completion denominator。
   - 新增 `portable_gate`：歷史頁碼與歷史 actual／expected 均不參與 identity；全冊唯一命中才執行，0 筆為 `NOT_EXECUTED`，多筆為 `IDENTITY_AMBIGUITY/FAIL`，split 重複執行同樣 fail closed。
   - 三上 2026-08-15 的 15 筆案例逐筆覆核後，只有 `NEG-3UP-005`「一級棒／一」以完整詞 locator 升為 portable gate；其餘 14 筆因短詞、重複 occurrence、頁面／glyph 來源限制或缺少 context，維持 reference-only。沒有捏造 source PDF SHA。
   - candidate workbook、authoritative payload hash、全冊 split 聚合與技術稽核報告均保存適用性狀態、是否計入完成門檻、locator context 與命中數。

3. 「轉動」
   - 撤銷 `V38-MOE-ZHUANDONG-SPIN-3UP` 的ㄓㄨㄢˋ結果，依使用者既有回標「旋轉、轉動、轉速、轉盤均ㄓㄨㄢˇ」改為ㄓㄨㄢˇ。
   - actual 證據不參與此 expected 規則修正。

4. GUI
   - 「更新報告」改為「修復／更新報告」。
   - `PROCESSING_FINISHED` 且仍有失敗門檻時，直接列出 `failed_gates`。

## 安全邊界

- actual 與 expected 證據鏈仍完全分離。
- mandatory regression 仍必須 required>0、全數執行、0 fail、0 not-executed、0 duplicate。
- hard／portable PDF regression 仍必須 required==executed、0 fail、0 not-executed、0 duplicate；reference-only PASS／FAIL 只進非阻擋稽核統計。
- historical actual／expected truth 只在 occurrence 獨立定位後進行 evaluation，不得參與 locator。
- session／review identity、PDF SHA、輸出檔 hash、來源資產 manifest 與集合對帳均保留。

## 驗證

- Runtime asset manifest：20 assets，`ok=True`，0 errors，0 warnings。
- 三上檔名範圍 mandatory integration：36/36 executed，36/36 PASS。
- unittest suite：115 passed，1 個桌面顯示測試 skipped。
- 其餘 pytest-style top-level tests：32 passed。
- 新增測試涵蓋：exact SHA hard gate、reference-only 實際稽核、portable 唯一／零／多重定位、讀音真值不參與 identity、split 唯一 owner／重複執行、definition drift、non-gating reference 與 mandatory 安全隔離，以及三上 15 筆分類。

### 第二階段 develop 驗證（2026-08-26）

- applicability／completion／既有 regression semantics／第一階段 reusable expected preservation：42/42 通過；其中第一階段保護 7/7 通過。
- historical locator：6/6 通過；dual-evidence architecture：5/5 通過。
- 技術稽核報告在只於隔離測試副本校正既有資產 SHA 後：1/1 通過，含「歷史回歸稽核」新欄位。
- 完整 unittest discovery：142 項，137 通過、2 失敗、2 錯誤、1 個無顯示環境 GUI 測試 skipped。兩個失敗與報告錯誤均由 develop 基準既有的 14 個本次範圍外資產 SHA 不符造成；另一錯誤是環境未安裝 pytest，但該檔 6 個 locator 測試已直接執行全數通過。未降低或自動更新 fail-closed 資產檢查。
