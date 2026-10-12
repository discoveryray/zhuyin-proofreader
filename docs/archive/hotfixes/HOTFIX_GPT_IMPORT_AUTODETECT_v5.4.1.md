# v5.4.1 GPT 匯入自動辨識熱修版3

## 問題
`待判定候選_給GPT.xlsx` 類型的 expected／差異證據檔若誤從「匯入 GPT actual 判定結果」進入，actual importer 會要求 `actual待判定` 工作表並中止。因交易沒有成功，`pipeline_status.json` 合理地保持匯入前狀態，看起來像「目前專案沒有更新」。

## 修正
- GUI 改成單一 GPT 匯入入口並自動辨識 workbook 類型。
- expected/difference：必須含 `待判定候選` 與 `匯入中繼資料`。
- actual：必須含 `actual待判定`。
- 同時含兩種核心工作表時拒絕匯入，避免 actual/expected 證據鏈混用。
- 失敗操作在 GUI 明確顯示非 0 exit code，不再顯示成一般「處理已結束」。

## 安全性
本熱修只改控制器與 GUI 匯入路由，未修改 actual decoder source list 或 expected resolver source list，因此不改 actual/expected fingerprint，也不會因此使既有 cache 失效。
