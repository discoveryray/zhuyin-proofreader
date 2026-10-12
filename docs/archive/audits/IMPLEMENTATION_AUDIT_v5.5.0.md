# v5.5.0 實作稽核：actual／expected 雙證據鏈架構

## 1. 為何升級架構
v5.4.1 熱修鏈的共同故障不是單一 stale 判斷，而是資料模型把 actual 與 expected 壓成一個 `state` 與一個 review snapshot。當 actual 修復後，expected resolver 才開始跑，導致先前獨立查得的 expected 被誤判為 stale；反向也可能因 `ACTUAL_DECODE_ERROR` 遮蔽 expected conflict。

## 2. 雙證據鏈
每筆 occurrence 現在同時維護：
- `actual_status`: RESOLVED / UNRESOLVED / DECODE_ERROR / EXCLUDED
- `expected_status`: RESOLVED / UNRESOLVED / AMBIGUOUS / CONFLICT / EXCLUDED
- `comparison_result`: 僅在兩邊 RESOLVED 時產生 MATCH / MISMATCH
- `state`: 由上述欄位機械推導，只供工作流程顯示。

任何一條 lane 的未決都不會清除另一條 lane 已建立的證據。

## 3. expected 不再等待 actual
`check_pronunciation_candidates.py` 現在先執行 expected resolver，再處理 actual gap。actual 缺失時，candidate ledger 仍保存 expected_set、expected_evidence、context_evidence 與 expected_status。

同時移除一個隱性 actual→expected 耦合：統一用字手冊的多讀音允許集合直接成為 expected_set；不再先查看 actual 是否落在集合外才建立 expected。

## 4. completion gate
覆蓋率與阻擋條件直接由 lane status 統計：
- actual coverage = actual_status==RESOLVED
- expected coverage = expected_status==RESOLVED
- decode_error_zero 只看 actual lane
- expected_unresolved / ambiguous / conflict 各看 expected lane

因此同一筆可同時是「工作流程顯示 ACTUAL_DECODE_ERROR」且「expected_status=CONFLICT」，兩個硬門檻都會正確反映。

## 5. GPT evidence handoff
expected workbook 只輸出 expected lane 尚待處理者與真正差異，不再包含「actual 未解、expected 已解」的純 actual 問題。

expected evidence 綁定 `expected_target_snapshot`（PDF／頁碼／字元／所在行／局部詞境）與 `expected_asset_fingerprint`，不再綁定 mutable actual 或 automatic expected output。`解決expected證據` 可在同一 target/context 上覆蓋較低優先 resolver 結果；`補建expected證據` 若目前已有不同 resolved expected 則 fail closed，要求改用明確解衝突動作。

GPT bundle 在 actual 寫入前會先 dry-run/preflight expected workbook，減少半套提交。

## 6. project-scoped dynamic actual evidence
動態 visual actual evidence 移至：
`<校對專案>/_專案證據/actual/`

包含 occurrence override、TTF exact glyph truth、CFF exact glyph truth。actual decoder、fingerprint、manual/GPT import 均指向專案 evidence root；immutable runtime assets 仍留在程式目錄並由 manifest fail-closed 驗證。

## 7. 相容性
這是資料模型 breaking change：ledger/session/workbook schema = 2.6.0。v5.4.x session 不直接升格成 v5.5.0 terminal state。review ID 演算法未改，因此 REVIEW_ID_SCHEMA_VERSION 仍為 2.5.0。

## 8. 驗證
- pytest：130 passed, 2 skipped
- runtime asset manifest：ok=True，schema=2.6.0，tool=5.5.0
- 單 PDF smoke：345 occurrence；336 PASS、4 ACTUAL_DECODE_ERROR、2 EXPECTED_AMBIGUOUS、2 DIFFERENCE_PENDING_CONFIRMATION、1 RULE_CONFLICT。
- 4 筆 ACTUAL_DECODE_ERROR 的 expected_status 均為 RESOLVED，證明 expected 不再因 actual gap 被跳過。
- expected GPT 表只有 5 筆（2 ambiguous + 2 difference + 1 conflict），沒有重複輸出上述 4 筆純 actual 問題。
- 專案 `_專案證據/actual` 已建立三個動態 evidence 檔。
