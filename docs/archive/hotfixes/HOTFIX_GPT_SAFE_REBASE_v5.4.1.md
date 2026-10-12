# v5.4.1 GPT 安全重定位／證據鏈獨立熱修（Hotfix 5）

## 問題

在 GPT expected 證據表匯出後，只要使用者先匯入 actual 視覺判定、重新解碼或刷新 actual 證據，舊版 `review_snapshot` 會因為同時包含 actual 與 expected 而全部改變。即使 GPT 判定只是在補充 expected 完整詞條，也會被整批拒絕為：

- `stale state`
- `stale/modified review snapshot`

此外，actual 修正流程會把同 occurrence 上的所有人工事件一併移除，連與 actual 無關的 expected 證據也會被清掉。這與思考模式 2.5 的 actual／expected 獨立原則不一致，也迫使使用者反覆匯出、匯入或手動維護 CSV。

## 修正

1. **新增 expected-only snapshot**
   - 新匯出的 `待判定候選_給GPT.xlsx` 增加隱藏欄 `expected_snapshot`。
   - 只包含 occurrence/review identity、expected_set、expected_evidence、context_evidence。
   - 不包含 actual、actual_evidence、state。

2. **action-scoped stale validation**
   - `補建expected證據`／`解決expected證據`：若 full snapshot 已變，但 expected/context chain 未變，允許安全重定位後匯入。
   - 舊版工作簿沒有 `expected_snapshot` 時，會用匯出列中的 expected/context 欄位重建相容 snapshot，因此 Hotfix 5 可直接處理 Hotfix 4 已匯出的工作簿。
   - `確認現版差異` 仍要求 full snapshot 完全一致；actual 或 expected 任一側改變都必須重新六閘門確認。
   - expected/context 本身若真的改變，仍 fail-closed 拒絕，不會強行套用 stale 判定。

3. **未操作列不再拖垮整批**
   - action 空白或 `保留待人工` 的列不屬於匯入 transaction，不再因 stale 而拒絕其他已填寫列。

4. **actual 修正不再刪除 expected 證據**
   - `補建expected證據`／`解決expected證據` 會跨 actual refresh 保留並重新 materialize。
   - 若先前已 `確認現版差異`，actual 改變時只撤銷 human confirmation；若該事件內含 expected 完整證據，會降級保存為 `解決expected證據`，不丟失答案鏈。

5. **expected event 可在 PASS 基線上重播**
   - PASS 可能只是 actual refresh 後的機械結果；既有獨立 expected 證據仍可安全重播並重新比較 actual／expected。
   - 不允許 stale workbook 覆寫已確認教材錯誤或明示排除。

6. **GUI 支援多選 GPT 判定檔**
   - `匯入 GPT 判定檔（自動辨識／可多選）` 可一次選 actual 與 expected xlsx。
   - CLI 自動依工作表辨識，固定先 actual、後 expected。
   - 使用者不需要再決定匯入順序。

7. **移除手動 CSV 的正常流程指示**
   - expected GPT 表的使用說明不再要求使用者手動修改 `manual_actual_occurrence_overrides.csv`。
   - actual 修正應走 actual GPT 視覺判定包，由程式自行維護 override 與增量解碼。

## 安全邊界

- actual 仍只能由 actual decoder／視覺確認後的 actual override 改變。
- expected 仍只能由 expected resolver／公司規範／權威詞條／人工 expected 證據改變。
- expected-only safe rebase 不讀 proposed expected 來修 actual。
- 最終 `確認現版差異` 不允許 safe rebase，避免舊 human confirmation 被帶到新的 actual／expected 組合。
- 真正的 expected/context drift 仍整批拒絕。

## 測試

新增 `tests/test_gpt_safe_rebase_v541.py`，涵蓋：

- expected-only action 在 actual-only change 後仍可匯入。
- actual change 令 base state 變 PASS 時仍可重播 expected 證據。
- 真正 expected/context drift 仍拒絕。
- 未操作 stale rows 不拒絕 batch。
- actual refresh 保留 expected event。
- actual refresh 撤銷 confirmation 時保留其 expected evidence。
- 多選匯入自動排序 actual → expected。
