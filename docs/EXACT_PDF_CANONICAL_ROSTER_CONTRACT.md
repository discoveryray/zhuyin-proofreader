# 同 PDF 完整 canonical roster 配對契約

`same-pdf-canonical-roster/1` 僅適用 `_match_pdfs` 已核對完整、各自唯一且相同的 PDF SHA 集合、目標實體 bytes 與既有完整畫面及文字頁面證據後。內部 admission 綁定來源／目標 manifest、mapping 及頁面資料；一般 dict 不提供此 admission。既有三個呼叫入口仍依序執行 `_match_pdfs`、`_map_reviews`，其後 actual／expected 與交易檢查保留。

現行 session／ledger `2.6.0`、workbook `2.6.1`、review ID `2.5.0` 才進此新契約，兩側封印仍須有效。schema-less 歷史入口及 workbook `2.6.0` 保留一般嚴格 anchor 路徑，不獲新契約；其他未知版本拒絕。一般 `_anchor` 與 renderer 不變。

每筆從原 `source_record` 欄位及所屬已封印 PDF partition SHA 重算 occurrence/review ID，不採 row/index fallback，不以頂層補缺。身份 aliases、頂層與原始欄位、confidence、collision、保存 ID 全部核對；canonical base 與兩種 ID 各自唯一。完整雙側 roster 與逐筆原始 bbox、頁碼、identity payload、display matrix、頁面尺寸必須相同，所有資料一對一、不依排列。定位須頁碼有效、數值有限、matrix 非奇異、尺寸正值、bbox 高度正值且寬度非負；允許原有零寬，不補寬或略過。

`sealed-top-true-source-N/1` 唯一讀取例外是頂層精確 `bool True`，原始精確字串 `N`。上述全部條件仍成立才可判為已證明非 fallback；真正 fallback、其他旗標矛盾、未知表示、偽 ID 或 collision 拒絕，不落回 anchor 掩蓋。完整適用清單保存在回傳 mapping 的 `fallback_adapter_ids`，成功入口 stdout 記錄兩個 contract、records 與 observations 數。兩側合計 observations 不是不同邏輯身分或真正 fallback 數。

所有已登記 identity aliases 在 source_record 與頂層各自核對一致性；頂層正式欄位必須存在，並直接對照原始 identity／定位值。尤其 char、physical_page 不得被較高優先 alias 遮蔽；缺欄、必要值為空或矛盾均拒絕，不能先取第一個 alias 就宣稱正式 projection 一致。正式欄位單獨使用既有 `_row_value(entry, key)` 空值投影，保留可空 component 的 `None`／空字串契約，不改寫資料。

Producer `parse_identity_row_fallback` 明確支持 bool、整數 0/1、精確字串 N/Y/FALSE/TRUE/0/1；其他值拒絕。producer 既有缺欄位／空欄位投影保留 False，但新 roster 契約要求明示有效旗標，缺欄位不獲 admission。來源字典不改寫。

不修改 ID 生成規則、schema、封印、原 bbox、manifest／source_record、Excel 或判定值。actual／expected 值、狀態、路徑、列號不參與配對身份。歷史產生器精確版本未證實；此 adapter 不據此重建歷史，也不構成完整匯入成功證據。
