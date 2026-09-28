注音校對工具 v5.7.0－人工 actual 批次確認與證據相容性強化版（思考模式 2.5）

v5.7.0 將人工 visual actual 改為「先暫存多筆、最後一次套用」：可連續核對原頁而不立即重跑 PDF，最後按「套用 actual 修正（N）」才正式寫入 actual evidence，並一次增量更新真正受影響的 PDF。新版同時強化 fingerprint／semantics compatibility 的 fail-closed contract；actual 與 expected 仍是完全獨立的證據鏈。
v5.6.2 新增「修復／更新報告」續作入口：同一教材與同一「注音校對_輸出」可直接沿用既有 actual、人工/GPT 判定與 session；actual 證據未變時不重解碼，只重建受規則／回歸修正影響的 expected 候選與完成門檻。
v5.6.1 修正「本批沒有任何適用 PDF occurrence regression 時，required=0 被誤判為 regression gate 失敗」；0 個適用案例現在視為中性通過，mandatory regression 與所有實際存在的 regression 案例仍維持原硬門檻。
v5.6.0 以 v5.5.1 為基準，保留 occurrence-local actual、GLYPH_TRUTH_CONFLICT 隔離與 actual／expected 雙證據鏈；主要改動是把「工具版本」從硬性安全邊界改成稽核資訊，讓同一校對專案可直接跨版本續用。

第一次使用：執行「安裝依賴.bat」。
平常使用：執行「啟動_單機注音校對.bat」。

跨版本相容策略（v5.6.0 起）
- 不再因程式版本號不同就要求重跑；同一專案的 session_id 會跨版本保留。
- ledger/session/workbook schema 只在真正資料布局不相容時阻擋；同一 schema family 的 patch 差異可直接讀取。
- actual／expected cache 是否可沿用，改看「PDF bytes＋實際會影響結果的資料資產＋專案動態 actual 證據」，不再因 decoder/resolver 版本或程式原始碼 hash 改變而全面失效。
- 舊版 expected/GPT 判定表只要仍屬同一 session、review identity 與證據資產相容，可直接匯入；升版後會保留相容的舊 expected fingerprint。
- occurrence-scoped actual 修正、expected 補建、人工/GPT 事件會沿用；只要 occurrence/review identity 仍能定位到同一 PDF 位置。
- 仍保留真正資料錯置防護：PDF bytes 不同、occurrence/review ID 無法對應、核心資料檔 bytes 改變、檔案損毀或資料布局真的不相容時，才重算受影響部分或阻擋。
- 不提供一次性遷移版；正常升級程式後直接開原本校對專案即可。

一、一般校對流程
1. 第一次處理一本教材：選「教材 PDF／資料夾」與「校對專案資料夾」，按「開始新校對」。
2. 之後回到同一個「校對專案資料夾」，直接按「繼續校對」。不要為了繼續人工確認而重跑完整流程。
3. 人工確認畫面只顯示校對需要的資訊：課本頁、詞語／局部詞境、所在句、目標字、課本目前注音、應標注音與問題原因。occurrence_id、review_id、state、hash 等技術資料預設隱藏在「更多…→查看／隱藏技術資訊」。
4. 人工處理後或由 v5.6.1 升級時，按主畫面的「修復／更新報告」。程式會先驗證相同 PDF 與 actual 證據；相容時直接 reuse actual，只更新候選、完成門檻與 Excel 報告。
5. 「查看報告」開啟日常使用的「注音校對_最終報告.xlsx」；「更多…→開啟技術稽核報告」才會開啟完整治理資料。
6. 主畫面的詳細「執行紀錄」預設隱藏；平常只顯示一行進度，需要除錯時再從「更多…」展開。

二、人工確認畫面的按鈕與處理順序
1. 第一組「應標注音待確認」先處理，第二組「課本目前注音待辨識」接續；其他差異與完整性阻擋仍保留入口。畫面顯示目前組別與剩餘筆數，組內依教材、頁面、出現位置排列。
   - 同筆兩邊都未決時，先輸入應標；保存後再進 actual 組，剛才的應標判定會保留。
   - 保存後留在本組處理下一筆，組內完成才接續下一組。組別完成不代表全冊完成。
2. 看過原文與語境，確認有效的目前注音就是本句應標時，按「確認目前注音就是應標注音」。
   - 一次點擊就保存本筆人工判定並前進，不重輸、不必填依據、不再第二次確認。
   - 目前注音尚未確定、無效或原頁讀取失敗時，快捷不可執行。沒按下按鈕就不會產生判定。
   - 此判定只適用本位置，保存當次讀音、項目識別、語境、操作與時間。日後 actual 改變會重新比較，不改寫保存的 expected。
3. 「輸入其他應標注音」：輸入合法注音後按「儲存本筆應標判定」。依據（選填）可以空白或只有空白字元；原文與位置由程式保留，不需填入代用或虛構來源。
   - 文字依據與人工操作紀錄分開保存；重新開啟、重播、更新報表、actual refresh 與完成度計算均保留本筆判定。
4. 「稍後處理」不作正誤結論，移至本輪稍後清單；按「重新查看稍後處理（N）」可再次處理。走完本輪不會自動循環或把稍後項目視為完成，重新開啟仍可看到未決項目。
5. 兩邊都確定但不同時，保留「確認教材錯誤」六閘門與「課本其實正確」重新輸入應標的流程，不自動確認教材錯誤。
6. 「更多…」保留此處不需校對、實際注音辨識有誤、撤銷本筆人工判定、技術資訊、更新 Excel 報告，並提供「另行建立上筆應標可重用規則…」。
   - 排除仍必須有可稽核原頁或結構證據，完整性阻擋仍須修復。
7. actual 暫存與批次套用維持原流程。
   - 原頁 actual 視窗不提供 expected／字典答案。只有勾選直接核對的位置會 staged；未 checked 的同字形位置仍待處理。
   - staged 只暫時隱藏第二組的該位置；若 expected 尚未確定，第一組仍會顯示。暫存不是正式套用或 completion。
   - 按「套用 actual 修正（N）」才批次交易寫入正式 actual evidence、撤銷依賴舊 actual 的差異確認並增量更新完整 session。獨立 expected 判定持續保留。
   - N 是 staged exact group 數。未受影響 PDF 依既有 fingerprint/cache 沿用；Excel 可在完成一批後再更新。

三、六閘門仍然存在，但操作簡化
確認教材錯誤時，同一個視窗會要求一次確認：
- 原頁 actual 已確認。
- expected 現行來源已確認。
- actual 與 expected 證據鏈互相獨立。
- 完整詞語、所在句與目標位置已確認。
- 公司規定／來源優先序已確認且無更高優先衝突。
- 人員本人確認為教材錯誤。

六項全部通過才會成為 TEXTBOOK_ERROR_CONFIRMED。確認紀錄由程式自動產生基本內容；需要時才補充備註。

四、actual 與 expected 的安全原則
- actual 只能來自 PDF 字型／字形解碼與 occurrence-specific 原頁 visual override。
- expected 只能來自公司現行規定、統一用字手冊、教育部《國語辭典簡編本》、一字多音字典與安全規則／人工現版證據。
- 每筆 occurrence 分別保存 actual_status 與 expected_status；其中一邊未決，不得抹掉另一邊已建立的證據。
- expected resolver 對每筆 occurrence 都執行，即使 actual 尚未解碼；actual 不得決定 expected 是否要解析，也不得決定 expected 要選哪個讀音。
- 多讀音「允許集合」本身就是 expected_set；比較時只做 actual ∈ expected_set，不再因 actual 恰好落在集合外才反向建立 expected。
- 自動流程不得用 actual 反推 expected，也不得為了讓兩者相等而改其中一方。使用者主動看過原文語境後按快捷，是另存本位置的人為判定，不是自動 resolver 推論；不改 actual、不建立 Global truth、quorum 或自動核准。
- 歷史判定只供回歸與定位，不得代替現版證據。
- RULE_CONFLICT、EXPECTED_AMBIGUOUS、未建立 expected、未解碼都不是「正確」。

五、Excel 報告改為前台／後台分離
1. 「注音校對_最終報告.xlsx」：日常校對工作報告，只保留四頁。
   - 校對摘要：目前狀態、可見注音數、已確認正確、教材錯誤、待確認、actual／expected 覆蓋率、內部檢查是否通過。
   - 修正清單：只列真正需要修稿的 TEXTBOOK_ERROR_CONFIRMED。
   - 待確認：只列尚未結案的位置，使用一般中文說明問題與建議操作。
   - 人工與公司規則：保留人工補建／解衝突／確認錯誤／排除，以及已核准的可重用 expected 規則。

2. 「注音校對_技術稽核.xlsx」：保留完整治理資訊。
   - Occurrence Ledger、所有互斥 state views、集合對帳、檔案統計、資料來源稽核、回歸摘要、fingerprint／技術執行資訊等。
   - 一般校對不需要開啟；開發、驗收或除錯時才使用。

六、可重用 expected 規則（可選）
- 先保存本筆應標，再由「更多…→另行建立上筆應標可重用規則…」明確操作；不自動啟用。規則所需完整詞與真實來源依據不得阻擋或撤銷本筆判定。
- 規則只套用「相同完整詞＋相同目標位置」，不會擴張成單字通用音。
- 規則必須有明確 expected 與獨立來源；actual 不參與規則內容。
- 規則儲存在「可重用expected規則.json」。更新報告／建立新工作階段時，會把當下規則 snapshot 寫入工作階段，確保之後可重現。
- 若相同條件出現互相衝突的規則，程式會退回「規則衝突」，不會強行選音。

七、GPT／人工 actual 雙軌處理
- actual 待判定與 expected／差異判定是兩份完全不同的證據包，不可混用。
- 「更多…→輸出 actual 待判定給 GPT」會依 exact glyph 分組輸出 actual待判定_GPT包.zip。表格不含 expected、應標讀音或辭典答案；圖片先提供 annotation-only 裁圖，再提供 whole-glyph 定位圖，降低用中文字義反推 actual 的風險。
- GPT 回填 VERIFIED 時，多 occurrence exact glyph 必須同時核對 A、B 兩個位置；圖片不一致就填 UNRESOLVED。任一 group/session/snapshot/真正不相容的 schema/注音格式不合法，整批拒絕；單純工具版本不同不再拒絕。
- 「更多…→匯入 GPT actual 判定結果」驗證成功後，會寫入與人工 GUI 相同的 verified actual evidence，再自動重新解碼專案。GPT 不能直接指定 PASS／教材錯誤，也不能修改 expected。
- 少量 actual 問題可直接在人工校對畫面按「實際注音辨識有誤／待建立」處理，不需要先匯出給 GPT。人工與 GPT 兩條入口最後共用同一 actual 證據格式。
- TTF 可重用學習鍵只接受 raw glyf SHA-256 exact match。第一個核對位置只保存 USER_VERIFIED_SINGLE；至少兩個獨立 occurrence 同音才升格 VERIFIED_EXACT_GLYPH。
- v5.5.1 起，occurrence-local visual actual 修正與 reusable glyph truth 升格分成兩層交易：即使全域 exact glyph 發生讀音衝突，已直接覆核的 occurrence 修正仍會寫入，不再因 promotion 失敗整批回滾。
- 同一 exact glyph SHA 出現互斥直接視覺讀音時，程式建立 GLYPH_TRUTH_CONFLICT，將該 SHA 放入 glyph_truth_conflicts.csv 隔離。隔離中的 SHA 不得作為內建／使用者／shape-auto 的跨字型 reusable donor。
- 發生 GLYPH_TRUTH_CONFLICT 時，只保留有直接視覺證據的 occurrence override；先前因全域 promotion 擴散到其他同 SHA occurrence 的位置限定覆寫會撤回，這些位置會依 fingerprint 重新解碼，必要時重新進 actual 待判定。
- glyph_truth_provenance.csv 保存 promotion、occurrence override 與 conflict 事件來源，便於追查「哪一次 visual evidence 讓某個 SHA 取得／失去全域資格」。
- CFF 可重用學習鍵使用「完整 CFF 整字字形 SHA-256」，不以注音 component/full-annotation signature 單獨升格。不同完整 glyph 即使共享相同注音 component signature，也不得互相傳播人工/GPT correction。
- occurrence-specific override 仍保留處理特殊個案；它不會因 expected 相同而自動建立。
- v5.5.0 起，manual_actual_occurrence_overrides.csv、user_verified_glyf_fingerprints.csv、user_verified_cff_glyph_fingerprints.csv 都存放在「校對專案資料夾/_專案證據/actual/」，不再以程式安裝資料夾作唯一真值來源。換新版程式不會因此遺失專案 actual 證據，也不會讓不同教材專案互相污染。
- 這些動態 actual 證據與 glyph_truth_conflicts.csv 仍納入每 PDF scoped actual fingerprint；只有真正依賴該 occurrence／exact glyph 的 PDF cache 會失效。

八、expected／差異 GPT 證據表
- 「更多…→輸出 expected／差異 GPT 證據表」使用待判定候選_給GPT.xlsx。
- v5.5.0 的 expected GPT 表只輸出 expected 未決／歧義／衝突，以及真正的 actual≠expected 差異；「actual 未解但 expected 已解」不再重複出現在 expected 表，改由 actual GPT 包單獨處理。
- session_id、occurrence_id、review_id、review_snapshot、內部 state 等機器欄位預設隱藏，但仍保留供交易式匯入驗證。
- RULE_CONFLICT 可用「解決expected證據」提交 expected、來源與詞境；不能直接填「課本正確」。
- 任一列 ID、state、snapshot、真正不相容的 schema 或證據不合法時，整批拒絕且不寫入；單純工具版本不同不算錯誤。

九、完成狀態
- 「處理結束」不等於「校對完成」。
- 只有 actual 100%、expected 100%、非終態 0、解碼錯誤 0、規則衝突 0、mandatory／歷史 regression 全部實際執行且 0 失敗，以及全量集合對帳成立，才可顯示「全冊注音校對完成」。

十、v5.5.0 工作階段與舊版相容性
- v5.5.0 將 ledger/session/workbook schema 升為 2.6。因 authoritative state 改成 actual_status＋expected_status 的機械投影，v5.4.x 與更舊 session 不會被靜默升格成 v5.5.0 完成狀態。
- 舊專案仍可保留作稽核；要使用 v5.5.0 架構，請以現版重新建立工作階段。review identity 算法本身仍維持 occurrence-based 2.5，避免同一 PDF occurrence 無故換 ID。
- v5.5.0 的 GPT expected workbook metadata 會封存 expected_asset_fingerprint；expected action 綁定 PDF／頁碼／字元／所在行／局部詞境，而不是綁定 mutable actual 或自動 resolver 當下輸出。
- GPT 單檔判定包先 preflight expected 目標與證據格式，再寫 actual；降低 actual 已提交後才發現 expected 檔本來就無效的半套狀態。

十一、單 PDF／依賴範圍增量 actual 更新
- 人工 batch 正式套用或匯入 GPT actual 證據後，不再因動態 actual 資料庫任何一列改變就整冊 20 個 PDF 全部重解。程式會先判斷哪些 PDF 真正依賴被修改的 occurrence／exact glyph，只讓這些 PDF 的 actual cache 失效；單筆人工 staging 本身不會使 cache 失效。
- occurrence-specific override：原則上只重新解碼該 occurrence 所在 PDF。
- TTF exact glyph truth：只重新解碼實際含有該 raw-glyf SHA-256 的 PDF；若同一 glyph 同時出現在多個 PDF，這些依賴 PDF 都會更新。
- CFF exact glyph truth：只重新解碼實際含有相同 style group＋完整 CFF glyph SHA-256 的 PDF。
- decoder 程式、靜態 actual 對照表或 fingerprint schema 改版屬全域語義變更，仍必須 fail closed 使相關 actual cache 全部失效；增量機制不會用來掩蓋程式版本變更。
- CFF 跨檔證據仍會讀取全冊既有 actual workbook，但只改寫受影響工作簿，確保跨檔證據能力保留而未受影響 XLSX 不被無意重存。
- actual 未變且 expected fingerprint 未變的 PDF，可直接沿用既有 candidate workbook，不必重新跑 expected resolver。
- 人工 actual batch 採「一次快速更新」：正式套用整批後更新受影響 PDF、ledger、pending 與 completion gate；大型 Excel 報告預設延後。建議連續暫存多筆後一次套用，再按一次「更新 Excel 報告」，避免每一筆都花時間更新專案。
- 人工 actual batch 有硬性驗收：更新完成後，新 ledger 中所有直接 checked occurrences 的 canonical actual 必須等於各自 staged 讀音。若任一筆 evidence 沒有真正生效，程式會報錯，不會把整批誤報成成功。
- 若 actual 已正確套用，但該筆因 expected 未決、規則衝突或 actual≠expected 的獨立問題仍須處理，GUI 會明確顯示剩餘原因；此時留下該筆是狀態正確，而不是 actual 更新失敗。

