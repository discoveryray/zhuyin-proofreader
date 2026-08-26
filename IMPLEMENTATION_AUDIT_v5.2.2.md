# 注音校對工具 v5.2.2 實作稽核

現行規範：**思考模式 2.5**。本版只修改 expected resolver／公司規範與同形詞語義判讀，以及 v5.2.1 同 schema 工作階段升級；actual decoder、字型/CFF/TTF 解碼與 occurrence ledger identity 未修改。

## 修正內容

1. 公司現行規範：使用者 2026-08-19 明示「寶寶」第二個「寶」應依公司規定判為輕聲 `˙ㄅㄠ`。新增位置限定 expected 規則；第一個「寶」不受影響，規則不讀 actual。
2. 「東西」：依教育部《國語辭典簡編本》兩義，只有可見句境明確為物品義（如吃東西、哪些東西、什麼東西、買東西）才選第二字 `˙ㄒㄧ`；方位義明確句境才選 `ㄒㄧ`；其餘仍棄權。
3. 「好玩」：只有「好玩的…」或與「有趣」並列等可見句境明確對應「玩起來有趣」義時，才選 `ㄏㄠˇ`；其他語境不自行類推。
4. 「看看」：只有獨立「看看」帶受詞／子句的觀察、觀賞義才將第二字選 `ㄎㄢˋ`；「檢視看看」等 V+看看 語境仍保留 `RULE_CONFLICT`，不猜輕聲。
5. P50「5不1沒有」：仍缺足夠獨立 expected 證據，維持 `EXPECTED_AMBIGUOUS`；不使用 actual `ㄅㄨˋ` 反推 expected。
6. 完整詞／公司／語義位置證據可合法壓過孤立單字唯一讀音，符合思考模式 2.5「完整詞條／現行上位規範優先於單字捷徑」。
7. v5.2.1 → v5.2.2 同 schema 升級：保留原 session_id、occurrence_id、review_id 與人工六閘門 events；重跑 expected/candidate 後再套用既有人工事件。v5.1.5 等更舊 schema 仍拒絕。

## 驗證

- Python/治理/整合測試：**66/66 PASS**。
- runtime 核心資產：**21/21 PASS**。
- mandatory resolver integration：測試要求 **36 required / 36 executed / 0 failed**，已通過。
- actual decoder 程式與 actual 12 項資產未修改。
- 目標真實 PDF 重跑：
  - L01：2010/2010 occurrence 全部 PASS；原 18 筆「寶寶」與 P8「吃東西」衝突均收斂。
  - L04：1495 occurrence 全部 PASS；「哪些東西」兩筆收斂。
  - L07：2905 occurrence 全部 PASS；「好玩」2 筆與可唯一化「看看」收斂。
  - L03：保留「強行」及尚未套用人工事件的「強制」差異；P50「5不1沒有」仍 EXPECTED_AMBIGUOUS。
  - L08：P155「來檢視看看吧」仍 RULE_CONFLICT；「什麼東西」收斂；「強制」仍需由既有六閘門事件回套。

## 使用者現有 v5.2.1 工作階段的預期收斂

使用者已在 v5.2.1 完成 6 筆六閘門確認（4 筆「強制」、2 筆「一年級」）。v5.2.2 的同 schema migration 會保留這些事件。重新產生報告後，預期：

- `TEXTBOOK_ERROR_CONFIRMED`：6（保留既有人工確認）
- 新增自動 PASS：18 筆「寶寶」+ 4 筆「東西」+ 4 筆可唯一化「看看」+ 2 筆「好玩」= 28
- 尚待人工／未決：3
  - `DIFFERENCE_PENDING_CONFIRMATION`：1（「強行通過」）
  - `RULE_CONFLICT`：1（P155「來檢視看看吧」）
  - `EXPECTED_AMBIGUOUS`：1（P50「5不1沒有」）

上述 3 筆不得為了完成率硬判。只有使用者實際以 v5.2.2 對原工作階段按「重新產生報告」後，才以新報告數字為最終真值。
