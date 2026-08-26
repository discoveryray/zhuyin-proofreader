# v5.4.1 實作稽核：單 PDF 增量 actual 更新與人工修正閉環

## 1. 目的
v5.4.0 已建立 GPT／人工 actual 雙軌自我學習，但動態 actual 證據一變動時，仍可能讓整冊 cache 廣泛失效；另外，人工 visual actual 證據雖已保存，若 occurrence override 因頁碼／stable-key 細微差異未真正命中，GUI 可能在更新後仍停留同一筆。v5.4.1 同時修正效能範圍與這個閉環問題。

## 2. scoped actual fingerprint
- actual workbook 會抽取本 PDF 實際依賴的 TTF raw-glyf SHA-256 與 CFF `(style_group, full_glyph_sha256)`。
- `manual_actual_occurrence_overrides.csv` 只把符合本 PDF scope 的列納入該 PDF 動態 hash。
- user-verified TTF/CFF 資料只把本 PDF 實際依賴的 exact key 子集合納入 fingerprint。
- 新 actual workbook 完成後再以實際依賴重新計算 scoped fingerprint 並重封 metadata。
- decoder code／靜態 actual assets 仍屬全域 fingerprint；程式碼改動不會被增量機制錯誤忽略。

## 3. baseline reuse plan
- pipeline 開始寫任何輸出前，先驗證並凍結既有 manifest，作為本輪 cache reuse baseline。
- 受影響 PDF 改寫後，不會因 manifest 中某個輸出 hash 已改變而連鎖使其他 PDF 失去 reuse 資格。
- candidate 只有在該 PDF actual 未重解且 expected fingerprint 未變時才沿用。

## 4. CFF 跨檔批次
- `cff_zero_map_batch.py` 新增 `--write-only-workbooks`。
- 跨檔自舉仍讀全部 actual workbooks，維持證據完整性。
- 只對本輪重解的 workbooks 寫入結果，其餘檔案不 save，避免無關 XLSX bytes 改變。

## 5. occurrence override 安全兼容
- legacy／工作階段可能分別記錄課本頁或實體頁；match 可接受其中任一頁碼命中。
- stable key 發生極小漂移時，只有 char 相同且 x/y 坐標差 <= 0.15 才容許以座標確認同一 occurrence。
- 不放寬成跨位置或跨字元套用。

## 6. 人工 actual 硬性 postcondition
`apply_manual_actual_correction()` 在重新解碼後會重新載入 manifest、DB、ledger，並驗證：
1. 能在新 ledger 找到同一 occurrence；
2. canonical post-actual 等於人工提交的 canonical reading。
任一條不成立即拋出明確錯誤；不得將「證據有寫入」誤報成「actual 已修正」。

## 7. 互動快速更新
- manual/GPT actual 修正後預設只立即重建必要 actual/candidate、session、pending、ledger、completion gate 與 pipeline status。
- 大型 `注音校對_最終報告.xlsx`、`注音校對_技術稽核.xlsx`、expected GPT workbook 延後產生。
- 使用者可在完成一批修正後按「更新 Excel 報告」一次重建展示型輸出。

## 8. exact-group 影響範圍
- pending occurrence 作為 review group seed。
- 同 exact glyph identity 的其他 PASS／非 pending occurrence 也納入 group，用於判斷真值升格後所有依賴 PDF。
- 這不改變任何 occurrence 的 expected 或狀態，只用來完整計算 actual 影響範圍。

## 9. 驗證
- 全測試：97 PASS；2 個需要桌面 display 的 GUI 視覺測試在 headless 環境 SKIP。
- 新增增量 actual 測試：
  - unrelated PDF/glyph truth 不改變另一 PDF scoped dynamic hash；
  - pending seed 能找到同 exact glyph 的 non-pending peers；
  - physical-page + tight stable-key drift 可安全命中 occurrence override；
  - 座標不夠緊時仍拒絕 stable-key mismatch。
- 真實康軒三下 P136「西」smoke：在單 PDF 測試專案中，人工提交 `ㄒㄧ˙` 後 canonical actual=`˙ㄒㄧ`、post state=`PASS`、`resolved_from_pending=True`；互動更新約 24.6 秒。
- 尚未宣稱完整 20 PDF corpus 的 v5.4.1 端到端計時驗證完成；本環境的多 PDF 完整報表測試受執行時間限制。
