# v5.3.1 實作稽核：人工確認介面可用性修正

## 修正範圍
- 修正「選擇／補充正確讀音」視窗在高 DPI／低解析度下看不到確認、取消按鈕。
- 修正 RULE_CONFLICT、EXPECTED_AMBIGUOUS 等狀態出現空白停用按鈕。
- 六閘門視窗同步改為固定底部操作列＋可捲動內容區。
- 主人工確認視窗採螢幕安全尺寸。
- 可重用規則詳細欄位預設收合。

## 不變範圍
本版未修改 actual decoder、expected resolver 判音內容、Occurrence Ledger、completion gate、來源驗證與 regression 語義。

## 驗證
- 全測試（Xvfb GUI 環境）：79 passed。
- GUI 版面測試同時在 800×600、1024×720 虛擬螢幕通過；確認／取消按鈕均位於視窗可見範圍。
- runtime 核心資產驗證：21/21 通過（actual 12、expected 9）。
- mandatory resolver integration 保持通過（36 required / 36 executed / 0 failed，由既有整合測試驗證）。
- 與 v5.3.0 比對：actual/expected resolver 核心來源檔 SHA-256 未變；standalone_proofread.py 僅版本／相容版本文字變更。

## 工作階段相容性
v5.3.0 與同 schema 的 v5.2.2／v5.2.1 人工事件可升級到 v5.3.1；不重算 occurrence_id／review_id，不清除既有人工判定。
