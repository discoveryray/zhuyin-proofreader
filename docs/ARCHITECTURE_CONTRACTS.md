# 產品架構與安全契約草案

<a id="ac-00"></a>
## AC-00 草案狀態與適用基準

本文件延續「文件體系重整第二階段」的產品架構與安全契約草案，整理既有產品契約的讀取入口與共同不變量。既有產品規範、技術附件及 validator 仍為權威；草案未採納為替代規範。本機整併不代表產品驗證、發布或資料遷移。

原草案來源為 branch `codex/integrated-review-improvement-20261105`、HEAD `596310ea97a4b1acd4c1e64b0643d0c05b6b082a`，該 commit 不含當時未追蹤的草案。原文件任務的 baseline／base 為 `8d08c5a05d8143c353fb47b9be4f33471d99ba10`，歷史分支 `codex/docs-contract-candidate-20261106` 保留候選 `5488101d68bfc796687f0954d51b3970f812746d`。本機整合比較點為已驗證的 PR #72 actual merge `9dd7f5ff3c64a0817435b75ebb2d0308d44f5778`。整合比較點不取代原 task baseline，也不是本文件的審查結論；本機候選依實際 Git object 與適用審查紀錄固定。

2026-11-06 階段曾修訂兩份草案及保存來源；該階段的授權和審查範圍保存在[歷史來源](#ac-source-custody)。本輪範圍是文件整併及必要文檢。當次使用者指示決定本輪範圍；草案摘要和歷史來源均不提供實作、schema／contract version／identity／SHA／semantics epoch 變更、資料操作或發布授權。

來源：[AGENTS.md](../AGENTS.md) §1、§10、§12、§16；[完整規範](V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md) §1–2；[原文件候選的來源索引與審查歷史](#ac-source-custody)。

<a id="ac-source-custody"></a>
### 歷史來源的固定定位

原任務的六份來源附件由原工作樹及 Git object `5488101d68bfc796687f0954d51b3970f812746d` 保存，路徑前綴為 `docs/evidence/document-system-restructure/`。同目錄 `README.md` 的 `#source-set` 列出附件的性質、原始位元組 SHA256 與閱讀版差別，`#candidate-scope`、`#history` 和 `#remaining-evidence` 記錄原範圍、原審查及當時的缺件。以下引用這些路徑時均指上述固定 Git object，相對於歷史工作樹讀取。六份原件保留於原候選；本機整合差異只引用定位與適用範圍。原件取得與公開適用各自的授權。

<a id="ac-01"></a>
## AC-01 產品契約與其他規範的分工

本文件整理產品功能與資料安全契約。開發、獨立審查、交接、驗證與合併先依唯一[流程入口](DEVELOPMENT_REVIEW.md#applicability)選定本 task 的適用版本，再依 [VALIDATION_POLICY.md](VALIDATION_POLICY.md) 及 [TESTING.md](../TESTING.md) 的專屬條款執行。執行授權以當次有效使用者指示為準；適用流程版本與產品契約分開判斷。

獨立技術附件保留原路徑，負責其精確格式、有限 adapter、來源證明與交易規則。本文件的摘要不提供額外相容 fallback，也不將附件內的原 task 授權、測試數字、舊 PASS、失敗或候選狀態提升為一般新要求。來源衝突時列出具體差異與受影響操作，保持原規範及 fail-closed，不以本草案自行裁決。

來源：[AGENTS.md](../AGENTS.md) §4、§16、§17；[完整規範](V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md) §2；[PR_REVIEW_GATE.md](PR_REVIEW_GATE.md)「信任邊界與留存」，依適用流程版本使用。

<a id="ac-02"></a>
## AC-02 Actual 與 expected 證據鏈獨立

Actual 只由符合既有契約的 PDF embedded font／glyph 解碼、已驗證 project/user actual evidence、occurrence-local 原頁人工 actual，以及允許的 exact reusable glyph truth 產生。Expected 只由專案規則、公司現行規範、《統一用字手冊》、《國語辭典簡編本》、一字多音資料、approved expected assets／rules 及其他明確允許的 expected evidence 產生。

禁止以 expected、字典、語意、詞性或中文字常識修 actual；禁止自動由 actual 選取或反推 expected；禁止為使兩邊相等改寫任一答案。PDF text layer、OCR、font name／index、語義或詞性若只具定位／候選角色，不得越權成為正式讀音來源。Actual 與 expected evidence 不得混入對方 fingerprint。

即使 actual unresolved、解碼失敗或完全缺少可用 actual，expected evidence 完整時仍必須獨立 resolve 並保存結果。Actual 的未決不能遮蔽 expected conflict 或成為跳過 resolver 的理由。重建、refresh 或 repair 其中一條 lane 時，不得刪除、覆寫、反推或靜默改變另一條有效證據。比較、候選、reconciliation 僅消費兩條 lane 各自獨立產生的結果。

Expected 來源優先序須依已採納的公司／專案來源及原 resolver 契約，不以本草案、歷史 actual、舊回標或外部通用語感重排。完整詞條證據與有限語境規則不得被單字／詞性捷徑取代。來源不足或矛盾維持未決／conflict。

來源：[AGENTS.md](../AGENTS.md) §1、§3；[完整規範](V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md) §3.1；[統一用字手冊規則來源](../統一用字手冊_規則來源.txt)「最高優先順序」「來源保真原則」。雙 lane 演進背景見 [v5.5.0 audit](archive/audits/IMPLEMENTATION_AUDIT_v5.5.0.md) §2–6；該 audit 的版本數字與驗證結果不轉成當前事實。

<a id="ac-03"></a>
## AC-03 本筆人工 expected 與確認模式的有限邊界

[AGENTS.md](../AGENTS.md) §3 的 actual→expected 禁令仍適用自動推導與改寫。使用者於 2026-11-06 明確裁定：[完整規範](V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md) §3.1 及 [EXPECTED_BUSINESS_CONFLICT_CONTRACT.md](EXPECTED_BUSINESS_CONFLICT_CONTRACT.md)「功能第 5 輪追加明確人工選擇」的本筆主動人工快捷，是該禁令下的明確有限例外；裁定範圍與來源見 [AC-P01](#ac-p01)。兩項技術條款界定此操作；缺失的歷史授權原文及核心草案採納狀態維持原紀錄。

依 2026-11-06 裁定及原契約的窄邊界，`CONFIRM_CURRENT_AS_EXPECTED` 快捷必須是使用者完成原有看圖、核對本筆原文／語境後主動操作；固定當次有效且已套用的選定讀音、嚴格本筆識別、語境、操作與時間；保存前依原 validator 核對 snapshot、sealed identity、來源及 receipt／CAS。此快捷不得使用未套用 staging、無效證據或缺少原判定紀錄補造人工 expected。獨立的 `ENTER_EXPECTED` 仍依原 validator、來源與文字依據選填契約保存，不因 actual unresolved 或尚待套用而停用；保存後仍按原流程等待 actual 套用／重新比較。來源為 [expected 衝突契約](EXPECTED_BUSINESS_CONFLICT_CONTRACT.md)「功能第 5 輪追加明確人工選擇」及 [使用指南](USER_GUIDE.md) §二的「保存應標時若 actual 尚待套用」。

這份本位置人工 expected 為固定的本筆人工判定；日後 actual 改變只重新比較，不連動改寫 expected。例外不延伸至自動 resolver、缺證據匯入、Global、學習或 quorum，也不自動建立 reusable expected 規則、Global actual truth 或 approval。Reusable 規則仍需另行明確操作與原來源要求。既有 `manual_expected_decision`、expected resolution binding、GUI confirmation 與外部六閘門事件分別走原 validator，不互相轉型。

新 GUI 本筆確認與歷史／Excel／GPT 六閘門格式必須分開適用。GUI 模式名稱不能代替實際人工操作及其證據；也不能讓外部匯入略過原六閘門。教材錯誤不能由保存 expected 或機械差異自動確認。

來源：[完整規範](V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md) §3.1；[expected 衝突契約](EXPECTED_BUSINESS_CONFLICT_CONTRACT.md) 主契約；[expected binding 契約](EXPECTED_RESOLUTION_BINDING.md) 主契約；[使用指南](USER_GUIDE.md) §二、§三；[原裁定及其來源性質](#ac-p01)。

<a id="ac-04"></a>
## AC-04 Fail-closed 與有限相容性

來源、identity、contract、完整性或相容性不能證明時，回報 unresolved、incompatible、blocked 或明確人工確認，不猜測成功。不得建立未知情況預設相容、缺欄位自動補合法或 unknown future contract 的寬鬆路徑。

Legacy compatibility 必須明確命名、綁定可證歷史契約、有限 allowlist／adapter 並有對應 regression。既有 validator 的成功、檔案存在、版本字串相同或可解析 JSON，不足以證明語義／來源安全。Absent、合法空資料、corrupt、incompatible、unreadable 及 busy 應依原 domain 分開；不得將讀取錯誤包成成功空庫。唯讀入口不得偷偷初始化、修復、覆寫或 mutation 正式資料。

來源：[AGENTS.md](../AGENTS.md) §4、§6、§12；[完整規範](V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md) §3.3、Phase 2、Phase 5 §5.6。

<a id="ac-05"></a>
## AC-05 身份與位置不可順便遷移

Occurrence／review identity、`occurrence_id`／`review_id` 生成及其 schema 是持久識別邊界，不能為 cache、repair、import、搬移或 regression 修正而順便變更。Actual／expected 值、狀態、路徑或列號不可被本草案新增為身份依據。需要 identity migration 時，另立 objective、明確設計、adapter、compatibility 與 regression plan。

有限 historical locator／fallback adapter 只取得原契約明列的能力；不泛化為跨位置、跨字元或 row/index fallback。封印、完整 PDF roster、原始 source_record／alias 一致性、唯一性、collision、bbox 及頁面定位檢查依原入口執行，不能靠重新封印或頂層補欄掩蓋來源矛盾。

來源：[AGENTS.md](../AGENTS.md) §8、§16；[完整規範](V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md) Phase 1、Phase 5 §5.4；[canonical roster 契約](EXACT_PDF_CANONICAL_ROSTER_CONTRACT.md) 全文。舊 tight locator 只作歷史範圍參考，見 [v5.4.1 audit](archive/audits/IMPLEMENTATION_AUDIT_v5.4.1.md) §5。

<a id="ac-06"></a>
## AC-06 Fingerprint 與 semantics epoch

Fingerprint producer 與 comparator 共用同一 required contract registry。Actual／expected 各自要求其完整 schema、reuse policy、semantics epoch 與 evidence components；缺必要欄位、未知 future contract 或未在有限 allowlist 的 legacy payload，均 fail closed。不得維護互相漂移的平行 required-key 規則。

`actual_decoder_semantics_epoch` 與 `expected_resolver_semantics_epoch` 是相同 evidence 可能因演算法語義改變而輸出不同讀音的獨立相容邊界。只有符合該語義條件的變更才應按已授權設計調整相應 epoch；tool version、source hash、release counter 不能冒充 epoch，也不得每次發布自動 bump。這不授權本輪改任何 epoch 值、schema 或 compatibility adapter。

Tool version／implementation source hash 可作 audit metadata，不能單獨替代正式 semantic compatibility。反之，資產未變也不能用來略過必要 epoch／contract 驗證。Identity migration 不能隱藏成 cache epoch 變更。

來源：[AGENTS.md](../AGENTS.md) §5、§8；[完整規範](V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md) Phase 3、Phase 5 §5.8；[v5.7.0 audit](archive/audits/IMPLEMENTATION_AUDIT_v5.7.0.md) §3–4、§12。早期 [hash audit](archive/audits/HASH_FINGERPRINT_ARCHITECTURE_AUDIT_v5.6.2.md) §15–18 是設計背景，不再以其「尚無 epoch」當現況。

<a id="ac-07"></a>
## AC-07 Immutable runtime assets 的 byte integrity

Runtime asset manifest、正式 asset roster、raw SHA、schema／row／來源檢查是安全邊界。檔案現在存在不等於版本正確。SHA mismatch 必須先辨別 asset 正確而 manifest 未同步、manifest 正確而 asset 受污染，或證據不足；不能為了 PASS 批次重算 SHA、以目前內容覆寫 manifest truth 或降低 validator。

必須避免未確認 CRLF／LF、BOM、格式化或重新儲存改變受保護 bytes。歷史 EOL 恢復只證明當時特定來源與原 bytes，不提供一般 normalize、reseal 或 hash refresh 授權。`.gitattributes`、runtime manifest、fixture／原始採納的 byte 約束維持其原義。

Dynamic actual evidence 與 Global DB 不是 immutable runtime assets；不能為方便將動態資料加入 runtime manifest，也不能以此移除其自身來源／schema／完整性檢查。

來源：[AGENTS.md](../AGENTS.md) §6–7；[完整規範](V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md) Phase 5 §5.8；[v5.6.2 audit](archive/audits/IMPLEMENTATION_AUDIT_v5.6.2.md)「Production runtime asset manifest integrity」；[升級說明](UPGRADE_V580.md) §3。

<a id="ac-08"></a>
## AC-08 Dynamic actual 與 per-PDF dependency scope

Project/user-maintained occurrence override、exact glyph truth、conflict 與 provenance 依原動態 evidence 契約管理。已有 per-PDF exact dependency 的路徑不得退回不必要的 whole-DB、全域檔案或 generation 粗粒度 invalidation，除非另有明確架構任務、設計與驗證。

Actual fingerprint 只依適用 PDF 的既有 logical dependency 契約反映會影響有效 state／reading 的資料。Matching legacy import、audit／provenance 增長若不改有效結果，不應無故改 logical subset；conflict／可用性改變則必須使真正受影響依賴失效。Global generation 供 audit，不是 cache key。

Scoped hash 不代表可以省略來源驗證或默認缺依賴為空集合；未知 dependency contract、失效 evidence 或不完整 roster 仍依原契約拒絕。跨檔 CFF evidence pool 不能為少寫檔而縮減；未受影響 workbook 不應被無意重存而改 bytes。

來源：[AGENTS.md](../AGENTS.md) §7；[完整規範](V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md) Phase 3、Phase 5 §5.6、§5.8；[migration 契約](LEGACY_GLOBAL_MIGRATION_v5.8.md)「Storage and compatibility」；[v5.4.1 audit](archive/audits/IMPLEMENTATION_AUDIT_v5.4.1.md) §2–4。

<a id="ac-09"></a>
## AC-09 Exact identity 與 reusable evidence 優先序

Global TTF 只接受原 `GLOBAL_ELIGIBLE_SIMPLE_GLYF_V1` 契約：合法、可見、完整、`numberOfContours > 0` 的 simple glyf，以完整 raw glyf record 的 SHA-256 識別；composite、malformed、truncated 不因 hash 相同而獲 Global 資格。CFF 以 canonical style_group 與完整 resolved glyph recording SHA-256 識別；不同 style_group 不能合併。

Font name／index、component／annotation／normalized outline signature 或語意不能單獨證明 Global exact identity。Global 限制不授權刪除原本合法的 project-local 功能；project-local learning 門檻也不能反向成為 Global promotion 門檻。

依原契約先核對全部適用 reusable exact sources 的 union conflict，再套優先序：有效 occurrence-local direct override；通過 conflict gate 的 project exact、Global `VERIFIED_GLOBAL`、static TTF exact；符合自身契約的 independent decoder；否則 unresolved。相關 project／Global／static exact donors 互斥時全部 suppression，有效本位置 direct override 保留。`CANDIDATE`、`PROMOTION_READY`、migration candidate、source_count 或 provenance 不是 reusable truth。

來源：[完整規範](V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md) §3.2、Phase 1、Phase 3；[AGENTS.md](../AGENTS.md) §3–4、§7。早期 project-local 門檻見 [v5.4.0 audit](archive/audits/IMPLEMENTATION_AUDIT_v5.4.0.md) §3，不能套用為 Global 規則。

<a id="ac-10"></a>
## AC-10 Direct evidence、quorum 與 approval

只有新產生且可驗的 `DIRECT_VISUAL_ACTUAL` 可依原契約計入 Global quorum。自動解碼、傳播、複製、沿用、legacy evidence、候選、usage index 或圖片 cache 均不得被重新標為 fresh direct evidence。

至少兩個同讀音、correlation-resistant independent sources 必須在實際同一對來源上同時通過 project/session、PDF SHA、embedded font-program SHA、occurrence ID、review ID 五軸獨立條件。分別計各欄 distinct 數、複製／改名／重跑或偽造 ID 都不能製造 quorum。

Quorum 與 approval 是不同門檻。既有 explicit approval service 必須綁定 exact identity、reading、完整 evidence roster／digest、policy 及 revision；stale／已變動請求拒絕。日常看圖保存所整合的核准意圖仍須走同一 service，原點擊不授權後來改變的 evidence 集合。只有實際看過並明確確認的位置建立直接人工證據，其他同組位置不自動取得。

來源：[完整規範](V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md) Phase 4；[日常學習契約](DAILY_GLYPH_LEARNING.md)「看圖確認」「保存、啟用與重試」「證據與相容邊界」；[使用指南](USER_GUIDE.md) §四。

<a id="ac-11"></a>
## AC-11 Conflict、quarantine 與可追溯性

同 exact identity 的適用 reading 矛盾時，禁止 last-writer-wins。依原安全寫入流程保存全部反證、durable quarantine、清除 active reading、撤銷適用 approval，保留 revision／generation、provenance 與 receipt。相同讀音的後續 evidence 增加不能自動解除 quarantine；independent decoder 也不能將隔離狀態洗成可信重用。

Read-side suppression／diagnostic 不得被宣稱為已 durable quarantine 或 revoke approval；唯讀查詢不可為取得一致外觀而 mutation。反之，新 mutation／approval 的 union conflict 不能省略 retained legacy readings。成功保存 conflict 可以是合法交易結果，不等於 transaction failure；本位置有效 direct override 不因 Global promotion 失敗或衝突被撤回。

Conflict resolution 的 explicit adjudication 與 fresh quorum 是原安全門檻，不證明 adjudication API 已提供，也不授權新增功能。不得偽造來源、確認時間、decision snapshot 或 receipt。

來源：[完整規範](V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md) §3.2–3.4、Phase 3–4、Phase 5 §5.5–5.6；[日常學習契約](DAILY_GLYPH_LEARNING.md)「衝突與已知影響」；[migration 契約](LEGACY_GLOBAL_MIGRATION_v5.8.md)「Storage and compatibility」。

<a id="ac-12"></a>
## AC-12 交易、交付與已知提交狀態

適用 Global 交付的原流程順序為 project-local actual transaction → durable outbox → Global idempotent delivery → processed_intent receipt → project ack → refresh。Project 與 Global 是不同交易 domain；local 已保存、delivery／approval 待完成、ack 待完成及 refresh 待完成應如實區分，不能合為一個模糊成功旗標。Surface-only 依 [AC-18](#ac-18) 及原技術附件處理。

Whole-batch validation、owner／token／plan、lock、revision／DB CAS、原來源／artifact snapshots、intent payload 與原 image bytes 依各入口契約執行。相同 ID／相同 payload 重試保持 idempotent；相同 ID／不同 payload 是錯誤。不得重複計票、receipt、provenance 或偽造恢復成功。資料漂移與 stale request 不能被晚 capture、merge foreign bytes 或重算 truth 合法化。

恢復 authority 必須來自原有效 receipt、known own outputs、durable journal／publication／ACK 副本及原 plan；不能只看當下磁碟有 COMMITTED、僅 attempted marker 或缺 journal 就猜測提交／完成。已知本次 COMMITTED 後的 delivery／cleanup／refresh 錯誤，依原 typed committed failure 保留真實 durability；未知或 rollback 結果不能回傳假成功。

Cooperative lock 與 optimistic precondition 不等於 OS atomic compare-and-swap；原最末比較至 replace／unlink 之間對非合作 writer 的窗口不得在文件中宣稱消失。具體 partial-prefix、rollback、outbox acknowledgement、journal successor 與 publication 狀態仍由 [surface 技術契約](MANUAL_SURFACE_ACTUAL_CONTRACT.md)「保存、依賴與恢復」「C19」及其原入口定義，本草案不增改格式或恢復能力。

來源：[完整規範](V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md) Phase 2、Phase 4；[surface 契約](MANUAL_SURFACE_ACTUAL_CONTRACT.md)「保存、依賴與恢復」「C19」；[日常學習契約](DAILY_GLYPH_LEARNING.md)「保存、啟用與重試」。

<a id="ac-13"></a>
## AC-13 保存、cache、staging 與背景結果

保存 cache 只在已驗證且內容依賴有效的服務生命週期使用，須保留原 baseline、ledger、identity index 與 snapshot／內容雜湊 guard。大小、mtime、source/version 相同或前次成功不能替代內容驗證。外部來源異動或失效 anchor 不能因 retry、defer、reload 或更換 service 被清除；須恢復原 evidence 或經原合法 refresh 建立新的有效工作階段。

Staging 只表示意圖，不是 authoritative actual、不計 quorum，也不等於教材完成。只有明確直接 checked 的位置可在既有 actionable queue 暫時隱藏；未 checked peers 仍待處理，同筆 expected 未決不得被 actual 暫存遮蔽。Apply 後的直接位置 canonical actual postcondition、expected 保留及 completion gate 不可省略。

失敗保存保留原 DB／目前項目／待辦，不前進、不 publish 假成功。成功 durable save 後若顯示／queue 失敗，須區分已保存與 UI 未完成；重開依 durable truth 恢復。背景請求綁定 generation、project、manifest／DB snapshot 與 review_id，stale worker 不 publish；worker／UI owner 的資源及 callback 邊界依原實作契約保留。

新學習必要證據圖片、可重建 cache 與未確認記憶體圖片分開；cache 清除不可刪有效確認原件。最後提交保存的是當次已準備且重新驗源的相同圖片 bytes，不能重新渲染換圖。本草案不新增清理正式資料、預覽快取或背景改寫未開啟專案的授權。

來源：[保存效能文件](REVIEW_CONFIRM_PERFORMANCE.md)「快取與保存契約」；[v5.7.0 audit](archive/audits/IMPLEMENTATION_AUDIT_v5.7.0.md) §6–9；[日常學習契約](DAILY_GLYPH_LEARNING.md)「證據與相容邊界」；[使用指南](USER_GUIDE.md) §二。歷史效能數字及真視窗測試命令不屬本節現行驗證要求。

<a id="ac-14"></a>
## AC-14 Legacy migration 與 read／reuse 的區別

Migration 只處理明確選定 project／PDF roots；不 broad scan、不掃 parent／Desktop／drive、不修來源 project、不 refresh、不初始化缺少的 evidence，也不透過 expected resolver 建立 actual-only mapping。Reading donors 限原 learning／conflict 檔，舊 override、decoder result、provenance、字典或語境不能單獨提供 Global legacy truth。

當前 embedded font bytes 必須重新證明 exact identity；source SHA、完整 locator、schema、CSV headers／canonical values、counts、retained readings、source mapping 與全部可預先判定檢查必須在第一次 Global mutation 前完成。只有已證 `NON_GLOBAL_ELIGIBLE` 的條目依原診斷排除；缺 PDF、錯 SHA、stale identity、歧義或不完整 proof 不能默默跳過。

Legacy evidence 永遠不計 direct quorum，不因 source_count 或複製來源增加而直接獲 promotion／approval。有限 `legacy-v0` 必須按原 exact algorithm 及完整 frozen contract 驗證。Strict-readable 不等於 reusable；合法舊 candidate 可以只讀，但 retained contradiction 必須即時 suppression，不能延至下一次寫入。原 rows／IDs／status 不因讀取重寫；新寫入只走原 current migration contract，不偽造歷史 receipt。

Dry-run 不初始化 absent Global DB、不 mutation 來源或 Global。Empty apply 仍 strict-read 現有 DB，absence 保持 absence；有效 no-op 不增加 generation／receipt／provenance。Nonempty apply 的整批原子性、bounded locking、replay receipt、rollback 與初始化後失敗的具體結果，保留附件原義。Reconfirmation target 只是定位資訊，不自動 stage 或建立 direct evidence。

來源：[完整規範](V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md) Phase 5 §5.1–5.8；[migration 附件](LEGACY_GLOBAL_MIGRATION_v5.8.md)「Validation and source boundaries」「Storage and compatibility」「Bounded historical-candidate compatibility」「Fresh confirmation」。

<a id="ac-15"></a>
## AC-15 Expected resolution binding 與重播

Expected 匯入／保存後的原 action、canonical payload、baseline expected、target、session、fingerprint、resolver epoch 與 integrity 綁定，依 [EXPECTED_RESOLUTION_BINDING.md](EXPECTED_RESOLUTION_BINDING.md) 主契約保存；其 binding 不含 actual。GUI 確認只能攜帶原 binding，不能用 confirmation snapshot 補造來源。

保存、prepare、repair、undo／transfer 使用原共用 validator；expected／context／target／fingerprint 漂移拒絕 binding 重播。Actual 漂移可使 actual-dependent confirmation 失效，但不能抹除原獨立 expected resolution。缺欄、malformed、未知 version、未經正式 producer 的來源不能由本草案補成有效。

有限 legacy adapter 必須明確指定原 workbook、唯一 review_ids 及原始 SHA，先按原 dry-run 檢查；不掃描、不憑自由 source 字串追認、不更改原 event／identity／confirmation time，也不因此發布新報告或宣稱完成。一般 `manual_expected_decision`、歷史 unbound expected-only 入口及新 binding 各保留其有限能力，不互授權。

來源：[expected binding 附件](EXPECTED_RESOLUTION_BINDING.md) 主契約、「明確限定的歷史 adapter」「隔離操作」；[AGENTS.md](../AGENTS.md) §3–4、§8–9。

<a id="ac-16"></a>
## AC-16 Expected 匯入衝突與依賴重核

Expected import 在任何業務分類前仍完整核對來源 proof、PDF bytes／頁面、canonical identity、seal、bound rows、expected fingerprint、原始位置／語境及合法來源事件。結構、來源、owner／CAS／交易異常仍整批拒絕，不能轉成可略過列。

合法來源與既有目標 expected 一致時依原規則保存補充；不同或無法證明相同條件時保留來源與目標證據、原目標正式判定及待裁決 workflow。未裁決 receipt 是獨立完成門檻，不用 annotation 改 expected truth。保存人工裁決前核對所展示 receipt／snapshot，漂移要求明確 reload；同檔重匯保持判重，不復活已否決來源。

非 expected-only 的差異／排除事件遇依賴漂移時，依 `review-dependency-recheck/1` 原有限範圍保存來源 action、基線／當前 snapshots 與 differences，不套用來源動作、不重綁事件，也不把它改成 expected resolution。`PRESERVED_LOCAL_DECISION` 等分類只依附件原 allowlist 與重播條件，不能成為一般保留完成狀態捷徑。

來源：[expected business conflict 附件](EXPECTED_BUSINESS_CONFLICT_CONTRACT.md)「Expected 匯入業務衝突」「非 expected-only 依賴待重核」。附件的原修正輪次、writer shim 與當時 export 失敗紀錄是歷史驗證限制，不證明目前 HEAD 成功或失敗。

<a id="ac-17"></a>
## AC-17 Canonical roster、sealed workbook 與 relocation

相同 PDF 的完整 canonical roster 配對只在原 `same-pdf-canonical-roster/1` admission 成立後使用；完整各自唯一且相同 PDF SHA 集合、實體 bytes、原頁／文字證據、兩側 seal、原 source_record、alias、canonical IDs／bbox／matrix／尺寸與一對一 roster 都必須核對。有限旗標 adapter 不改一般 anchor、renderer 或 ID，不因摘要而擴大支援 schema。

Workbook 讀寫由目前明確選定 project root 所有。PDF／actual-candidate role／workbook SHA／trusted relative locator 必須形成原 frozen binding；未知角色、traversal、links／junction／hardlink、缺檔、錯 SHA、同 SHA 多候選或歧義拒絕。歷史 absolute locator 的有限 descriptor adapter 不讀舊位置、不換 drive、不全機搜尋替代檔。Frozen location 失效不能改找另一份檔案。

Recovery 先完整驗證原 manifest／seal、ordered backup entries、原 SHA／bytes／role-relative 關係，再還原選定 root 中的原記錄路徑。不能從目前 bytes 重新發現位置、重封來源真值、改寫原機器檔案或以新檔名追認。Project root 搬移／改名不等於內部 workbook／PDF rename migration。

來源：[canonical roster 附件](EXACT_PDF_CANONICAL_ROSTER_CONTRACT.md) 全文；[sealed workbook location 附件](SEALED_WORKBOOK_LOCATION_CONTRACT.md) 全文；[使用指南](USER_GUIDE.md) §三；[升級說明](UPGRADE_V580.md) §4。

<a id="ac-18"></a>
## AC-18 Manual surface actual 的獨立邊界

`manual-surface-actual/1` 的 capability、marker、origin、surface projection、原 occurrence/review、source／render proof、人工空白輸入及確認 receipt，依 [MANUAL_SURFACE_ACTUAL_CONTRACT.md](MANUAL_SURFACE_ACTUAL_CONTRACT.md)「格式與證據」及後續必要條款驗證。它是 occurrence-local final visible surface evidence，不能變成 raw glyph donor、learning、Global intent 或 quorum，也不能自動進入 expected resolver／fingerprint 或取代獨立 expected 來源。[AC-03](#ac-03) 所述原本明確的本筆人工操作仍按原 validator 與窄限制處理，不因 actual 來自已驗有效且已套用的 surface evidence 而額外排除；本草案不增加任何人工例外。此處沿用 [完整規範](V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md) §3.1 及 [surface 附件](MANUAL_SURFACE_ACTUAL_CONTRACT.md)「格式與證據」的分工。

Required files／marker／未知 successor 的判斷不得因缺檔降格 legacy、自動初始化合法專案或補造來源；bare legacy 與 enabled profile 各保留原讀取／寫入邊界。原 owner、captured dependency、known phase、afterimages、publication／ack 與狀態輸出限制由附件負責，不能以摘要合併兩種 transaction authority。

原附件對 nonempty／缺 manifest 的 cold writer 在第一次副作用前拒寫，明載為協調者依當次原文作的適用解讀，並非另有人類採納的 cold-specific 條款。本草案保留該限定、原負向控制與未解承諾的回報要求，不將它升為普遍禁止 legacy 讀取／修復的新規則。日常學習的 journal／relocation 支援也僅限附件所驗證的原 owner、版本、來源與有限恢復狀態，不泛化為任意舊專案修復能力。

#### 原整合改善任務的有限設計紀錄

本機整合基準上，[surface 附件](MANUAL_SURFACE_ACTUAL_CONTRACT.md) 及對應實作仍以 `surface-visual-proof/1` 為準。原整合改善任務 `integrated-interactive-review-20261105` 的 `surface-visual-proof/2` pair 實作來源 `7e7849847479f3ac10488b061b05eb6719f291f0` 未併入原文件 baseline 或本機整合比較點。2026-11-06 的裁定接受已核對的原任務指令轉錄與固定 HTML 作為該原任務的有限設計來源，適用範圍見 [AC-P02](#ac-p02)。以下兩段僅記該候選設計。

有限 `FILL_STROKE_PAIR_V1` 依[原作證來源](#ac-source-custody) `execution-instruction-reading.txt` §7.1–7.5：恰好兩個相鄰且完整匹配的 fill／stroke 原生成員，以實際順序構成 source unit，維持完整輪廓、coverage／contribution 與四種 replay 證明；不符即拒絕，不以 bbox／Unicode／字型名稱猜測。即使通過仍為 `SURFACE_ONLY`、`donor_eligible=false`，本人須看圖輸入新讀音，不產生 reusable truth、學習、Global 或 quorum。§7.6–7.7 保留完整 v1 與嚴格 v2 並存，v1 bytes／hash／歷史不改寫，不把單操作偷偷解釋為雙操作；未知 schema、extra fields、缺成員或來源對調拒絕。首次準備保存 v2 須告知需要支援 v2 的程式，不能承諾不支援 v2 的舊版仍可寫入。

該候選的保存及恢復設計另見固定指令 §2.11、§4.3–4.8：只恢復指定 durable identity，不順便套用新 pending；surface-only 處理原全部 surface pending，ordinary staging 保持其原 bytes 並納入 admission／備份，未知或混合交易封套拒絕。明確套用先 ordinary，再取得當前 admission／manifest 重驗 surface；第二步過期或失敗不部分偷套、不刪 pending，保留第一步已成功結果。原 owner、receipt、原像／自身後像、journal／publication、必要 proof 與 expected 獨立性不因 proof `/2` 放寬；不得機械更換 store／journal／identity／epoch 或恢復語義。現行保存及恢復仍依原 surface 技術附件。

現行來源：[surface 附件](MANUAL_SURFACE_ACTUAL_CONTRACT.md)「格式與證據」「保存、依賴與恢復」「驗收與停止條件」「C19」；[日常學習契約](DAILY_GLYPH_LEARNING.md)「看圖確認」「保存、啟用與重試」；[manual_surface_actual.py](../manual_surface_actual.py) 為現行實作定位。候選設計與 `PROOF_V2` 實作緣由見[原索引](#ac-source-custody) `#candidate-scope`。

<a id="ac-19"></a>
## AC-19 GUI、查閱與學習觀測的界線

GUI 只組裝明確人工操作並呼叫既有安全 service，不重造 identity、quorum、approval、conflict 或 truth 判斷。狀態頁應區分儲存狀態、有效可重用狀態、journal／outbox／receipt／refresh、absence／合法空庫／錯誤；預檢通過不表示 APPLY、核准或可重用。

Candidate list、usage index、快取及 aggregate metrics 是觀測／定位資訊，不是 truth 或來源票數。影響清單只涵蓋明確登記且可驗的 project，不能宣稱全機完整；未開啟 project 不在背景重寫。只因等待第二來源或稍後學習，不增加教材完成門檻，也不清除其原有實際待辦。

尚未提供的 aggregate metrics、批次 APPLY、adjudication 或恢復執行功能，以當次已核對的功能範圍說明為準，不把早期 Phase 計畫當已實作或新工作授權。原生介面觀察／人工驗收與非視窗 service 證據分開，不互稱已驗證。

來源：[完整規範](V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md) Phase 6；[Phase 6 需求對照](PHASE6_REQUIREMENTS_STATUS.md) 主表、「尚未提供的完整統計與範圍」；[使用指南](USER_GUIDE.md) §四；[日常學習契約](DAILY_GLYPH_LEARNING.md)「衝突與已知影響」。

<a id="ac-20"></a>
## AC-20 產品 regression、reconciliation 與教材完成

產品 mandatory／historical pronunciation regression 與 CI 歷史 execution 重播是不同概念。取消 CI 歷史 artifact 閉包，不撤銷產品 regression、不刪原失敗、不解除當前安全 blocker。歷史正／負案例只能在現版 identity 與兩條 evidence 獨立產生後評估，不能救援未決 lane、反推答案或憑前版未變判通過。

Mandatory regression 仍須有適用的必要案例並全數實際執行；hard／portable 適用案例按原 identity、source PDF SHA、locator/context、唯一 owner、definition 與 split 對帳執行。Applicable required 與 executed 必須一致，failure、NOT_EXECUTED、duplicate 不可略過。只有真正沒有適用 historical PDF occurrence 案例的 `0 required / 0 executed` 按原規則中性通過，不擴為 mandatory 無案例可通過。Reference-only 結果保留 audit，不移入完成分母；不得編造 source PDF SHA 或以歷史讀音協助 locator。

Actual／expected 各自依 lane status 統計覆蓋與未決；未解碼、expected unresolved／ambiguous／conflict 不是「正確」。同筆兩 lane 的問題都須反映在 gate，不因工作流程 state 只顯示其一而消失。教材全冊完成須原 actual／expected coverage、所有適用非終態／錯誤／確認要求、mandatory／適用 historical regression、完整 artifact integrity 與集合 reconciliation／completion gates 全部成立。合法零 occurrence PDF 可參與全冊資料對帳，但不能以空母體假稱全冊完成。

`PROCESSING_FINISHED`、候選收斂、匯入／保存成功、報告存在或待辦 UI 暫時為零，不等於 `PROOFREAD_COMPLETE`。Repair、report regeneration、refresh 或 import 不得放寬任何完成條件，亦不得用舊 session／報告／統計冒充目前結果。沒有完整漏檢真值不得宣稱召回率，位置數與根因群組數不可混用。

來源：[AGENTS.md](../AGENTS.md) §3、§9、§11；[使用指南](USER_GUIDE.md) §二、§五；[v5.5.0 audit](archive/audits/IMPLEMENTATION_AUDIT_v5.5.0.md) §2–5；[v5.6.1 audit](archive/audits/IMPLEMENTATION_AUDIT_v5.6.1.md)「修正」；[v5.6.2 audit](archive/audits/IMPLEMENTATION_AUDIT_v5.6.2.md)「歷史回歸適用性」「安全邊界」；[zero occurrence hotfix](archive/hotfixes/HOTFIX_ZERO_OCCURRENCE_v5.4.1.md)「安全邊界」。歷史案例數與 PASS 不轉為目前結果。

<a id="ac-21"></a>
## AC-21 備份、搬移與發布的產品資料邊界

一致備份需先停止相關 project／Global 寫入者，保留原 PDF bytes、整個 project 的 session／manifest／人工事件／DB／evidence／staging／journal／outbox／receipt／reports、程式旁實際使用者資料及 Global 資料組。存在 WAL 時必須保存一致資料組，不只複製主 DB、不刪 WAL；自訂 Global root 必須明確核對，不能只備份預設位置。

Seed、使用者後續 learning／override／conflict／provenance、approved expected 規則與 immutable runtime assets 分角色保存。不得以檔名含 user 推定可刪，也不能新包覆蓋個人判定、批次追認舊 evidence 或為升級改 runtime manifest truth。先用完整工作副本及隔離 Global，未知兼容性與缺來源維持拒絕。

搬移還必須保留當次狀態所需的外部必要證據。依 [日常學習契約](DAILY_GLYPH_LEARNING.md)「保存、啟用與重試」，已有 `actual_refresh_publication.json` 時，完整 project 與可驗 PDF 之外，還須保留其明確登記、位於 project 同層的 `.ar-XXXXXXXX` 及其中 `previous`／`ack-committed.json`；只搬 project 而遺失原件不可重建或回舊 root 搜尋。這只描述該 pending publication 的有限要求，不擴成全機掃描、一般資料搬遷或本輪操作授權。與升級／使用指南摘要的同步列為 [AC-P04](#ac-p04)。

軟體發布與教材全冊完成分開。Candidate ZIP、版本號、main merge、tag／Release 及教材完成各自有原證據與授權門檻；任一者不能代替其他者。發布包須來自指定來源與 allowlist，核對 manifest／清單／checksum；原人工 USER_REPORTED 結果不能改稱代理、新 HEAD 或其他教材已驗。候選／穩定發布狀態列為 [AC-P05](#ac-p05)，本草案不重新宣告版本完成。

來源：[升級與回復](UPGRADE_V580.md) §1–5；[日常學習契約](DAILY_GLYPH_LEARNING.md)「保存、啟用與重試」；[六項人工驗收](RELEASE_ACCEPTANCE_V580.md)「操作前」「固定操作表」「歷史證據的限定沿用」「軟體發布完成條件」；[使用指南](USER_GUIDE.md) §三、§五。

<a id="ac-22"></a>
## AC-22 驗證政策不改產品契約

`validation-flow/current-evidence/1` 使用當次候選 coverage `zhuyin-validation-coverage/3` 與 merge-short `zhuyin-merge-short/3`。歷史 `/2` 及其 reader、原 schema／判定保持原義；run／inventory 的版本也不能跟 coverage 一起機械取代。依[流程入口](DEVELOPMENT_REVIEW.md#applicability)選定審查版本，再依[驗證政策](VALIDATION_POLICY.md)核對適用的證據矩陣。

取消所有自動真 Tk、hidden root、虛擬顯示器、fixture／import／子程序中的 Tk 初始化、configure／preflight／GUI retry／supplement，不取消產品 GUI／launcher，也不取消有效非視窗資料、交易、保存、恢復、來源與功能安全測試。非視窗服務結果不能改稱人工原生 GUI 驗收。本輪只作文件靜態檢查，不執行產品測試、CI 或 GUI，不宣稱正式審查 PASS。

來源：[AGENTS.md](../AGENTS.md) §11、§17；[VALIDATION_POLICY.md](VALIDATION_POLICY.md)「現行永久政策：當前候選證據」「原 no-real-tk/1 政策與採納歷史」；[TESTING.md](../TESTING.md)「PR 的當前完整非視窗驗證」「develop 實際 merge 的短驗證」「歷史契約與人工驗收」。

<a id="ac-23"></a>
## AC-23 獨立技術附件與適用關係

下列附件原路徑及精確契約保持不變。表中的範圍摘要不能替代全文 validator、授權或證據要求。

| 原路徑 | 本草案入口 | 原附件負責的必要細節 |
| --- | --- | --- |
| [EXACT_PDF_CANONICAL_ROSTER_CONTRACT.md](EXACT_PDF_CANONICAL_ROSTER_CONTRACT.md) | [AC-05](#ac-05)、[AC-17](#ac-17) | exact PDF roster admission、支援 schema、source aliases／IDs／bbox、有限 flag adapter |
| [EXPECTED_RESOLUTION_BINDING.md](EXPECTED_RESOLUTION_BINDING.md) | [AC-03](#ac-03)、[AC-15](#ac-15) | binding version、payload／fingerprint／epoch、重播／transfer、有限 legacy adapter |
| [EXPECTED_BUSINESS_CONFLICT_CONTRACT.md](EXPECTED_BUSINESS_CONFLICT_CONTRACT.md) | [AC-03](#ac-03)、[AC-16](#ac-16) | receipt formats、補充／待裁決、人工 token／CAS、dependency recheck、有限歷史 entry |
| [MANUAL_SURFACE_ACTUAL_CONTRACT.md](MANUAL_SURFACE_ACTUAL_CONTRACT.md) | [AC-12](#ac-12)、[AC-18](#ac-18) | capability／proof／projection、captured authority、journal／publication／ACK、typed commit 與恢復 |
| [SEALED_WORKBOOK_LOCATION_CONTRACT.md](SEALED_WORKBOOK_LOCATION_CONTRACT.md) | [AC-17](#ac-17) | selected-root role／PDF／SHA frozen binding、有限 legacy descriptor、authenticated backup recovery |
| [LEGACY_GLOBAL_MIGRATION_v5.8.md](LEGACY_GLOBAL_MIGRATION_v5.8.md) | [AC-08](#ac-08)、[AC-11](#ac-11)、[AC-14](#ac-14) | read-only source plan、exact reproof、legacy-v0、whole-batch APPLY、reconfirmation |
| [DAILY_GLYPH_LEARNING.md](DAILY_GLYPH_LEARNING.md) | [AC-10](#ac-10)、[AC-12](#ac-12)、[AC-13](#ac-13)、[AC-21](#ac-21) | 看圖固定操作、來源獨立性、圖片保存／cache、重試與 relocation recovery |
| [PR_REVIEW_GATE.md](PR_REVIEW_GATE.md) | [AC-01](#ac-01)、[AC-22](#ac-22) | 開發審查 schema 與有限歷史 adapter；不定義產品 pronunciation truth |
| [RELEASE_ACCEPTANCE_V580.md](RELEASE_ACCEPTANCE_V580.md) | [AC-21](#ac-21) | 原版本六項有限人工驗收、隔離與結果限制；不是驗收 PASS 或發布授權 |

<a id="ac-24"></a>
## AC-24 裁定紀錄與剩餘待決事項

AC-P01、AC-P02 依 2026-11-06 當次使用者裁定更新；其餘事項仍為文件位階、版本來源或整併表述的待確認點，不由本草案自行裁決。原來源、validator 與原已合法支援的功能／入口保持其適用性，不因「待裁定」而撤銷功能或新增停止門檻；也不以文件整理默認成功、擴大例外或新增能力。後續若要修改這些邊界，仍須按原有效契約與另行授權處理。

<a id="ac-p01"></a>
### AC-P01 人工 expected 例外的規範位階

2026-11-06 使用者明確裁定為 [AGENTS.md](../AGENTS.md) §3 禁令下的有限例外，依 [AC-03](#ac-03) 保留看圖、語境、有效且已套用 actual、snapshot、身分、來源及 receipt／CAS 等原檢查；獨立 `ENTER_EXPECTED` 不增加 actual 已解析／已套用前提。原裁定定位為[歷史來源](#ac-source-custody)的 `20261106-user-decision-transcript.md`「一、1」及原索引 `#ac-p01-decision`。轉錄記錄該次裁定；本草案的整體採納仍須另行裁定及審查。

<a id="ac-p02"></a>
### AC-P02 Surface proof 版本與採納證據

2026-11-06 使用者接受轉錄與固定 HTML 作為原整合改善任務的有限設計來源，見[歷史來源](#ac-source-custody)的 `README.md#source-set` 和 `#ac-p02-design`。[AC-18](#ac-18) 定位 §7 的精確格式、相容與恢復限制。候選 base 與本機整合比較點均無該 pair 實作；原產品候選程式、開發短測及 `CODE_REVIEWED` 各自保持原適用範圍。後續驗證依各自 task 的適用流程；原 surface 附件與 schema／version／validator 依原契約生效。

<a id="ac-p03"></a>
### AC-P03 思考模式 2.5 的整體位階

待逐條確認 [思考模式2.5.docx](../參考文件/思考模式2.5.docx) 的逐次重建、兩圖要求、詞例與現行 exact reuse／GUI 確認模式之適用關係。已由原規範承接的獨立 evidence、完整詞條、負向控制、覆蓋與對帳原則保留；不能將整份歷史參考直接提升為新最高規範，也不能逕判全文失效或更改 runtime 規則。

<a id="ac-p04"></a>
### AC-P04 Pending publication 的搬移說明同步

[日常學習契約](DAILY_GLYPH_LEARNING.md) 已描述明確登記的同層 `.ar-XXXXXXXX` 必要恢復證據；[升級說明](UPGRADE_V580.md) §4 與 [使用指南](USER_GUIDE.md) §三的摘要未充分呈現該狀態差異。待確認後續文件同步的狀態矩陣與責任範圍。[AC-21](#ac-21) 保留較具體原證據要求，不提出資料 migration、缺件重建或操作正式資料方案。

<a id="ac-p05"></a>
### AC-P05 候選、功能範圍與發布狀態

舊文件的 candidate、NOT STARTED、USER_REPORTED、延期與固定 SHA 都是原時點陳述。待有相應當前原件與明確範圍後，才能更新功能／發布狀態；不能由版本號、本文新增或舊 CI 推定已上線、COMPLETE、main／tag／Release 已授權。

<a id="ac-25"></a>
## AC-25 舊條款到新章節的來源與覆蓋對照

本表沿用第一階段 F01–F61 及 N1–N6 對照，只記本文件承接的產品相關內容，不重新分類 61 份文件。來源文件的章節文字為定位依據；相對連結保留原檔案入口，不依賴不穩定的中文自動 fragment。覆蓋表示已在草案列出規則／附件／待裁定，不表示通過產品驗證或正式審查。

| 第一階段來源與原章節 | 新章節 | 本草案覆蓋結果 |
| --- | --- | --- |
| F01 §1、§3–4；F25 §3 | AC-01–AC-04 | 兩 lane 獨立、expected 不等待 actual、定位／候選不得越權、fail-closed；人工窄例外依本次明確裁定及 AC-P01 限定 |
| F25 §3.1；F07 主契約；F24 §二–三 | AC-03、AC-P01 | 本筆主動人工 expected 的證據／隔離邊界與 GUI／外部六閘門分流；2026-11-06 位階裁定另有轉錄，不追認舊授權或擴張例外 |
| F01 §5、§7–8；F33 §15–18；F45 §3–4 | AC-05–AC-08 | shared registry、獨立 epochs、有限 legacy、audit-only version/hash、per-PDF scope、身份不順改 |
| F01 §6；F44「Production runtime asset manifest integrity」 | AC-07 | raw bytes／EOL／來源判斷、禁止盲目更新 manifest／SHA；歷史恢復事件維持限定 |
| F25 Phase 1–3；F32 §3–5、§11–13 | AC-08–AC-09 | exact identity、union conflict、direct override 優先、logical subset；歷史設計不充作目前狀態 |
| F25 Phase 4；F05「看圖確認」「保存、啟用與重試」；F41「核心行為」 | AC-10–AC-12 | 五軸來源、direct only、quorum／approval 分開、持久隔離、交易與重試分階段 |
| F25 Phase 5 §5.1–5.8；F09 規範章節 | AC-14 | source read-only、legacy 零 quorum、strict-readable≠reusable、reproof、整批交易、reconfirmation |
| F25 Phase 6；F11 主表／延期範圍；F24 §四 | AC-19 | GUI service 邊界、觀測不是 truth、功能／metrics 狀態不等於實作授權 |
| F08 主契約／有限 adapter；F47「安全邊界」 | AC-15 | expected-only binding／重播、actual 漂移保留獨立 expected、精確 legacy 前置條件 |
| F07「Expected 匯入業務衝突」「非 expected-only 依賴待重核」 | AC-16 | supplements／conflicts、target basis、receipt CAS、判重／裁決、dependency recheck |
| F06 全文；F22 全文 | AC-05、AC-17、AC-23 | canonical roster、aliases／ID／定位、selected-root、role／SHA、有限 adapter 與恢復 |
| F10「格式與證據」「保存、依賴與恢復」「C19」；F05 恢復段落 | AC-12、AC-18、AC-P02 | proof／capability、known receipt／phase／outputs、journal／publication／ACK、typed COMMITTED 保留；有限 /2 的原任務設計與本次來源接受另見穩定索引，正式驗證未完成 |
| F21「快取與保存契約」；F45 §6–9；F05 圖片／cache 段落 | AC-12–AC-13 | snapshot／hash guard、失敗不前進、stale worker、不將 staging／cache 當 truth、圖片原 bytes |
| F34 的獨立回歸原則；F40 §2–5；F43「修正」；F44「安全邊界」；F50「安全邊界」 | AC-20 | mandatory／historical applicability、0 適用中性、空 PDF、雙 lane、reconciliation／completion；舊測試數不沿用 |
| F23 §1–5；F24 §三、§五；F05 pending relocation；F18 操作／沿用章節 | AC-21、AC-P04–AC-P05 | 一致備份／WAL／使用者資料、`.ar-*` 必要證據、隔離、發布與教材完成分離 |
| F31 現行政策；F03 現行 PR／merge／main 章節；F16 檔首 current 政策 | AC-22 | current coverage／short `/3` 與歷史 `/2` 分開、自動真 Tk 取消不取消產品安全測試 |
| F61「最高優先順序」「來源保真原則」；F60 已由現規範承接部分 | AC-02、AC-20、AC-P03 | expected 來源／詞條、獨立現版結果、負向控制與覆蓋；DOCX 整體位階待裁定 |
| F01 §2、§10–17；F15；F16；F25 §1–2、§5–8 | AC-01、[現行流程入口](DEVELOPMENT_REVIEW.md#applicability) | 開發程序與當次授權分工；這些舊來源的審查／版本條款依流程入口選定的適用契約 |
| F19–F20、F57–F59；F21 原始 benchmark／舊指令 | AC-00–AC-01、AC-21–AC-22 | 保留歷史 task／原件／原判定，不轉寫成現行授權、測試命令或新 PASS |

2026-11-06 裁定及原候選的補充來源見[歷史索引定位](#ac-source-custody) `#clause-map`：AC-P01→AC-03；固定設計 §0.3、§7.1–7.7、§2.11、§4.3–4.8→AC-18／AC-P02。原 19 列來源身分沿用。

歷史草案曾列後續整併建議：F25 §3–4、F21 保存不變量及 F11 的有效功能邊界供核心整理，F24／F23／F05 的使用操作可轉介使用指南；F06–F10、F16、F22 等精確技術附件保留各自的適用邊界。來源內容、引用及 SHA 依[歷史來源](#ac-source-custody)追溯，並以本輪適用範圍為準。
