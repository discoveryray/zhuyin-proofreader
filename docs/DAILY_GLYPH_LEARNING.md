# 日常校對整合字形學習

本文件描述本任務的開發候選；正式驗證與人工介面驗收尚未完成，不表示日常版本已替換或功能已正式上線。

## 看圖確認

「新字形待確認」從完整 actual 結果收集候選，包含目前 actual 與 expected 相同的項目。候選的讀音只取 actual，不用 expected、字典或語境證明辨識正確。同組依既有 exact glyph identity 建立，同一中文字、字型名稱或讀音不代表同一字形。

清單顯示來源 PDF、頁碼、原頁局部圖與程式辨識注音。先查看要確認的位置，再準備當次保存資料；準備完成後，在同一個看圖介面選擇：

- **辨識正確並記住**：明確確認畫面上的程式辨識注音。
- **修改圖片上的注音並記住**：依原頁輸入真正印出的注音，再明確保存。
- **稍後處理**：暫時略過學習，沒有新增確認證據，也不清除教材原有待辦。

保存操作同時表示「保存本次目視確認，且在既有獨立來源條件成立時，允許這份固定證據供其他教材使用」。不需另外開啟文字核准視窗。變更讀音、選定樣本、圖片或來源後，必須重新準備；先前點擊不能授權後來改變的證據。

同組只有實際看過並明確確認的樣本會建立直接人工證據。圖片載入失敗、原 PDF 改變、目標位置無法核實時不能保存。原頁有疊改、遮蓋或不符合全域 donor 資格時，依既有 PR58 原頁確認流程保存位置限定成果，不把最終畫面的讀音當成底層字形的全域讀音。

## 保存、啟用與重試

第一次有效確認會保存成果；第二個同讀音來源同時滿足 project/session、PDF SHA、font-program SHA、occurrence 與 review 的全部獨立性條件後，才可啟用跨教材重用。複製、改名、重跑、傳播或增加相同來源的樣本不能製造第二個獨立來源。已有有效結果或無法增加獨立性的一致重複資料，不反覆列為學習問題。

介面區分本地保存、Global 交付／核准待完成及已可重用。保存失敗不跳到下一筆。本地已提交而 Global 或 refresh 尚未完成時，保留原操作及既有恢復資訊；重開與重試只重送原固定請求，不重新計數，也不擅自核准新的證據集合。核准資料過期時，保留已保存的本位置成果，重新看圖確認後才能建立新操作。

若本地已保存但重新整理失敗，可用「接續已保存字形確認」讀取原固定操作；此入口在一般校對畫面無法載入時仍可使用。恢復成功後重新載入教材，不能利用恢復操作核准後來改變的資料。

專案搬移或改名後，學習紀錄以目前選定位置的 sealed session、完整 PDF SHA roster、workbook 封印及原生位置重新驗證；舊目錄只保留為歷史 locator，決策 bytes、operation ID、證據 hash 與原核准請求不改寫。單筆無法驗證的紀錄列為 `UNRECOVERABLE`，不使其餘可讀候選整批失敗。

已啟用 `manual-surface-actual/1` 的專案若留下未完成的 owned transaction journal `/2` 或 `/3`，恢復時以共同的唯讀 runtime relocation binding 核實原 owner/session/manifest、完整 PDF/native occurrence/review、workbook role/relative/SHA 及交易依賴。入場、鎖內重驗、rollback、delivery、ack 與 refresh 沿用這份綁定；歷史 owner、dependency keys、決策、operation ID、證據與核准不因搬移改寫。原 PREPARED 合法前綴及 COMMITTED/publication 的逐 byte/hash 檢查仍適用；未知、遺失、碰撞、連結或 foreign bytes 拒絕，不讀寫舊專案目錄。

搬移最小集合包含完整專案，以及既有來源機制能核實的 PDF。若已有 `actual_refresh_publication.json`，還必須保留它明確登記、位於專案同層的 `.ar-XXXXXXXX` 暫存目錄；其中 `previous` 與 `ack-committed.json` 是尚未結束交易的必要恢復證據。跨資料夾搬移時需連同該目錄保留在新專案同層；同層改名不需搬它。只搬專案而遺失此目錄、同名目錄含不同 bytes 或證據有連結時會拒絕恢復，不回舊位置搜尋，也不重建缺失原件。這項操作要求不是全機掃描或正式資料遷移。

搬移後可直接使用原本的「套用 actual」或學習重試入口，不需先另行呼叫低階 rollback。若原交易停在 PREPARED，同一次操作先完整還原原 beforeimages/staging，確認原 journal 已由該 phase 正常移除、沒有 publication 殘留及外來 bytes，再結束該交易的 owner/token authority。選定目錄與來源防護仍保留，新交易依原流程進行；固定學習決策、operation ID 與核准不因此重製。任何 handoff 邊界資料變動仍拒絕繼續。

合法 PREPARED 的「尚無 recovery plan」以明確 `None` 保留，與已有計畫的 hash 分開驗證；入場與 rollback authority 交接使用相同表示，不補空字典或假計畫。此狀態只沿用既有 journal `/2`、初始 `/3` 及有限 C15 surface 恢復契約；缺少確切 afterimages 的 legacy partial 仍拒絕，COMMITTED 與 publication 仍必須有有效計畫。這不變更 journal 原件、schema 或全域 hash helper。

已套用的 surface 確認每次收集候選仍依原嚴格來源／render proof 驗證；只有相同 occurrence、review、讀音及 receipt 的有效位置不再詢問，未確認的同字形位置仍保留。失效證據不算完成，正常「修改 actual」入口仍可重新確認本位置，surface 永遠不增加 Global donor 或獨立來源票數。

等待第二來源屬於學習進度，不加入教材完成條件。教材是否完成仍由原本的 actual、expected、差異確認與完整性門檻判定。

## 衝突與已知影響

機器辨識結果與既有全域讀音不同時，只列為待人工確認。有效人工確認與既有讀音矛盾時，沿用原 quarantine、撤銷核准及保留歷史流程；本位置人工修正仍保留。不自動裁決或解除衝突。

影響清單只包含明確登記的使用專案，依既有 per-PDF exact dependency 查詢；不掃描電腦，也不宣稱涵蓋未登記的舊專案。遺失、變動或無法核實的專案須顯示診斷。未開啟的專案不在背景重寫；開啟時重新核對相關 actual 依賴，必要時更新 actual，保留獨立人工 expected。

## 證據與相容邊界

候選、使用索引與圖片快取均不是 actual truth、quorum 或 approval。直接人工證據仍走既有 project transaction、outbox、Global receipt、ack 與重用檢查；核准仍走既有 explicit approval service，綁定固定讀音、完整 evidence roster、policy 與 revision。

核准預覽使用唯讀 Global snapshot 的私有暫存副本，執行同一套原有 delivery 與 approval preview 邏輯。正式 Global 在預覽期間不被初始化或修改。真正保存時，原有 CAS 與全證據集合檢查會拒絕已變動的請求。

原 PDF 只保留來源引用，不複製整冊。新學習確認圖按內容 SHA 去重，必要證據與可重建快取分開；快取預設上限 256 MiB，清除快取不刪必要證據。既有 `manual-surface-actual/1` 的內嵌圖片、證據原件與 hash 契約維持原義，不遷移或重寫。

真實 worker 準備階段使用每個 worker 預設上限 256 MiB、依圖片 SHA 去重的記憶體暫存。取消、重新準備、部分準備失敗或重開會釋放未確認圖片，超限則拒絕整次準備。最後按「並記住」並重新通過來源檢查後，才把原準備圖片的相同 bytes 保存為必要證據；提交不重新渲染替換圖片。圖片與決策發布共用圖片鎖；發布前失敗只回收本操作新建且未引用的圖，共用既有圖與已發布、待恢復操作的圖保留。既有正式資料不批次清理。

新學習圖片預設放在 `%LOCALAPPDATA%\DiscoveryRay\ZhuyinProofreader\GlyphLearning\images\evidence\<sha256>.png`；可重建圖片另放同層 `cache`。已知專案使用索引位於 `GlyphLearning\usage.json`。這些是使用者資料，不放入 repository 的 `tmp`；測試另用隔離路徑。

本功能不修改既有 SQLite schema、occurrence/review identity、actual/expected fingerprint schema、semantics epoch、runtime assets 或通用審查政策。舊資料不批次追認為新的 direct evidence。

## 日後有限人工驗收

使用少量自行準備的隔離測試專案及獨立 Global 資料位置，不使用整批正式教材。人工操作前先確認測試版本、資料位置與備份。

1. 讓一份包含新字形的教材完成辨識，確認 actual＝expected 的字形仍出現在清單；同 exact 字形合併、同字不同 exact 字形分開。
2. 查看原頁局部圖並確認一個樣本，保存後關閉再開啟；核對本位置成果仍在、其他未看過的位置沒有冒充直接人工確認。
3. 用複製／改名教材確認不增加獨立來源數；再用符合全部獨立條件的第二來源確認相同讀音，核對顯示已可重用，第三份教材沿用既有 Global 重用路徑。
4. 改動來源 PDF 或讓圖片無法載入，確認按鈕不能保存。保存時連點或暫時鎖住隔離 Global，核對不跳筆、不漏本地成果，重試／重開不重複計數。
5. 以小型疊蓋範例確認位置限定成果不變成全域字形讀音；只改 expected 不應新增 actual 確認。
6. 對已啟用字形作有效人工相反讀音確認，核對隔離、撤銷核准及已登記教材影響清單。重開其中一份，核對 actual 依賴更新且人工 expected 保留；未開啟專案檔案不應被重寫。
7. 選「稍後處理」，核對原教材待辦仍在；僅等待第二來源時不應新增教材結案障礙。核對圖片去重與快取清除不刪有效確認證據、原 PDF 沒有被額外複製。
8. 在隔離資料複本搬移／改名已完成及待恢復專案，核對 journal `/2`、`/3` 原操作可依 PREPARED rollback 或 COMMITTED/refresh 分支接續、不增加票數、不建立舊目錄。已有 publication 時連同明確登記的同層 `.ar-XXXXXXXX` 必要證據搬移；另測缺失或遭改動的目錄會拒絕。再次中斷、移回與重試後，核對原決策、人工 expected 與票數不變。
9. 多次調整圖片尺寸並準備／取消，確認未確認圖不累積永久檔案；保存後重開，核對原圖仍可供恢復。疊蓋字形保存後重開，不應再次詢問已確認位置，但其他位置仍須確認。

以上是待執行人工驗收步驟；非視窗 handler 或服務測試不能代替原生介面的圖片、排版與點擊驗收。
