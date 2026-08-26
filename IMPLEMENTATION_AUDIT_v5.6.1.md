# v5.6.1 Implementation Audit

## 修正範圍

- 基準：v5.6.0 雙證據鏈＋跨版本相容版。
- 問題：`_aggregate_pdf_regression_rows()` 將 `required == 0` 強制視為失敗，造成沒有任何適用歷史 occurrence regression 的教材即使 mandatory regression 與其他完成門檻全部通過，也無法進入 `PROOFREAD_COMPLETE`。

## 修正

原邏輯：

```python
required > 0 and failed == 0 and not_executed == 0 and duplicate_count == 0
```

新邏輯：

```python
executed_count == required and failed == 0 and not_executed == 0 and duplicate_count == 0
```

因此：
- 0 required / 0 executed：PASS（無適用案例，中性通過）。
- required > 0：仍必須每一案例恰好執行一次，且 0 FAIL、0 NOT_EXECUTED、0 duplicate。
- mandatory regression 不受此修正影響。

## 相容性

- 不修改 ledger/session/workbook schema。
- 不修改 actual/expected evidence semantics。
- 不修改 evidence asset hashes。
- v5.6.0 專案可直接續用並重新計算 completion gate。

## 新增測試

- 全冊沒有任何 historical regression rows 時，aggregate report 必須 `ok=True`。
- 多個 split PDFs 全部沒有適用 rows 時，aggregate report 必須 `ok=True`。
- 既有 required>0、未執行、重複執行、定義漂移測試保留。

## 驗證結果

- Targeted regression tests：15 passed。
- Full suite：141 passed, 2 skipped。
- Runtime asset manifest：20 assets，`ok=True`，0 errors，0 warnings。
- Completion smoke：mandatory regression 36/36 PASS + PDF occurrence regression 0/0 applicable → combined regression gate `ok=True` → `PROOFREAD_COMPLETE`，0 failed gates。
