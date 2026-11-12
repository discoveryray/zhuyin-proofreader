# 開發與獨立審查流程入口

本文件是開發、審查、驗證及合併的唯一程序導航。[AGENTS.md](../AGENTS.md) 仍是永久安全與授權規範；使用者當次較窄限制優先。先判定適用版本，再讀所需專屬契約，不以歷史快照、候選文件或受審工具創造授權。

<a id="applicability"></a>
## 適用版本與目前狀態

| 版本／工作 | 狀態與入口 |
| --- | --- |
| 現行流程 | **目前仍適用舊制兩位獨立 reviewer、完整適用審查及既有 gate v3／v4**；依下節導航執行。現行驗證政策為 `validation-flow/current-evidence/1`，真視窗義務依 `validation-flow/no-real-tk/1` 取消。 |
| `development-flow/tiered-review/1` | **設計已採納、首版已縮減、尚未啟用**。保留分級審查與生命週期，所有 PR 仍走既有 full；文件免 full、parser、專屬 producer／artifact／post-merge 均延後。本文件的待驗收章不能用於放行現行任務。 |
| 本次改革 | Task `pr-flow-decisions-20261112`；原 baseline `8d08c5a05d8143c353fb47b9be4f33471d99ba10` 保留，固定實作起點 `42b1985b43aa8ba62bff93edde8583c03d4da5c9`。改革本身屬核心，按起點舊規取得兩位獨立完整 code review 及必要定向檢查；目前不宣稱正式 PASS。 |
| 舊 task／歷史例外 | 保留原 task、baseline／source_ref、原件、判定、corrective count、STOP／BLOCKED 及精確 adapter；既有已採納驗證政策的有限遷移仍依其原採納條款。新政策不重啟或完成舊 task。 |

新政策的程式啟用常數為 `scripts/pr_flow_policy.py::ENABLED = False`；新 gate `zhuyin-pr-review-gate/5` 的公開路由必須回報 pending activation／STOP。State 欄位、宣稱採用、測試 fixture 或文件採納都不能繞過它。**正式驗收、整合及有效授權完成後，才可另以明確版本與生效點對新 task 啟用**；修改常數本身不足以取得 merge eligibility，所有適用審查、檢查、授權及保護條件仍必要。舊 v3／v4 保持原判定。

本次三批已獲依序接續授權，僅限本機實作與必要定向驗證；沒有 fetch、push、PR、遠端 CI、merge、安裝或資料操作授權。這是本次 scope，不改寫一般授權規則。

後續[首版縮減裁定](DEVELOPMENT_FLOW_DECISIONS.md#flow-first-edition-20261112)從 `822d4941b6913dd62b75eb49615c156fb248641b` 追加原 task 一次限定第四輪 4/4。前三輪與兩份 parser `BLOCKED/code` 保持；只記當次例外，不改一般三輪政策、不採納 G4。第四輪固定候選若仍有 confirmed code blocker，STOP，不自啟第五輪。

<a id="current"></a>
## 現行舊規章：本次仍適用

本章選定目前可執行契約；下列文件是專屬細節或保留原文，不能各自選擇較寬鬆版本。

| 需要判定的事項 | 直接來源與用途 |
| --- | --- |
| 安全、授權、scope、停止條件、完成宣告 | [AGENTS.md §§1–16](../AGENTS.md)；永久 actual／expected、identity、fingerprint、runtime integrity 及 fail-closed 邊界保持。 |
| 現行兩位獨立 reviewer、完整 scope、新 HEAD、non-code 同 HEAD 補審、原三輪上限 | [AGENTS.md §17 保留契約](../AGENTS.md#current-review-contract)。這仍是現行規則，不是被新入口撤銷的舊稿。 |
| 開發／正式驗證、當次證據、有效非視窗全集、沿用、actual merge 短驗證 | [VALIDATION_POLICY.md](VALIDATION_POLICY.md) 文首現行政策與開發／驗證章；後段精確 task／歷史段落按其標示適用。 |
| 代理能力、隔離、唯一 PR、遠端重核、merge 副作用及中斷恢復 | [PR_REVIEW_AUTOMATION.md](PR_REVIEW_AUTOMATION.md)；只執行當次已授權動作。 |
| v3／v4 schema、報告 relation、BLOCKED 分類、有限歷史 adapter | [PR_REVIEW_GATE.md](PR_REVIEW_GATE.md)；gate 核輸入一致性，不能認證真實性或代審。 |
| 測試命令、依賴與 artifact 操作 | [TESTING.md](../TESTING.md)；開發模式不因此取得昂貴驗證時段。 |
| 產品架構／安全及 v5.8 階段契約 | [完整規範 §§3–4](V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md#3-不可破壞的核心原則)；其餘程序段落供現行舊制專屬細節與歷史定位。 |

現行流程不套用下章的 1／1／2、跨候選 delta 補審、單一原件 PR 摘要或 reviewer／CI 分離結論。尤其舊 `evidence_gap` 只處理其原 schema 允許的相同 baseline／base／HEAD／scope；不能拿來接受新 HEAD 的 delta review。本次只可回報「已修改、待正式驗證」及實際 code review 結果。

<a id="pending"></a>
## 已採納但未啟用政策：development-flow/tiered-review/1

本章是新政策的唯一程序規則來源。採納依據與原提案差異見[決策採納附記](DEVELOPMENT_FLOW_DECISIONS.md#flow-adoption-20261112)；工具有限欄位另由 [gate 契約](PR_REVIEW_GATE.md) 說明。本章不改產品安全、持續授權、GitHub 保護或正式驗證啟動條件。

<a id="pending-classification"></a>
### 1. 依語義分類，由必要 reviewer 確認

作者依完整 cumulative diff 提出分類及短理由，由該級既有的必要 reviewer 在首次審查內確認，不新增分類輪。有爭議升級；未知先 BLOCKED，釐清後按已證明的級別處理。Mixed diff 取最高級，只有可獨立驗收且無隱藏相依的工作可拆 task，不以拆檔規避。

| 級別 | 可成立範圍與升級條件 | 最低獨立 reviewer／驗證 |
| --- | --- | --- |
| 純文件 | 排版、錯字、已核實連結／導覽或既有說明澄清；不改規範、命令結果、授權、工具消費語義或證據 bytes。任一契約改變或影響不明即升級。 | **1 位**；完整 diff、直接消費者及必要內容／連結確認由 reviewer 如實記錄；正式時段仍須既有完整非視窗 CI，沒有 full 豁免。 |
| 一般局部程式 | 已知邊界內局部修正、非證據性的顯示／文案／導覽或內部整理；直接及間接消費者可列明，安全判定、對外契約、identity、證據與 gate 語義不變。核心、架構或未知副作用升級；不能只因是產品程式就一律升核心。 | **1 位**；完整初審、開發時必要定向檢查；正式驗證保留當前完整有效非視窗 CI 及適用附加檢查。 |
| 核心或未知 | 規範／命令／授權／安全、actual／expected、proof／fingerprint／證據、identity、交易／恢復、gate／CI／選測語義，或影響未知。Markdown 也可能屬核心。 | **2 位不同 session**；各自完整初審、適用補審，以及當前完整有效非視窗 CI、對帳與必要整合／恢復檢查。 |

Reviewer 必須獨立於所有本 task 實作者；修改受審文件、測試或設定的協調者也屬實作者。Reviewer 不修改受審檔案，不以另一位 verdict、實作摘要或 CI 綠燈代替親自審查；隔離測試如實記錄權限及環境限制。

<a id="pending-review"></a>
### 2. 完整初審、有限補審、最新候選結論

每位必要 reviewer 首次親自審固定 task baseline → 候選 HEAD 的完整 cumulative diff，核對當前 base／merge-base、scope、直接及間接消費者、安全契約與整合情境。Baseline 不因後續修正或 base 前進重設。

同一 reviewer 後續可在可取回其完整初審及相關補審原件的前提下，審前次候選 → 最新候選的新 diff、影響、原 findings 的修正／未解狀態及最新 base 整合情境。每次都給綁定最新 baseline／base／HEAD／scope 的新結論。**換 reviewer、架構改變或補審影響未知，須回到完整 baseline → 最新候選審查。** 固定 SHA／scope 不因這項補審取得 rebase、改 baseline 或擴 scope 授權。

原 findings 保留歷史；未解 code finding 一律阻擋。舊 HEAD／base 的 PASS 不自動適用新候選，CI rerun、摘要或 unrelated PASS 不能清除它。既有檢查只有依現行驗證政策證明原件、tested SHA／tree、依賴、環境、capture、命令、scope 與 artifacts 仍適用才可沿用；沿用檢查不等於沿用舊 verdict。缺乏影響證明即補足審查／檢查。

<a id="pending-records"></a>
### 3. 每個 review 版本一份 canonical 原件

完整初審與每次補審各有不可變、可取回的 canonical 版本，保留 reviewer／session、task、baseline／base／HEAD／scope、分類確認、所依初審／補審版本、findings 的未解／已解歷史、修正依據及最新結論。可保存 **same-bytes 備份**，但不能把不同內容或 PR 摘要當同一權威版本。兩位 reviewer 各自負責原件，不能合成一份代簽。

PR 只保存各版本摘要、穩定可取回引用、版本識別及內容 hash；正文不再複製到 PR。原件缺失、不可取回、hash 不符或引用版本不明，對應 review 即 evidence BLOCKED，補足前不可使用。協調者保存原件與來源鏈，不為記錄結論新增空 commit 或改受審 HEAD。

<a id="pending-conclusions"></a>
### 4. Code review、checks 與 merge eligibility 分開

| 結果 | 責任及必要內容 |
| --- | --- |
| Code review | 必要 reviewer 各自提供最新候選結論與 canonical 版本引用，親自負責 diff／影響／findings／整合判斷。 |
| CI／checks | 協調者核實當前 run／attempt／event／tested SHA、原件、結果及適用性，追加獨立檢查紀錄。 |
| Merge eligibility | 協調者／既有 gate 聚合最新候選 review、未解 findings、必要 checks、有效授權、精確 base／HEAD 及保護條件；不能自證授權或原件真實性。 |

Code review 可與 CI 並行。CI 完成後追加 checks／eligibility 結果，不要求 reviewer 代簽 CI 或重寫全文；只有 CI 暴露 finding、候選／base 改變或新影響才需適用補審。必要 CI 未完、失敗、漏跑或來源不適用時，即使 code review 完成也不可 merge。Actual merge、當次 push 短驗證及完成宣告仍依現行驗證政策。

<a id="pending-validation"></a>
### 5. 初版驗證與原工作預算

首版 docs／local／core **全部**保留既有完整有效非視窗 pytest、inventory／JUnit 對帳、Windows Python 3.13.0／凍結依賴、runtime／compile／diff／clean，以及任務適用的整合、資料安全與恢復檢查。分類只決定必要 reviewer 數，不取得測試豁免。開發模式先做必要定向檢查；正式時段仍須既有明確啟動授權。

Reviewer 親自確認任務必要的文件內容、連結、anchor 或 render，並在既有 canonical `impact_summary`／`review_checks` 留下實際範圍、方法、原件及未完成限制；不新增作者勾選式自證欄位。Full pytest 不表示這些確認已做完，缺必要確認不能由 full 成功補造。

PR 一次 full、develop actual merge 短驗證、取消歷史 CI 自動閉包與真 Tk 自動驗證均為既有成果。初版不改選測或取消 obsolete PR run；產品 GUI／Tk 保留。

<a id="pending-checks-routes"></a>
#### 首版：分級審查，一條 full 檢查路徑

| 分類 | 必要獨立 reviewer | 合併前／合併後檢查 |
| --- | --- | --- |
| docs | 1 | 既有當次 full coverage `/3`／actual merge short `/3`；另由 reviewer 如實記錄必要文件確認。 |
| local | 1 | 同上完整 CI 與適用整合檢查，不能以文件原件替代。 |
| core／影響未知 | 2；未知影響仍先 STOP | 同上完整 CI 與適用安全／恢復檢查，未解 findings 仍阻擋。 |

首版不消費 `pr-flow-documentation-checks/1`、`/2`、文件專屬 short、capture／producer 或中立文檢 sidecar。`checks_route` 只接受 `full`，保留的 `documentation_checks` 槽必為 null；舊研究 docs／fallback 路徑及其「成功」原件不能成為首版放行證據。這不是把 full 改名成 docs，也不是宣稱 parser 已修復。

Workflow／summary 與 gate／post-merge 優先重用既有 full `/3` 與 actual merge short `/3` consumer，沒有研究 plan／parser 原件的強制依賴。缺 capture／producer 不妨礙正常 full。Main push 的原完整驗證保持。公開入口仍鎖定未啟用，不能由 state／環境變數開啟。

**G4 延後：沿用同 task 最多三輪 confirmed code corrective，不增加「同 finding 連續兩輪無進展」提前停止規則。** 只有確定 code finding 的實際修正消耗 corrective count；evidence 補證據、capability／contract STOP 仍依既有分類。不能藉拆 commit、換 branch／session／工作區、改 task 或新政策重設歷史；既有限定例外按原授權及有限 adapter，無額外一般額度。

<a id="pending-transition"></a>
### 6. 驗收與啟用邊界

完成縮減後的直接 consumer 與相應定向負控，仍須按起點有效舊規由兩位獨立 reviewer 審完整累積差異及本次縮減，再等待正式驗收。受審新 gate、fixtures 或本文件不能放行本次改革；政策保持未啟用。

首版保留 canonical 原件鏈、分類／code review 與 checks 分離評估，以及有限 v5 生命週期。來源與命令見 [TESTING](../TESTING.md#pending-pr-flow-ci)，完整判定與啟用鎖的分離見 [v5 待啟用契約](PR_REVIEW_GATE.md#pending-v5)。協調者仍從實際獨立 reviewer 取得可取回原件並核來源；作者 JSON、label、hash 或布林值不能認證身分。研究專用 Actions capture／producer 不再是首版前置。

**仍需補齊的正式前置：** 六項凍結依賴、真實當次 hosted／full 與 actual merge push 證據，以及原 gate 對 `752→3b` 功能續作歷史的適用表示。保留真實 corrective 端點，不捏造連續鏈或新增 adapter。本輪不執行正式全集、不安裝、不發遠端操作；模型原件／API fixture 與真正 CI 結果分列，不能宣稱正式 PASS。

自製 parser、文件豁免路徑與研究測試已延後；原 bytes／oracles 以 [822d494 研究來源附件](research/PR_FLOW_DEFERRED_822d494.zip)保存，索引見[決策稿](DEVELOPMENT_FLOW_DECISIONS.md#flow-first-edition-20261112)與原 task INDEX。RA5-F1／RB5-F1 保留未修復判定。研究不在首版入口、授權或 active pytest 收集路徑；若未來另案恢復，須明確 scope／授權與驗收，不能因檔案存在自行重啟。

之後正式啟用須保存明確政策版本、生效點與新 task 適用性；舊 task 的基線、原審查、計數、STOP／BLOCKED、原件與有限例外保持。G1、T1／T2／T3 均未採納，不改產品 proof、交易／恢復、來源信任或安全優先序；P5 工作管理已結案，原 STOP、三輪／第四輪歷史及 symlink 中斷原義不動，不讀寫其工作樹。

候選外文件與未授權研究只以文字來源定位，不建立不存在的本地連結。歷史決策及保留 anchors 見[決策稿歷史](DEVELOPMENT_FLOW_DECISIONS.md#flow-history)。
