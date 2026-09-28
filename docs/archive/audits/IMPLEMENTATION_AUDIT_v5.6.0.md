# v5.6.0 Implementation Audit

## 目標
把 v5.5.1 的「版本／程式碼變動即失效」改成跨版本相容，保留 actual／expected 雙證據鏈與真正資料完整性檢查。

## 核心修改
- 新增 `cross_version_compat.py`：schema family 相容、review ID 相容、舊／新 fingerprint components 語意比對。
- `runtime_source_validation.py`：actual/expected reuse fingerprint 只綁會影響結果的 evidence assets；版本與 source hash 留在 components 稽核欄。
- `standalone_proofread.py`：取消工具版本硬擋；升版保留 session_id、人工事件與相容 expected fingerprints。
- `check_pronunciation_candidates.py`：舊 actual workbook 可用 evidence-assets semantic fingerprint 繼續分析。
- `actual_review.py`：actual GPT workbook 匯入忽略單純工具版本差異，仍驗證 session/schema/review identity。
- runtime manifest tool-version mismatch 改 warning。

## 保留的硬邊界
- PDF SHA-256 不同。
- 核心 expected/actual 資料資產 bytes 不同。
- 專案動態 actual 證據對該 PDF 的有效子集不同。
- occurrence/review identity 無法對應。
- schema major/minor 真正不相容。
- 檔案／JSON／Excel 結構損毀。

## 驗證
- `PYTHONPATH=. pytest -q`：139 passed, 2 skipped。
- v5.5.1 → v5.6.0 單 PDF cross-version smoke：actual reuse、candidate reuse、session_id 不變。
- 舊 v5.5.1 expected GPT workbook 在升版後可通過 v5.6.0 dry-run 匯入驗證。
