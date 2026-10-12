# v5.4.1 零 occurrence PDF 熱修說明

## 問題
批次處理附件刀模／純製作 PDF 時，若該 PDF 的 actual source rows 與結構排除 rows 都為 0，候選階段會呼叫 `build_occurrence_ledger([], {})`，並被全域 `validate_occurrence_ledger()` 的「occurrence ledger 不得為空」中止整批流程。

## 修正
1. `check_pronunciation_candidates.py`
   - 新增 `_build_candidate_ledger()`。
   - 只有在「actual source 確實為空且 classifications 也為空」時，允許該單一 PDF 產生空 candidate ledger。
   - 若 source 為空但 classifications 非空，仍 fail-closed，避免把資料遺失誤當成合法空檔。
   - 非空 source 仍完整走原本 `build_occurrence_ledger()` 與 `validate_occurrence_ledger()`。

2. `standalone_proofread.py`
   - `_parse_ledger_rows()` 允許 header-only 的單 PDF candidate ledger，供全冊 manifest 與集合對帳使用。
   - `collect_manifest()` 與 `materialize_ledger()` 也允許整批 0 occurrence，不會 runtime fatal；completion gate 仍會以 `in_scope_occurrence_count_positive=False` 阻止誤宣告「全冊完成」。
   - 非空 ledger 仍執行原有嚴格 validator。

## 安全邊界
- 沒有修改 `occurrence_ledger.py` 的全域 validator。
- 沒有修改 actual decoder、manual occurrence override、expected 判音規則或 actual/expected 分流邏輯。
- 因此本修正不會把「有 actual 卻漏建 ledger」靜默放行。
- `check_pronunciation_candidates.py` 是 expected fingerprint 的來源檔，因此第一次使用此修正版時 candidate 會重建；actual fingerprint 來源檔未變，不應因此要求全部 actual 重解碼。

## 測試
新增 `tests/test_zero_occurrence_pdf_v541.py`：
- 真正空 PDF candidate ledger 可接受。
- source 空但 classification 非空時仍拒絕。
- header-only candidate ledger 可由控制器解析。

完整測試結果：`102 passed, 2 skipped`。

## 2026-08-21 第二次熱修

1. 修正 `collect_manifest()` 對摘要數值 0 使用 `value or -1` 的錯誤判定。零 occurrence PDF 的「偵測到注音字形筆數」「PDF偵測母體總數」「現版注音校對分母」現在都會把 0 當作合法數值，不再誤轉成 -1。
2. `pipeline_status.json` 在 [1/3]、[2/3]、[3/3] 都會更新進度；此檔僅供 UI 顯示，不是完成證據，也不取代封存的 `校對工作階段.json`。
3. GUI「目前專案」改為優先讀取 `pipeline_status.json`。即使第一次分析在建立工作階段前失敗，也會顯示「處理被阻擋」與最後錯誤，不再誤顯示成「新的校對專案」。
4. GUI 每 0.5 秒刷新目前專案狀態，可在執行期間顯示 [1/3]、[2/3]、[3/3]。
5. 若第一次分析已完成 1/3 actual 與 2/3 candidate，卻在 3/3 建立工作階段前失敗，下一次啟動會先對既有輸出執行完整 `collect_manifest()` 驗證。只有 PDF hash、actual fingerprint、expected fingerprint、schema、candidate payload hash、actual/candidate 對帳與摘要統計全部通過，才直接續跑 3/3；任一項不通過就回退完整重跑。這可避免像本次 0/-1 邊界錯誤修正後又白白重解 16 個 PDF。
