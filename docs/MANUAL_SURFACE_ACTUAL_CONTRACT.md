# 單一位置人工表面 actual 契約

PR58 corrective 15 保留 baseline `02ac4331e49a25feba3cd43884219ee2402d6e17`、C1–C14 全部失敗及原 BLOCKED。使用者明確撤除「未修改舊程式在所有狀態都會寫前拒絕」要求。此契約只支援通過本次驗收、實作 `manual-surface-actual/1` capability 的新版 writer；版本字串 `5.8.0` 本身不是 capability。舊版不支援開啟或回寫新格式專案。支援政策不能技術阻止舊程式直接寫入。開啟新格式及明確轉換時，GUI 均顯示這項限制。

## 格式與證據

既有 `_專案證據/actual/manual_surface_actual.json` 位置使用版本化 document，欄位為 `schema/session_id/revision/records/pending/history`。每個 current record 只對應一個既有 occurrence/review。替代已套用讀音保留舊 record 至 history；重複同 proof、同 reading 的已套用紀錄不改 bytes，不增加 quorum。pending 只表示暫存意圖。

`surface-visual-proof/1` 綁定 PDF SHA、session、原 occurrence/review IDs、原頁與完整 source tuple、native trace/paint/drawing/bboxlog composition、來源 draw/ordinal、完整 native contour coverage、來源在 final render 的非零 contribution、四種 replay hash，以及固定參數的原頁與目標 PNG。未知 composition、slot contradiction、缺少／錯誤 tuple 或完全遮蔽均拒絕。僅有非白像素不足以通過。機器證明來源、位置與 render，不宣稱能辨識完整中文字或注音。

人工必須另行明示整個 final unit 清楚可讀、目標字一致、讀音依原頁自行輸入，並保存 actor、帶時區時間及 confirmation receipt。輸入初始為空白，沒有 expected 預填或推導。自動解碼原值及 raw overpaint 警示保留；人工 surface evidence 永遠不是 raw glyph donor，不建立 glyph learning、quorum 或 Global intent。

封印工作階段中的 required capability marker、`actual-surface-projection/1` 與 raw-input origin 共用 `manual_surface_actual.py` 的 producer/comparator 契約。`origin_manifest_sha256` 保留明確轉換前的原封印供 audit；後續 refresh 固定沿用此 reference，不要求它等於每次的新封印。新格式要求 DB、五份原 actual CSV 及 surface store 均存在，不因刪檔回退 legacy。合法 legacy 的 optional/ABSENT 讀取契約保留；明確轉換入口拒絕不完整 legacy 輸入，使用者須先透過原合法流程準備完整專案。一般開啟、保存及 retry 不進行轉換或補檔。原 PDF 驗收只轉換隔離副本。

依本次「舊資料沿用既有合法讀取契約」及「缺檔、損壞、未知版本或來源歧義不得自動補成合法專案」的適用範圍，pure readonly `collect_manifest` 仍可核對舊 raw/candidate cache；新版不承諾同目錄 nonempty／缺 manifest 的 cold 自動寫回。舊 `run_pipeline_pdfs` 在 complete-stage trigger 後會嘗試 collect，但在其權威驗證前已可能寫 blocked/progress、mkdir 及 seed/補 CSV。新版 public cold 入口在這些第一次寫入之前拒絕，完整保留既存檔案，不能只因 workbook 齊全就當作 fresh 或遺失專案的修復授權。這項 writer 行為差異不能泛稱保留所有舊 writer contracts，也不是人類另採納的一條 cold-specific 條款；它是協調者依本次原文所作的適用解讀，仍由兩輪獨立 reviewer 評估。若發現明確更高的完整 cold 成功寫回承諾，先回報，不修改原 assert 或增加放行例外。

## 保存、依賴與恢復

public surface/service 入口使用 `project_write_admission.capture_project_write_admission`，在第一個 lock/mkdir/journal/status 寫入前，只讀核對版本、來源選擇與 SHA、workbook seals、DB、新格式必要 CSV/proof、journal/publication owner。receipt 綁定 bytes 與合法 absence，直接傳至原 project lock，在建立 lock 前及鎖內比較同一份 receipt。缺檔、損壞、未知版本、錯身分、來源歧義與寫前漂移均不補成合法狀態。共用低層 hook、報告與原 local recovery 依有限 new-profile indicators 才套用本次新增的完整 project admission；legacy offline、partial source-context 與 DB 初始化沿用原合法契約與原負向驗證。indicators 包括 marker、projection/origin、store、publication 及 journal `/2`；刪除 marker 或 store 不能藉其餘仍存在的證據降格 legacy。bare program/user evidence root 不因資料夾名稱變成 project。

沿用 `ReviewSaveService` 的 capture、anchor、cache 與寫前完整比對。capture 包含整份 store 的 bytes；frozen anchor 只包含 applied record 的 logical digest，因此 pending-only 暫存不阻擋獨立 expected 保存。applied record 變動需經封印 publication 重新建立 anchor。expected resolver、expected fingerprint 與 identity 不變；壞 surface store 阻止 project 寫入，不是跳過 expected resolver 的理由。

surface projection refresh 在讀取 manifest、來源及晚取的發布前像後，仍比較最初的同一份 receipt，且在建立 owned stage 前拒絕有效新封印或有效 CSV 的外部漂移；不把晚讀到的合法值當作新 baseline。原 publisher 的首個 guard 再比對此 receipt，早於 previous backup 與 project publication journal 寫入。合法 PREPARED publication 先驗 owner/preimages，鎖內比較原 receipt，再由既有 owner 回滾；只有其 owned entries 回到原 bytes、publication 消失且其他已捕捉來源完全不變，才以確定的自身 outputs 推進 phase。回滾後的旁支漂移不能被重新接納，自己的合法 publication 完成後也不再拿未更新的舊 receipt 比較。

C16 保存 C15 R1 已確認的 stage post-read 漂移 BLOCKED 及原重現，不以先前35項短測追認通過。`load_snapshot` 將 document 與同一次讀取的原 bytes 一起回傳；stage/apply 讀取時立即與原 admission 的 store bytes 精確比對，不能只在最後比磁碟、卻已使用暫時漂移後又還原的另一份 document。dynamic capture 也使用此同源 snapshot，不再分別讀 document 與 physical bytes。projection publisher 驗證 caller document 等於已准入 store；明確轉換只允許該 session 的空初始 document。

stage 在既有 atomic helper 建立 temporary file 前與 replace 前，均核原 receipt 及 expected store bytes；surface-only apply 在原 local transaction 第一筆 PREPARED journal 前核原 receipt 與其 captured store preimage，自己的 journal 寫入後仍保護其餘原資料及 store bytes。這是 project lock 下的 optimistic prewrite comparison 與 atomic replacement，並非作業系統提供 atomic compare-and-swap；不宣稱能排除非合作外部 writer 在最後比較與 replace 之間的無限小窗口。

明確 opt-in 需接續 stage 時，由同一既有 publisher 在發布前保存自身確切 staged output bytes，完成後與原 receipt 加上這些 owned outputs 比對；共同欄位必須精確相同，前後均重核，不能在驗完自身轉換後無條件採納較晚外部狀態。local PREPARED 回滾亦只容許原 journal preimages 與 journal/staging 的指定狀態轉換，無關來源不變才可接續。原 `load` 的唯讀回傳契約、普通 transaction/atomic helper 缺省行為、schema/epochs/identity/expected/Global 均不變。

surface-only 的完整 pending→applied 純轉換由 producer 與恢復共用，沒有新 actor/time。有限歷史契約命名為 `surface-only-all-pending/1`：只適用 profile1、journal2、合法原 session/origin/owner、operation=surface-only、非空原 pending。新 writer 在第一筆 PREPARED journal 已保存完整 plan；affected OIDs、reading、group 必須精確對應原全部 pending，不能接受 subset。C15 原 producer 的 PREPARED/plan=None 窗口只在上述有限契約內讀取，不視為任意操作的授權。

冷／熱 PREPARED 恢復只允許 store 為 exact原raw preimage，或由其全部 pending 唯一導出的 canonical afterimage；其他 tracked files 與 ordinary staging 必須仍是 exact preimage。foreign bytes 一律在恢復寫入前拒絕，保留 journal 與外部內容，不猜測為本次寫入。尚未嘗試 target replace 的 guard/temp open/write/fsync 失敗，既有 atomic helper 以具名例外保留這個事實；surface-only transaction 只有在自己的初始 PREPARED journal bytes 也完全未變時，才僅撤其 journal，不回寫未動的 store。replace 已嘗試或成功後的失敗必須通過上述 exact own-state 判定，不能靠例外類別盲目 rollback。一般 direct-visual／legacy transaction 不套用這個有限 surface-only 推導。

恢復實際使用的 journal raw／manifest／current file bytes 也取自同一 admission，避免先解析與後capture不相同的兩份資料；有效 journal plan 的完整對應檢查同樣適用 COMMITTED。合法自身 afterimage→preimage 還原沿用原 atomic helper 的可選 receipt guard，在 temporary output 前及 replace 前檢查，再以已知 preimage phase 重核後才移除 journal；不晚取外部值重設基準。I/O例外保留原 errno／filename 及原因，普通 caller 的缺省行為不變。

上述 surface-only PREPARED exact own-state 規則由唯讀 admission 與恢復共用；foreign store、CSV、staging、plan或owner在不存在 project lock 的冷入口也必須先拒絕，不能先建立 lock 才驗證歸屬。

dynamic actual snapshot 保存 store 的 physical bytes，per-PDF dependency 僅取該 PDF 已套用 receipt；其他 PDF 保留原 dependency mapping，pending 不成為 authoritative dependency。raw decoder epoch 不變：這是獨立人工 occurrence evidence 的 post-decoder projection，並未改 raw decoder 的 pronunciation 演算法。expected epoch 不變。

純 surface apply/refresh 使用既有 deferred publisher，保留原 raw workbook/epoch seal 作 audit，獨立封印 projection 與 per-PDF receipts，明列 `automatic_decoder_cache_revalidated=false`。它不重封舊 raw cache 為當前 epoch，不宣稱完整 current-decoder/completion gate PASS，也不 drain 既有 Global outbox。原 raw inputs 若改變，普通 refresh 必須走原 decoder 路徑；明確 `run_pipeline_pdfs` 仍遵守目前 decoder epoch，再重套獨立 surface evidence。

套用使用原 local project transaction；新格式 journal `/2` 在原有限 roster 加入 surface store、project/session/origin seal 及 operation owner；新 profile 拒絕無 owner 的舊 journal `/1`，legacy 無 profile 的 `/1` 契約保留。deferred publication `/2` 必須加入同一 store，保留原 root JSON/workbook 有限 allowlist、backup、CAS 與 acknowledgement 流程。合法 legacy publication `/1` 由原 reader 驗證；unknown future 或 surface entry 不降格。PREPARED 只依有效 owner/preimage 回滾；COMMITTED 依原 recovery plan 完成 projection，不重新套用 staging，也不交付 Global。受舊 actual 影響的 confirmation 仍由原 service 撤銷其結論並保留可獨立重播的 expected evidence。必要 proof 本身損壞／遺失時不靠 journal 幫它通過。

整份專案在保持原 session/IDs/PDF SHA 的搬移可重新解析來源；surface proof 不含機器絕對路徑。跨 project/session 的 actual transfer 不支援此 local contract，寫前拒絕；expected-only 入口沿用其獨立契約，不將 surface evidence 當作新 actual vote。

## 驗收與停止條件

C17 保留 C16 的四個已確認 lifecycle BLOCKED 原件：初次 PREPARED journal、body 後 journal、COMMITTED→publisher handoff，以及 publisher backup 後的合法外部漂移。C16 原服務驗收與短測不能清除這些 code findings。使用者後續限時授權延續 corrective 計數；以下 continuation 不修改儲存 schema、epoch、identity、expected 或 Global 契約。

本次 surface lifecycle 使用原 `ProjectWriteAdmission` 的 in-memory `SurfaceWritePhase`：A 是原 store bytes，C 是由 A 全部 pending 唯一導出的輸出；J0、後續 PREPARED、COMMITTED journal 及 publication 各階段均由原 receipt 與已知自身序列化輸出推進。public entry 的原 receipt 傳入內部 service，不以較晚的有效 capture 替換。journal preimages、ordinary staging、backup、stage DB/status、ack copy 實際使用的 buffer 直接來自此已知 phase；讀到暫時 B 而磁碟又還原 A，不能使 B 成為後續輸出依據。

原 atomic helpers 的 surface-only 可選 phase 在 temporary 寫入之前、fsync 後 replace 之前及 replace 後核對；初次及後續三次 local journal 寫入、store body、publication journal、previous backup、每筆 stage→project replace、ack copy、ack journal 與 unlink、owned cleanup 均延續同一 phase。成功的 local COMMITTED phase 直接交給 publisher 與 acknowledgement，不能在 handoff 重新接納另一個 transaction_id。冷恢復只在最初入口唯讀准入一次；PREPARED exact own rollback 後按確切已知 outputs 更新 phase，不重新 capture 外部資料。普通 helper caller 的缺省契約保留。

失敗只接受整筆 planned write 的 exact before 或 exact after；foreign bytes 不 rollback、不 merge、不 rebaseline、不 acknowledge。store 已 durable COMMITTED 後的 publication／ack 失敗維持 COMMITTED 與 refresh 失敗／待恢復狀態，保留原 journal；不能報 SUCCESS 或假稱沒有提交。負控從注入當時比較完整檔案集合，只允許清除實際 helper 已知的自身 temporary，不泛排 `.tmp`。這仍是合作鎖與 optimistic precondition，不是 OS atomic CAS；最末比較與 replace/unlink 之間對非合作 writer 的窗口不宣稱消失。

自身 COMMITTED replacement 已完成並推進 known phase 後，即使後續例外或 foreign journal 使原 transaction context 無法正常退出，結果仍以既有 `ManualActualPostApplyError` 回報 COMMITTED／refresh 未完成；不由目前磁碟或僅 attempted marker 猜測 durability。正常 publication／ack／rollback cleanup 繼續核完整 phase。若外部 project B 導致尚未發布的自身 stage 需要撤除，只有原 known publication 為 ABSENT、從未 attempt publication replacement、stage 完整檔案 roster/bytes 皆屬已知自身輸出且無 extra/links，才從這些原 bytes 與 publication ABSENT 導出窄 cleanup receipt。它只清自身未引用 stage，不觸碰 project B；曾發布後被外部刪 journal 不能套用此例外。

C18 保留 C17 兩個 genuine code BLOCKED：stage DB 曾實讀合法 B 後磁碟還原 A，pending/status卻消費B；nested opt-in則以內層新capture B替代外層原A。C17 的75項及環境/metadata成功不能清除這些原件。修正後，surface-only logical consumers 的實際 manifest/DB/staging/hash/token/plan buffer都從原 receipt或已知own outputs嚴格解析；stage DB initial與clear後兩讀、pending/reconciliation/status、publisher/rollback/ack postconditions均不再以live loader重取authority。原 `normalize_db`、staging/outbox/CSV validators繼續適用，不能以initializer補成合法。顯式conversion完整性由首次captured DB、五CSV、staging/outbox作只讀驗證，普通legacy讀取/ABSENT契約保持。

public enable與stage的opt-in使用同一private conversion body。直接enable取得自身原receipt後委派；stage則顯式傳已鎖內核對的outer A，不再呼叫第二個decorated public entry重新admit B。body將由A建立的phase明確交既有projection publisher，stage僅接續其已知outputs。ordinary apply dispatcher也只在受支援surface指標下，將其原receipt交同一private surface apply；legacy/raw預設路徑不因此改造成一般snapshot框架。合法冷publication `/2` 即使manifest仍是legacy，也須其原owner/session/source/stage receipt成立後沿同一phase恢復，不能以profile=False降格或補檔。

原五CSV raw dependency producer沿用相同檔名registry與SHA演算法，僅增加captured reader參數供surface phase使用；普通default仍讀原路徑。這是資料綁定修正，不改raw decoder／expected結果演算法或epoch。真實負控必須非空注入合法B：新consumer可證明實際仍消費原A並產生與final DB一致的pending/status，或在首次後續寫入前拒絕保全；不能以舊live-read hook未觸發宣稱拒寫。原script/失敗與新hook角色分別保留。C18 evidence recorder使用NUL分隔Git paths，涵蓋非ASCII原檔名；這不追認C17原253項capture涵蓋當時漏掉的11個tracked輸入。

必要定向回歸涵蓋原頁 proof/負向控制、blank human input、pending→expected save、apply→重開→refresh、其他位置／expected／DB 保全、required file 刪除／損壞／錯身分、語法有效寫前漂移、per-PDF cache、raw override 正常 refresh 分流、rollback、durable publication cleanup、pending recovery、重複保存與 provenance history。原 PDF/原九筆以隔離副本另驗；非視窗服務證據不能稱為人工 GUI 驗收。

新候選仍須兩輪完整獨立審查、固定候選一次當前非視窗全集及必要當次原件／CI，不能用 C13/H13 PASS 或舊版執行失敗代替。C15 原「不授權 C16」停止及其 BLOCKED 保留於歷史，後續 C16／C17 依使用者另行限時授權執行，不追認舊 PASS。新版寫前保全不足、未解交易／來源回歸、必須改 identity/expected/Global DB schema 或放寬傳播邊界時停止；限時授權仍受其收尾與失效時間限制。generic gate 的真實結果與全部歷史原文保留。
