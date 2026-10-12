# v5.4.0 實作稽核：actual 雙軌自我學習

## 1. 不變的治理原則
- actual 與 expected 仍為兩條獨立證據鏈。
- actual 學習只接受 PDF 可見字形、exact glyph fingerprint/signature 與人工/GPT visual verification；不得讀 expected、辭典、中文字音義來產生 actual。
- PASS 仍只能由現版 `actual ∈ expected_set` 的機械比較產生。
- 教材錯誤仍需六閘門全部確認。

## 2. actual 雙軌入口
- GPT：輸出 `actual待判定_GPT包.zip`，按 exact glyph group 批次判讀，再以 session/group snapshot 交易式匯入。
- 人工：校對 GUI 的「實際注音辨識有誤／待建立」直接核對原頁。
- 兩條入口均呼叫 `apply_verified_actual_group()`，不建立兩套證據規則。

## 3. exact learning 安全門
### TTF
- key = raw glyf SHA-256。
- 1 occurrence = USER_VERIFIED_SINGLE；>=2 個獨立 occurrence 一致 = VERIFIED_EXACT_GLYPH。
- 既有 exact key 與新讀音衝突時 fail closed。

### CFF
- key = style_group + **完整 CFF 整字 recording SHA-256**。
- `CFF完整注音簽名` 僅保留作稽核，不得單獨成為可重用人工/GPT correction key。
- 此設計避免不同完整漢字 glyph 共用相同注音 component signature 時發生跨字誤傳播。

## 4. GPT actual 包的語義隔離
- xlsx 不含 expected／應標欄位。
- 不輸出 target Han character 欄位。
- 圖片先給 annotation-only，再給 whole-glyph 定位圖；不輸出句境 context 圖。
- VERIFIED group 有多個 occurrence 時必須 A/B 皆直接核對；不一致則 UNRESOLVED。

## 5. 動態 actual fingerprint
- `manual_actual_occurrence_overrides.csv`
- `user_verified_glyf_fingerprints.csv`
- `user_verified_cff_glyph_fingerprints.csv`
均經 schema 驗證後納入 actual fingerprint。變更會使舊 actual cache 失效，但不影響 expected fingerprint。

## 6. P122 遷移修正
先前 v5.3.1 補丁曾把康軒三下 P122「那」的 actual occurrence override 寫成 `ㄋㄚˋ`。高解析 PDF 原頁與 CFF glyph 均顯示 `ㄋㄚˇ`。v5.4.0 僅移除該精確 legacy patch；不寫入替代答案，讓 actual decoder 重新從 PDF glyph 獨立建立 `ㄋㄚˇ`。expected 仍可獨立判定為其他讀音並形成教材差異。

## 7. 驗證
- v5.4 actual-review tests：8/8 PASS。
- architecture：42/42 PASS。
- company semantic rules：7/7 PASS。
- historical locator：6/6 PASS。
- v5.3.1 hotfix regression：7/7 PASS。
- import transaction：8/8 PASS。
- mandatory resolver integration：1/1 PASS。
- regression semantics：8/8 PASS。
- report integrity：1/1 PASS。
- visual context repairs：2/2 PASS。
- GUI pure layout：2 PASS；2 個需要桌面 display 的視覺測試在 headless 環境 SKIP。
- P122 真實 PDF smoke：移除舊 override 後，actual=`ㄋㄚˇ`，CFF完整注音簽名相同的「哪／那」具有不同完整 CFF glyph SHA-256，證明新 key 可避免跨完整 glyph 的人工 correction 傳播。
