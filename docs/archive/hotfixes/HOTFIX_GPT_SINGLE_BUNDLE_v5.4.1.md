# v5.4.1 GPT 單檔判定包熱修版 6

## 問題
第三輪可能同時包含 actual 視覺修正與 expected 證據補建。若只匯入 expected 檔，程式只會關閉 expected 已解決且 actual 一致的項目；actual 誤解碼仍會留在待處理池，造成使用者看到「49 -> 46」而誤以為匯入未生效。

## 修正
- GUI 可直接選擇 `.zip` GPT 單檔判定包。
- 判定包內 actual occurrence 視覺修正會先驗證並套用，再增量重解碼。
- actual 完成後，再匯入同包內 expected／差異工作簿，沿用 v5.4.1 safe rebase。
- actual 路徑只讀 occurrence identity、舊 actual／actual evidence hash、GPT 原頁視覺確認結果；不讀 expected 作為 actual 判定依據。
- 同一 exact glyph group 只有在包內至少兩個 occurrence 都經視覺確認且讀音一致時才允許群組推廣；否則維持 occurrence-specific override。
- 單獨匯入 expected xlsx 時，終端會額外提示目前仍有多少 actual 待處理項，避免把「匯入成功」誤解為「本輪已全部收斂」。

## 使用方式
「更多…」→「匯入 GPT 判定檔／單檔判定包（一鍵）」→ 選 GPT 判定包 ZIP。
