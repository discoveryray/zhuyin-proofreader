# 注音校對工具 v5.2.1 實作與真實整冊驗收稽核

現行規範：**思考模式 2.5**。本版接續 v5.2.0 的 fail-closed 治理層，修正真實整冊驗收發現的 regression 聚合／語義問題、mandatory resolver 紅燈，以及兩個現版 PDF 句境／結構證據問題。不得以歷史結果替代現版 actual／expected 證據。

## 本輪主要修正

1. **全冊歷史 regression 聚合**
   - 78 個歷史 case 全冊只計一次，不再對 9 個分檔各自重複計入。
   - 非所屬分檔的 NOT_EXECUTED 不再形成 624 個假缺口。
   - 同一 case 若在多個分檔真正執行，改為 fail closed。

2. **歷史確認錯誤的正確回歸語義**
   - 歷史錯誤只在現版 actual／expected 已獨立產生後驗證。
   - 舊錯若現版已修正為 expected，視為 regression PASS；不要求歷史錯誤 actual 持續存在。
   - 歷史 actual 不參與現版 expected 推導。

3. **歷史 occurrence 定位**
   - phrase span 必須涵蓋目前 target position。
   - 支援 `target_occurrence_index` 區分同一 phrase 中重複 target。
   - 修正「詞在同一行出現，但不是目前標記字所在位置」的假命中。

4. **mandatory / resolver 安全修正**
   - mandatory 36 案目前 36/36 PASS。
   - 「寶寶」真值依簡編本完整詞條修正為 ㄅㄠˇ ㄅㄠˇ。
   - 修正「應當作…」跨詞界、「第一名」序數例外、「小人和他／人和」邊界等問題。
   - 「不」維持專案既有保守策略：去聲前接受 ㄅㄨˊ／ㄅㄨˋ 集合，非去聲前 ㄅㄨˋ；後字 expected 未決時棄權。
   - 簡編本完整詞條優先於較低階的專案字典例詞／單字 fallback。

5. **actual cache 與 controller 版本解耦**
   - actual fingerprint 使用 actual decoder 自身版本與 actual decoder source hashes。
   - expected/controller-only 修正不再無理由使 actual cache 失效。

6. **真實 PDF 證據修復**
   - P32 包裝圖「有效期限」：2026-08-18 原頁視覺確認該插圖文字沒有任何可見注音；更新現版三審分檔精確 structural exclusion（font_xref=164）。此 occurrence 保留 ledger 並成為 `EXCLUDED_OUT_OF_SCOPE`，不再冒充解碼錯誤。
   - P8「小寶寶」、P150「得1分」、P156「東西呢」：只依原頁可見文字重建跨行句境，不提供 actual 或 expected 讀音。
   - P150 因句境補全後，expected 可由現版 resolver 獨立建立，已由 `EXPECTED_AMBIGUOUS` 收斂為 PASS。
   - P156 不再由孤立單字「西」產生不安全差異，改保守停在 `RULE_CONFLICT`。

7. **最終報告效能**
   - `_style_sheet` 改為重用 openpyxl style object，避免百萬級 cell 重複建立 Alignment 物件。
   - 本環境真實 17,752-row ledger 最終報告由先前 >240 秒未完成，縮短至約 28 秒完成；不改變任何 ledger/state/證據資料。

## 驗證結果

- Python／治理與回歸測試：**61/61 PASS**。
- 核心資產驗證：**21/21 PASS**（actual 12、expected 9）。
- mandatory regression：**36 required / 36 executed / 36 passed / 0 failed / 0 NOT_EXECUTED / 0 duplicate**。
- 歷史 occurrence regression：**78 required / 78 executed / 78 passed / 0 failed / 0 NOT_EXECUTED / 0 duplicate**。
- 全體 regression gate：**114/114 PASS**。
- 全量集合對帳：**PASS**。

## 115國小健體3下課本－三審－最終檔案真實整冊結果

- PDF 偵測母體：**17,752** occurrence。
- 明確排除：**1**（P32 包裝圖「有效期限」無可見注音）。
- 現版校對分母：**17,751**。
- actual 覆蓋：**17,751 / 17,751 = 100%**。
- expected 覆蓋：**17,739 / 17,751 ≈ 99.9324%**。
- PASS：**17,714**。
- DIFFERENCE_PENDING_CONFIRMATION：**25**。
- RULE_CONFLICT：**11**。
- EXPECTED_AMBIGUOUS：**1**。
- ACTUAL_DECODE_ERROR：**0**。
- EXPECTED_UNRESOLVED：**0**。
- 資料完整性錯誤：**0**。

因此頂層狀態仍為：

`PROCESSING_FINISHED`

不是 `PROOFREAD_COMPLETE`。

## 尚待處理的 37 個 in-scope occurrence

### 25 個現版差異候選

- `寶`：18 筆。多數為「寶寶」第二字，actual 為 `˙ㄅㄠ`，現版完整詞條 expected 為 `ㄅㄠˇ`；P8 跨行案例已補回完整「小寶寶」句境。這些已有現版 actual/expected 差異，但正式 `TEXTBOOK_ERROR_CONFIRMED` 仍須依思考模式 2.5 完成六閘門確認。
- `強`：5 筆。「強行／強制」actual `ㄑㄧㄤˊ`、完整詞條 expected `ㄑㄧㄤˇ`；仍需六閘門確認。
- `一`：2 筆。P99「一年級」actual `ㄧ`、現版規則 expected `ㄧˋ`；歷史確認只能作 regression，正式錯誤仍須現版六閘門。

### 11 個規則衝突

- 「東西／西」：4 筆。手冊同形詞同時有方位義本調與物品義輕聲；目前保守停在 conflict，不用 actual 反選 expected。
- 「看看／看」：5 筆。簡編本存在 `ㄎㄢˋ ㄎㄢˋ` 與 `ㄎㄢˋ ˙ㄎㄢ` 等完整詞條讀法；目前沒有足夠規則唯一化。
- 「好玩／好」：2 筆。簡編本有 `ㄏㄠˇ ㄨㄢˊ` 與 `ㄏㄠˋ ㄨㄢˊ`；需更明確語義規則或人工判讀。

### 1 個 expected ambiguous

- P50「1用電安全5不1沒有!」的「不」：目前句型屬標題／列舉式「5不」，後續 sandhi 條件不足，依 fail-closed 保留未決。

## 仍未完成的架構問題

- 單字 fallback 尚未完成「全詞典／全變體唯一 allowlist」重構；目前仍不應把自動 expected 視為全面免人工的最終來源。
- 簡編本 variant/alternate reading 尚未完成一般化候選集合處理。
- 同形詞語義衝突（如東西、好玩）仍需可靠語義證據，不能靠 actual 反推。
- 「看看」等完整詞條多讀音需建立專案可接受集合／語義條件，否則維持 conflict。

## 最終判斷

v5.2.1 已通過治理測試、核心資產驗證、mandatory regression、歷史 regression 與全量集合對帳；真實整冊 actual 覆蓋已達 100%。但 expected 仍有 12 個未唯一化位置，另有 25 個現版差異尚未完成六閘門確認，因此**仍不能宣告全冊校對完成，也不能完全取代 GPT／人工 expected／差異覆核流程**。
