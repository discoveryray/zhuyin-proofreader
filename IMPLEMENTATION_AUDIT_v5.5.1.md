# v5.5.1 實作稽核：glyph truth conflict quarantine

## 目的
修正 v5.5.0 以前 actual review 的耦合：一筆直接 visual occurrence 修正若與既有 reusable exact-glyph truth 衝突，不能因 global promotion 失敗而連 occurrence-local 修正一起回滾。

## 核心行為
- `apply_verified_actual_group()` 先把 direct visual evidence 視為 occurrence-local 真值層；reusable learning 為另一層。
- 若既有 user/static exact-glyph reading 與新 visual reading 相反，建立 `GLYPH_TRUTH_CONFLICT`。
- conflict registry：`glyph_truth_conflicts.csv`。
- audit provenance：`glyph_truth_provenance.csv`。
- 既有 user global learning row 改為 `QUARANTINED_CONFLICT`，因此 `load_user_verified_glyf/cff()` 不再載入。
- TTF decoder 另外在 runtime 對 quarantined SHA 移除 built-in/user donor，並阻擋 OOF、shape-auto 與 target bridge 使用該 SHA。
- conflict 時只保留 direct visual occurrence overrides；舊 promotion 擴散到未直接覆核 peer 的 override 會撤回。
- `affected_occurrence_ids` 包含整個 exact-glyph group，讓舊差異確認等 actual-dependent events 一起失效。
- conflict registry 納入 per-PDF dynamic fingerprint，依 workbook 的 exact SHA dependency roster 只 invalidates 真正受影響 PDF。

## 相容性
- tool 5.5.1
- ledger/session/workbook schema 2.6.0（不變）
- review identity schema 2.5.0（不變）
- actual review workbook schema 1.0（格式不變，v5.5.0 已輸出的 actual GPT workbook 可繼續匯入）
- actual fingerprint schema 2.8.0
- runtime asset manifest schema 2.6.0，tool_version 5.5.1
- v5.5.0 session 是 patch-level compatible；v5.4.x 仍只供稽核。

## 驗證
- `pytest -q`：133 passed, 2 skipped。
- runtime asset validation：ok=True，20 assets，tool=5.5.1。
- 單 PDF smoke：pipeline 1/3→2/3→3/3 正常完成；因現有資料仍有未決 evidence，completion gate 正確維持尚未完成。
- 專門回歸：先用兩個 visual occurrence 把同 SHA 升格成 `ㄩㄝˋ` 並傳播到四個 occurrence，再用同組 direct visual evidence 提交 `ㄌㄜˋ`：匯入不再報錯；兩個直接覆核位置改為 `ㄌㄜˋ`；其餘兩個 propagated overrides 自動撤回；user global row 改 `QUARANTINED_CONFLICT`；registry 同時保留 `ㄩㄝˋ|ㄌㄜˋ`。
