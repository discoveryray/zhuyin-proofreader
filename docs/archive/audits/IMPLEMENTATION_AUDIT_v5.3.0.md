# v5.3.0 實作稽核：校對員介面與報告重整

## 版本定位
- 版本：5.3.0
- 現行方法：思考模式 2.5
- 本版目標：保留 v5.2.2 治理與判音安全性，把 state machine、ID、hash、ledger、回歸、集合對帳等技術複雜度移到後台，讓教材校對員以一般中文完成日常工作。
- 本版不是新的判音版本；actual decoder 與 frozen expected resolver 判音來源檔均維持 v5.2.2 位元相同。

## 主要修改
### 1. 校對員人工確認介面
- 內部 state 改以一般中文顯示，例如「發現差異，請確認」「有兩種規則互相衝突」「正確讀音尚未確定」。
- occurrence_id、review_id、source_view、證據內部欄位預設隱藏，只在「更多…→查看技術資訊」顯示。
- 主要動作依狀態自動切換，不再固定呈現五個技術按鈕：
  - 差異：確認教材錯誤／課本其實正確／稍後處理。
  - 規則衝突：選擇正確讀音／稍後處理。
  - expected 未決：補充正確讀音／稍後處理。
  - actual 問題：程式辨識錯誤說明。
- 六閘門仍全部存在，但整合成單一視窗六個核取方塊；最後「確認為教材錯誤」才是 human_confirmation。
- RULE_CONFLICT 與 DIFFERENCE_PENDING_CONFIRMATION 都可透過「重建 expected 證據」合法收斂；人工不能直接指定 PASS，仍由 actual ∈ expected_set 的機械比較決定。

### 2. 可重用 expected 規則
- 人工補充／解決 expected 時可選擇升格成可重用規則。
- 規則只允許 exact「完整詞＋目標字位置」，可另加 context_contains；不建立單字通用音。
- 規則內容只包含 expected 與獨立來源，不讀 actual。
- 同一 match key 若出現不同 expected，fail closed，不強選。
- 規則儲存在 `可重用expected規則.json`；建立／更新工作階段時將當下規則 snapshot 與 SHA-256 寫進 session，使舊 session 的結果可重現。
- session materialization 使用 session snapshot，不會因全域規則檔之後改變而靜默改寫既有工作階段。

### 3. 主畫面
- 主要動作改成：開始新校對／繼續校對／更新報告／查看報告／更多…。
- 顯示人類可讀的專案狀態與待處理數。
- 已有 session 時若再次按完整重跑，先警告使用者。
- 詳細執行紀錄預設隱藏，只保留一行進度；技術 log 從「更多…」展開。

### 4. Excel 報告
`注音校對_最終報告.xlsx` 僅保留四頁：
1. `校對摘要`
2. `修正清單`
3. `待確認`
4. `人工與公司規則`

完整治理資料改到 `注音校對_技術稽核.xlsx`，仍保留 Occurrence Ledger、互斥 state views、集合對帳、來源驗證、回歸與執行資訊。

GPT 證據表仍保留 session/review/snapshot 等安全欄位，但預設隱藏，避免一般校對時干擾閱讀；匯入仍採整批交易式驗證。

## 思考模式 2.5 不變量
- actual 仍只來自 PDF glyph／字型解碼及 occurrence-specific actual override。
- expected 不得由 actual 反推。
- 人工按「課本其實正確」不會直接 PASS；必須重建 expected 證據並重新機械比較。
- RULE_CONFLICT、EXPECTED_AMBIGUOUS、未建立 expected、未解碼均不算正確。
- TEXTBOOK_ERROR_CONFIRMED 仍要求六閘門全部成立。
- PROOFREAD_COMPLETE completion gate 未放寬。

## Frozen source 比對
以下 v5.3.0 檔案與 v5.2.2 SHA-256 完全相同：
- export_zhuyin_readings.py
- export_pdf_text_diagnostics.py
- cff_zhuyin_decoder.py
- cff_unseen_family_bootstrap.py
- ttf_zhuyin_shape_decoder.py
- ttf_symbol_recombination.py
- cff_zero_map_batch.py
- occurrence_ledger.py
- check_pronunciation_candidates.py
- pronunciation_rule_engine.py
- concise_expected_resolver.py
- runtime_regression_gate.py
- runtime_source_validation.py
- runtime_asset_manifest.json

因此本版沒有藉 UI 重整改 actual 解碼或 frozen resolver 判音內容。

## 驗證結果
- Python 語法編譯：通過。
- 單元／整合測試：74/74 PASS。
- runtime 核心資產驗證：21/21 PASS（actual 12、expected 9）。
- mandatory regression：36 required、36 executed、36 PASS、0 FAIL、0 NOT_EXECUTED、0 duplicate（fresh one-PDF smoke 中重跑）。
- v5.2.2 → v5.3.0 `--report-only` 相容性：通過；原 session_id 保持不變，並補入空的 reusable-rule snapshot。
- v5.3.0 fresh one-PDF smoke：成功建立 session、actual/candidate、簡化工作報告與技術稽核報告。
- GUI headless launch smoke：主畫面與人工確認畫面皆可正常啟動；測試以 timeout 關閉，stderr 無例外。
- 簡化報告檢查：4 個工作表、無公式、gridlines 關閉、freeze panes 生效；完整技術報告另存 22 個工作表並保留 Occurrence Ledger 與集合對帳。

## 已知邊界
- v5.3.0 是介面／報告／人工 expected 證據工作流重整，不宣稱任何特定整冊教材因此自動達成 PROOFREAD_COMPLETE。
- 可重用規則是使用者核准的 post-resolver expected evidence layer；若使用者沒有正式獨立來源，仍應選「稍後處理」，不可依 actual 建規則。
- actual 辨識錯誤仍必須透過 occurrence-specific override 後重新解碼，不在人工結論畫面直接改 actual。
