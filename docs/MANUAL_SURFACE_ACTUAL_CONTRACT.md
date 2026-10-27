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

## C19：新格式專案的普通 actual 交易

H18 的兩項 code BLOCKED 保留：new-profile ordinary writer 未承接原 phase，會覆蓋另一個合法 transaction journal；普通 `/2` PREPARED cold rollback 也缺少可證 own afterimage，會覆蓋合法外部 override。既有 surface-only `/2` 的 deterministic all-pending 證明不能推廣為普通 learning／quorum／outbox 寫入的證明。C19 沿原 C14／C15 必要格式版本授權，在同一既有 local journal 使用有限 `/3.0` successor；不改 Global DB、occurrence/review identity、expected、learning/quorum 或 raw decoder epoch。

`/3.0` 僅供已啟用 surface profile 的 `owner.operation=direct-visual`。保留 `/2` 的 `state/transaction_id/recovery_plan/files/staging/owner`，七份 files roster 不變，另加必需 `write_plan`，其 exact fields 為 `contract/dependencies/afterimages/outbox_delivery`。contract 為 `new-profile-direct-write-plan/1`。dependencies 是原 receipt 中非本交易 body targets 的 canonical path→SHA256／JSON null（已證 absence）；afterimages 初為 null，準備完成後是 exact `files/staging` packed bytes。ordinary 的 surface-store afterimage 必須等於 preimage，不能用這份 journal 宣稱另一筆 surface store 是自身輸出。

最初 PREPARED 可以尚無 afterimages，此時尚未寫任何 body data。既有 `_PreparedActualCSV` 先在記憶體完成五 CSV 的結果、時間與 provenance，沿原 validators、來源／quorum／learning 規則產生。完整 recovery plan、七份 afterimages 及 staging afterimage 必須先寫入同一 owned PREPARED journal，才 flush CSV、enqueue outbox、ack staging，最後寫 COMMITTED。冷恢復不重新產生時間或 actor，也不由 expected 推導任何 actual。

最初提案曾以 null／單筆 record 表達 outbox delivery；實際 wire refinement 是有限 array，原提案與 refinement 原件分別保存。每筆 delivery 僅含 exact `before_sha256/afterimage`，SHA 必須接續上一份已知 outbox bytes；同一 intent roster/payload 不變，舊 DELIVERED receipt 不變，新增 receipt 依既有 validator，DELIVERED 數嚴格增加，array 長度不得超過原 PENDING 數。這是既有 subset acknowledgement 的有限狀態鏈，並非無界 log。Global 已完成的真 receipt 對應 D bytes 先 durable 於同一 owned COMMITTED journal，才回寫 outbox。重複 D 不增加紀錄；沒有 local journal 的獨立 retry 不補造 journal。

普通 PREPARED cold recovery 僅接受 exact 原像 A，或原 producer 已封存 A→C 寫入順序的合法前綴。rollback 逆序回 A；外部 B 一律拒寫並保留 B／journal，不合併、不追認、不晚 capture。COMMITTED 僅接受 C 或已封存的有限 outbox D。真 JC replace 已成功並推進 known phase 後的 handoff／cleanup／refresh 錯誤，保留既有 COMMITTED／refresh 未完成結果；不能因目前磁碟是另一個 COMMITTED journal 或只是 attempted write 而猜測本次成功。pending J3 尚未完成時，新 ordinary／surface staging 必須先拒絕，不能合法自身新增資料卻破壞原恢復依賴。

既存新格式 ordinary `/2` 沒有 afterimages，不自動升級：只有 PREPARED 且七檔與 staging 全部 exact preimages 的有限未寫 body 前綴可撤銷 journal；partial 或 COMMITTED 保留檔案並明確拒寫，需另有可驗解決方案。surface-only `/2` 與 bare／legacy `/1` 原契約保持。journal3／unknown successor 本身也是格式 indicator；缺 marker/store 不降格為 legacy。

新格式 ordinary raw refresh 仍呼叫原 producer、stage、publisher 與 ack。原 project、CSV、DB、journal 與來源持續原 phase；fresh stage 的 manifest／DB／surface／raw-dependency logical consumers 使用同份已知 buffers。新 workbook bytes 沿既有 producer 封印、來源、IDs、actual postconditions、expected 獨立性與完整 artifact 驗證，單次擷取其完整有限 output roster 後交原 publisher。僅本次明列 producer／seed path 及 exact bytes 可以清除未發布的自身暫存；未知 extra／links 拒絕，不清正式 project 或舊 stage。

ordinary publication 必須是既有 `/2`，包含 required surface-store entry。J3 的原 dependency SHA 與 publisher 的 token／plan digest／原 manifest seal／backup bytes 對應；合法 PREPARED old-MF/new-workbook 混合前綴及 COMMITTED→PUBLISHED→ACKNOWLEDGING 均沿同一規則，不能用舊 SHA 誤擋 own refresh，也不能把外部 B 當新基準。ACK copy 保存同一 J3；完整發布後才 acknowledgement。

workbook recovery binding 重用 `sealed_workbook_paths` 同一 role/path/PDF/唯一性 registry。新格式原 receipt 固定捕捉同 registry 的兩個 role namespace 及 bytes，暖重核也比較 roster，新增 XLSX／links 不能逃過原 files tuple。已嚴驗 publication `/2` 時，只有其 exact original backup bytes 可供原 MF 的 binding view；實體 current bytes 仍須獨立符合該 publication 的 old/new 與 J3 owner/plan/dependencies。這個 in-memory recovery view 不重封 manifest truth；整筆交易寫前 guard 也不改 per-PDF actual logical dependency 或 cache scope。

全部保護仍是 cooperative lock 與 optimistic precondition；不宣稱 compare→replace/unlink 是 OS atomic CAS。H18 原非視窗服務驗收、C19 定向測試與原件各保實際 source／HEAD，不能互相改標新候選執行，也不能取代兩輪完整獨立 review 或正式當次全集。

普通 J3 owned ack 完成後仍沿原 `_publish_local_actual_conflict_resolution`：只在原 Excel actual receipt 的位置／來源／token、當前 override、provenance 與讀音均符合，且不再有 pending actual transaction 時，重新發布報告以清除原 hold。actual／expected conflict receipt 的 bytes／ABSENCE 在最初 admission 捕捉，非空 receipt 沿原 validator 同份 buffer 驗 schema／identity／source，損壞不能等到建立 lock 才發現；有合法 pending 的 receipt 不等於資料損壞，不阻止其 own recovery。notes、completion hold 與 expected receipt 仍用原規則，captured reader 未列路徑必須拒絕。

這個後置報告是既有下一 operation，不是新增 J3 transaction。new-profile `regenerate_report` 仍執行原逐 PDF fingerprint、dynamic/global snapshot、workbook binding 與完整 collector 的只讀 cache 驗證；結果不符原 sealed raw records 時拒絕並要求正常 actual refresh，不把 stale cache 或歷史 epoch 認成 current。成立時使用原 sealed manifest／DB，保留完整 surface projection、origin、raw-completion hold、identity 與 expected；不將報告收集器的新 manifest 取代原 truth。legacy 的原 collect／write 路徑保留。

new-profile 報告的兩個原 Workbook 都先序列化至記憶體；兩者成功且同一已知 receipt 重核後，才沿既有 atomic helper 寫 technical、user report，最後寫 status。所有 target 原 bytes／合法 absence 都已捕捉。handled I/O 失敗只在 exact own-prefix 下逆序還原；foreign source／target 保留並拒絕 rollback。這不提供三檔 crash atomicity 或新 journal，亦不宣稱 OS CAS。manifest／DB／J 不因這個報告步驟改寫；來源或 cache 拒絕不先寫 error status。原 report producer、actual／expected gates 與 validator 語義不改。

publication 仍 pending 時，new-profile 的 Global retry／ack 不先改 outbox，避免讓 publication 綁定的 C 失效。已完整 PUBLISHED 的 owner 先完成自身 ack／cleanup，Global 未完成則維持真實 PENDING_RETRY；之後獨立 retry 以全新入口取得第一份只讀 receipt，在沒有 journal／publication 的狀態沿原單檔 outbox service 寫 D，不補造舊 J3。已有真 processed receipt 的重試仍保持 idempotent，不新增 quorum。這與 publication 之前、同一 J3 封存有限 C→D 的路徑分開驗證。

C19 namespace continuation: explicit conversion and validated publication/2 with a legacy manifest capture the same shared workbook-role namespace and runtime inputs as enabled surface projects. A non-null captured namespace is carried unchanged to the first project lock. Ordinary report/refresh entries refuse transitional legacy-manifest publication before locking and direct the caller to the existing recover_committed_actual_project owner; only that owner and explicit conversion may continue the transition. Legacy ordinary inputs without this namespace retain their default contract.

C20 ordinary capability: original receipts remain valid for their named owner only. A legacy-manifest publication/2 receipt or phase cannot authorize any ordinary writer, including supplied and entered phases. Complete explicit owner recovery, then begin a separate ordinary operation. Cold ordinary recovery retains its validated COMMITTED token/plan across owned journal/publication acknowledgement; subsequent report failure remains a typed committed failure, never a reconstructed journal or a guessed disk commit.
