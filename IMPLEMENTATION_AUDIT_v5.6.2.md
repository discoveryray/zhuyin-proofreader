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
   - `pronunciation_regressions.csv` 新增 `applicability_mode`、`source_pdf_sha256`。
   - `hard_gate` 維持既有要求；指定 SHA 時只對 exact PDF bytes 適用。
   - `reference_only` 產生 `NOT_APPLICABLE` 稽核列，不進 required／executed／not_executed 分母。
   - 三上 2026-08-15 的 15 筆舊頁碼案例因沒有保存 exact PDF SHA，改列 reference-only；真值與備註仍保留。

3. 「轉動」
   - 撤銷 `V38-MOE-ZHUANDONG-SPIN-3UP` 的ㄓㄨㄢˋ結果，依使用者既有回標「旋轉、轉動、轉速、轉盤均ㄓㄨㄢˇ」改為ㄓㄨㄢˇ。
   - actual 證據不參與此 expected 規則修正。

4. GUI
   - 「更新報告」改為「修復／更新報告」。
   - `PROCESSING_FINISHED` 且仍有失敗門檻時，直接列出 `failed_gates`。

## 安全邊界

- actual 與 expected 證據鏈仍完全分離。
- mandatory regression 仍必須 required>0、全數執行、0 fail、0 not-executed、0 duplicate。
- hard-gate PDF regression 未執行時仍 fail closed；只有明確 reference-only／SHA 不相符者可 NOT_APPLICABLE。
- session／review identity、PDF SHA、輸出檔 hash、來源資產 manifest 與集合對帳均保留。

## 驗證

- Runtime asset manifest：20 assets，`ok=True`，0 errors，0 warnings。
- 三上檔名範圍 mandatory integration：36/36 executed，36/36 PASS。
- unittest suite：115 passed，1 個桌面顯示測試 skipped。
- 其餘 pytest-style top-level tests：32 passed。
- 新增測試涵蓋：三上「轉動」ㄓㄨㄢˇ、reference-only／SHA mismatch、actual 0 正常結束、repair 保留 session、GUI 顯示失敗門檻。
