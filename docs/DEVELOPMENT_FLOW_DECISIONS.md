# 開發流程決策稿：2026-11-12 新版待採納方案

<a id="flow-20261112-status"></a>
## 狀態、來源與適用範圍

**本節至「歷史提案快照」之前是 2026-11-12 的新版建議，尚未採納、沒有政策效力。** 本次只匯入並修訂這一份文件；沒有帶入來源候選的其他文件、產品或工具修改，也沒有合併該候選。下列「採納後」均是未來條件，不是本次授權。

| 來源項目 | 精確定位與意義 |
| --- | --- |
| 本機工作分支／baseline | `codex/pr-flow-decisions-20261112`；`8d08c5a05d8143c353fb47b9be4f33471d99ba10`，建立時既存本機 `origin/develop`／`develop`，未 fetch；不宣稱為最新遠端狀態。該 baseline 沒有本文件。 |
| 匯入原文的 Git 候選 | `a035cd78ca684b01669e5e98403cd72bf4a96660` 的 `docs/DEVELOPMENT_FLOW_DECISIONS.md`，僅讀取此 Git object，未採納／合併候選。 |
| 原文 blob／最後修改 commit | blob `80058695268d253a00194e2276012ce697a1d3be`；最後修改 `ae0ded4f3d1005034d1ad1a8bbde9eafe409e242`。 |
| 原始檔案 bytes SHA-256（physical SHA） | `4efaebf27ab7ec19c36bd467c641947ad6f626a3b542a273ded5b92fae346f60`；只識別匯入原文，不是本次修訂檔的 hash。 |

現行權威仍是 [AGENTS.md](../AGENTS.md)、[VALIDATION_POLICY.md](VALIDATION_POLICY.md)、[完整規範](V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md)、[執行手冊](PR_REVIEW_AUTOMATION.md) 與 [gate 契約](PR_REVIEW_GATE.md)。正確性、actual／expected 獨立、證據完整性、fail-closed 與授權邊界不變；歷史 G1 的效能優先序提案不在本次 PR 流程簡化的採納範圍。

建議優先只適用**明確採納生效後的新 task**。既有 task 的 baseline、原審查、corrective 計數、STOP／BLOCKED 與凍結契約保留。本次只做文件靜態一致性、引用與 diff 檢查，沒有產品 full、新量測、正式審查 PASS、CI 或 merge。

<a id="flow-20261112-compare"></a>
## 精簡前後對照

| 項目 | 現行要求／既有成果 | 新版待採納改變 |
| --- | --- | --- |
| Reviewer | 一般任務兩位獨立 reviewer，各核完整適用 scope | 純文件／一般局部程式／核心或未知，分別 1／1／2 位；不另加分類審查輪。 |
| 修正後審查 | 新 HEAD 需重新取得兩輪完整適用 PASS | 首次完整審查；同 reviewer 在已知影響下補審新 diff、影響、原 findings 與整合，另給最新版本結論。 |
| 報告與 PR | 本機與 PR evidence 區保存完整報告 | 每份 review 一份權威原件；PR 存摘要、穩定連結與 hash，補審追加歷史。 |
| CI | PR 一次有效非視窗全集、develop merge 短驗證、取消歷史 CI 閉包／真 Tk 義務，均已存在 | 初版一般程式／核心維持當前完整 CI；純文件採必要文檢。選測與取消 obsolete PR run 另研。 |
| 工作預算 | 同 task 最多三輪 true corrective，補證據不計 | 新 task 先沿用三輪，加同 finding 連續兩輪無可核進展提前停止；不建立多維評分。 |
| 文件入口 | AGENTS、完整規範、手冊、gate 等共同描述程序 | 未來另授權整併為一個流程入口；安全／授權與歷史契約保留。 |

<a id="flow-20261112-levels"></a>
## 三級分類與 reviewer 數

分類依完整 cumulative diff 的實際語義及消費者，不依副檔名、行數或「文件整理」名稱。作者提出分類與短理由，由該級原本就必須參與的獨立 reviewer 確認；有爭議即升級，不新增專門分類輪。

| 級別 | 可成立的具體範圍 | 採納後最低審查／驗證 | 升級條件 |
| --- | --- | --- | --- |
| 純文件 | 排版、錯字、已核實導覽／連結、已知使用說明的澄清；不改下列核心契約或工具消費語義 | 1 位獨立 reviewer；核完整 diff、引用及已知消費者，必要結構／連結／render 與 diff 檢查 | 命令或操作結果改變、規範語義改變、工具讀取欄位／證據 bytes 改變、影響不能證明。 |
| 一般局部程式 | 已知邊界內的局部缺陷修正、非證據性的顯示／文案／導覽、無安全判定作用的內部整理；直接及間接消費者可列明，對外契約、identity、證據與 gate 語義不變 | 1 位獨立 reviewer；核完整 diff 與影響，開發階段必要定向檢查；正式驗證初版仍保留當前完整有效非視窗 CI 及所有必要附加檢查 | 觸及核心契約、跨邊界副作用／消費者不清、架構改變、兼容性需猜測；不能只因是產品程式就一律升最高級。 |
| 核心或未知 | 規範／命令／授權／安全、actual／expected、proof／fingerprint／證據、identity、交易／恢復、gate／CI／選測語義或未知影響；即使只有 Markdown 仍屬此級 | 2 位不同 session、獨立於實作者的 reviewer；完整初審及下述補審，當前完整有效非視窗 CI、對帳與適用整合／恢復檢查 | 不可因檔名降級；未知先 BLOCKED，釐清後按核心處理或由 reviewer 確認可證分類。 |

Mixed diff 取最高級；只有可真正獨立驗收、沒有隱藏相依的工作才可拆 task，不能拆檔規避。Reviewer 不得是本 task 的實作者或修改受審檔案者，reviewer 數不是同一作者重複簽名；安全、授權與 GitHub 保護均不因低級別減少。

<a id="flow-20261112-review"></a>
## 完整初審、有限補審與最新結論

每位必要 reviewer **首次都審 task baseline → 候選 HEAD 的完整 cumulative diff**，核對當前 PR base／merge-base、scope 與整合情境。Task baseline 不因後續修正或 base 前進而重設。

採納後，同一 reviewer 對後續候選可核對「前次受審候選 → 新候選」的新 diff、直接／間接影響、原 findings 的修正與未解狀態，以及最新 base 的整合影響；其首次完整審查原件必須可取回。更換 reviewer、架構改變、補審影響未知，均回到完整 baseline → 最新候選審查。固定 SHA／scope 任務仍不得自行擴張、rebase 或改 baseline。

這是**待採納的新補審契約**，不是現行 gate 的 `evidence_gap`：現行同 reviewer 缺口補審仍限相同 baseline／base／HEAD／scope。未同步規範與工具前，不能拿新 HEAD 使用舊契約。

每次 code review 結論可以是簡短、可追溯的補審 attestation，但必須明確綁定**最新 baseline／base／HEAD／scope**、所依完整初審／補審原件、全部未解 findings 與審查所需適用檢查。舊 HEAD／base 的 PASS 不能直接沿用；新 HEAD／base 沒有新結論即不可 merge，code finding 未解亦不可放行。協調者另將最新 code review 與當前 CI 聚合為 merge eligibility，不要求 reviewer 代簽 CI。

未受影響的個別檢查可依現行驗證政策，證明原始來源、tested SHA／tree、依賴、capture、命令、環境、範圍及 artifacts 仍適用後沿用；這只沿用檢查證據，**不自動沿用舊 verdict**。來源不適用或影響未明時補足對應檢查／完整審查，不能用「差異很小」替代證明。

<a id="flow-20261112-records"></a>
## 權威原件與三種結論分開

每份 review 的正文只存一處權威原件；初審與每次補審以不可變版本追加到同一審查紀錄，保留原 finding、修正證據、未解／已解歷史及作者／session。PR 摘要只列結論、精確版本、穩定可取回連結與各版本內容 hash；不能把兩位 reviewer 合成一份代簽。

| PR 摘要欄 | 誰負責及用途 |
| --- | --- |
| Code review | 必要 reviewer 各自給最新候選的結論與原件引用；CI 綠燈不能替代 code approval。 |
| CI／檢查 | 協調者追加當前 run／attempt／event／tested SHA、結果與適用原始證據；不得改寫 reviewer 判定。 |
| Merge eligibility | 協調者核當前授權、base／HEAD、review、未解 findings、必要 CI 與保護條件；gate 只核明確輸入條件。 |

正式 review 與 CI 可並行。CI 完成後由協調者／既有 gate 聚合，只追加 CI 欄，保留 reviewer 原 code 結論；只有 CI 暴露 finding 或候選／base 改動才需適用補審，不另要求每位核可 CI 或重寫全文。Code review 已完成而 CI 未完仍不可 merge。原件不可取回、hash 不符或引用版本不明，對應結果即 BLOCKED，補足前不使用；摘要、gate 通過與 CI rerun 都不證明原件真實性，也不能清除 finding。

<a id="flow-20261112-validation"></a>
## 驗證初版範圍

純文件執行其必要文檢；排除產品 full 的分類理由由既有 reviewer 在首次完整審查內確認，作者不能自證 CI 豁免，不加第三人分類門。一般局部程式／核心初版維持當前完整有效非視窗 pytest、inventory／JUnit 對帳、Windows Python 3.13.0／凍結依賴、runtime／compile／diff／clean，以及任務適用的整合、資料安全與恢復檢查；開發／正式驗證模式仍分開。

PR 一次 full、develop 實際 merge 的短驗證、取消歷史 CI 自動閉包、取消所有真 Tk 自動驗證，都是已採納的既有規則，不列為新版成果。產品 GUI／Tk 保留。依風險選測及取消 obsolete PR run 只列後續研究，初版不改測試選取器、取消策略或 CI coverage；本次也不啟動上述驗證。

<a id="flow-20261112-budget"></a>
## 簡單有限的工作預算

建議採納後每個新 task 仍最多 **3 輪 true corrective**：一輪是處理 confirmed code finding 的一批實際修正與回審，不能靠拆 commit、換 branch／session／工作區重設。若同一 finding 連續 **2 輪沒有可核實進展**，提前 STOP；進展須以 finding 所指問題消除／縮小的 diff 與適用驗證證明，改寫說法或重跑無關 CI 不算。

能力缺口、越界、未釐清契約或安全／完整性問題立即 STOP。純補證據不假充 corrective，也不取得無限重試權；只補已命名缺口，無法取得必要原件或證明即停並列明缺口。到上限仍需修正，交回原 findings、已耗輪數及下一步，另行授權續作也不得抹去歷史。既有 task 不重設、不解 block，不新增時間／成本／分數多維預算。

<a id="flow-20261112-entry"></a>
## 未來單一入口與精確同步範圍

建議未來以文字路徑 `docs/DEVELOPMENT_REVIEW.md` 作唯一**當前程序入口**；該路徑在本 baseline 不存在，來源候選同名草案也尚未採納。日後須另授權整理並審查其內容，不能因本次提到路徑就直接採納原草案。AGENTS 保留永久安全／授權與薄導引；舊程序保留可追溯歷史／有限 adapter，清楚標示適用版本。

未來另批同步清單如下；本次一律不改：

- 文件：`AGENTS.md`、`docs/DEVELOPMENT_REVIEW.md`、`docs/CHATGPT_PROJECT_INSTRUCTIONS.md`、`docs/VALIDATION_POLICY.md`、`docs/V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md`、`docs/PR_REVIEW_AUTOMATION.md`、`docs/PR_REVIEW_GATE.md`、`TESTING.md`，並更新本決策稿的採納狀態。
- 設定／四角色：`.codex/config.toml`、`.codex/agents/coordinator.toml`、`.codex/agents/implementer.toml`、`.codex/agents/reviewer_cumulative.toml`、`.codex/agents/reviewer_pr.toml`。
- 工具／CI：`scripts/pr_review_gate.py`、`scripts/validation_runner.py`、`scripts/validation_evidence.py`、`scripts/test_entrypoint_audit.py`、`scripts/test_group_registry.json`、`.github/workflows/ci.yml`，以及各變更對應的既有測試與必要負控。
- 歷史：`scripts/pr29_review_gate.py` 及精確 adapter 另作隔離／相容評估，不為簡化而整批改寫／刪除；移除舊 adapter 不能釋放歷史 STOP 或重設計數。

Gate 只檢查用途清楚的**當前 merge 條件**，不自證授權、不證明原件真實性，也不代替 reviewer 讀 diff。新版流程改革本身依事前凍結的舊規則，或使用者在執行前明確採納的有限 transition 驗收；不能用受審新 gate 放行自身。

<a id="flow-20261112-batches"></a>
## 分批實作與每批驗收

| 未來批次 | 範圍 | 該批可核驗收 |
| --- | --- | --- |
| 1 採納與文件／角色 | 固定生效點、只適用新 task、transition、原件留存位置；整併入口及同步角色 | 每條當前規則有唯一入口；舊 task／STOP／counter 與安全規則不被覆寫；未實作工具差距明列，不能先宣稱新流程可用。 |
| 2 Review／原件／gate | 實作 1／1／2、完整初審／有限補審、最新 attestation、單一原件與分欄摘要 | 下表接受／拒絕情境均有對應靜態或工具負控；舊 schema／adapter 仍按原義；缺原件、未知與 stale verdict 拒絕。 |
| 3 文檢／CI 整合 | 純文件必要文檢、一般／核心保留現行完整 CI、review／CI 並行 | 實際 consumer、角色與 CI 規則一致；完整 inventory／coverage 不減；CI 完成只補必要結果；不夾帶選測或 obsolete run 取消。 |

本輪不授權後續實作；後續須取得涵蓋相應批次的明確授權，若一次已涵蓋各批則依序接續，不例行逐批重問；各批仍按凍結舊規則／事前 transition 驗收。只有文件已寫而 consumer 未同步時，記為未生效。選測、run 取消與產品研究不阻擋前述 PR 改革，也不被這三批順帶授權。

<a id="flow-20261112-cases"></a>
## 靜態驗收情境

此表是未來實作的接受／拒絕要求；本次只核對條文一致性，未執行新 gate 或宣稱正式 PASS。

| 情境 | 必要結果 |
| --- | --- |
| 純文件混入核心命令／契約修改 | Mixed 取核心 2 位；若真獨立拆分，分別固定 scope 與相依證明。 |
| Markdown 核心被誤列純文件；一般局部程式僅因副檔名升核心 | Reviewer 更正語義分類；前者升核心，後者以實際契約／消費者確認一般級，不另加分類輪。 |
| 舊 HEAD 或 base 的 PASS；最新 baseline／scope 不符 | 不可 merge；保留原件，核最新 diff／整合並取得綁最新版本結論，不能自動沿舊 PASS。 |
| 仍有未解 code finding | BLOCKED；CI 綠燈、摘要或補證據不可清除，真修正計入原 task 輪數。 |
| 必要 CI 失敗／未完／漏跑 | Code review 可完成，但 merge eligibility 拒絕；只補當前必要證據，不重寫全文。 |
| 原報告缺失、不可得或 hash 不符 | Evidence BLOCKED，補足可驗原件前不使用該 review。 |
| 補審超出已知影響、換 reviewer／架構改變；沿用檢查來源不適用 | 前三者完整 baseline → 最新候選審查；來源不適用者補對應檢查，未補足不得給可用最終結論。 |
| 無法分類、未知欄位／相容性或影響範圍 | 預設 BLOCKED／升核心；不得以 unknown＝success 或 allowlist 猜測放行。 |

<a id="flow-20261112-product"></a>
## T1／T2／T3：另行研究，不阻擋 PR 改革

三項均待裁定，沒有產品修改授權，也不阻擋 PR 改革。提案來源 D 為文首 `a035cd78ca684b01669e5e98403cd72bf4a96660`：`D:docs/PERFORMANCE_DECISIONS.md`「三項產品信任標準」、`D:docs/PERFORMANCE_NEXT_SCOPE_PLAN.md`「開發流程及信任標準」、`D:docs/PERFORMANCE_CHANGE_BATCHES.md` 的 BT2／BT3；這些候選文件不匯入、不作本地有效連結。下列現行契約以文首 baseline B 為準。

**T1：proof 防錯與成本比較。** B 的 [manual surface actual 契約](MANUAL_SURFACE_ACTUAL_CONTRACT.md)，交叉定位 [actual_source_visual_safety.py](../actual_source_visual_safety.py) 的 `_native_clip_proof`／`_native_visibility_proof`／`surface_visual_proof`，及 [manual_surface_actual.py](../manual_surface_actual.py) 的 `validate_proof`／`verify_sources`：PDF SHA／session／occurrence 頁／source tuple 防錯來源；native trace／outline coverage 防缺成員輪廓、錯 tuple 與 unknown composition；full replay／vendor render 核最終畫面。`without`／`full` 差只證非零來源貢獻，不能證完整可讀；`prefix`／`prefix_without` 檢後續繪製疊蓋／背景；PNG hashes／proof digest 防圖片替換。Whole-unit 人工讀音／receipt 補機器未證的可讀性，不能從 expected 預填；surface 可經人工明示接受已知疊改，但不能升為 raw glyph donor／quorum。這是來源交叉定位，不表示本次已執行 proof 驗證。

T1 只提出比較方法：同 PDF／位置，以獨立來源真值與人工可讀標籤，成對比較現行 proof 與未採納輕量候選；涵蓋正常、錯位、遮蓋、全遮、缺成員、偽圖、unknown 正負例，記錄等價輸出、誤受／誤拒、理由／不能判定及各段成本。不能以現行 validator 本身作唯一真值；哪些 proof 可替代、何種等價標準可接受仍待裁定，本次不跑實驗。

**T2：交易批次／恢復設計。** B 的 [manual surface actual 契約](MANUAL_SURFACE_ACTUAL_CONTRACT.md) 現行 `surface-only-all-pending/1` 不允許 subset。新研究設 A／B／C 三筆 pending 而只選 A／B，固定全原像 S0／後像 S1：成功時 A／B 各保存一次及各自 receipt，C 完整 pending 及原 receipt 保持，不新增或改寫 C 的確認資料；首個 durable 寫入前中斷須維持 S0。PREPARED 後只依凍結 owner／plan／exact states 恢復，不得 late baseline 或順便納 C；COMMITTED 後 publication／ack／refresh 失敗須如實標示已提交待恢復，不能假稱未提交。

T2 重試須 idempotent，不重複 history／actor／time／receipt、不吸收新 pending；foreign bytes 拒絕覆寫或 rebaseline，保全未選 C。A／B 同批 all-or-nothing 或逐筆允許部分完成的原子性取捨尚待裁定；不得把 subset 塞進舊 `/2` journal 原語義，每筆 proof／來源／完整性證據強度不降。成功、中斷、恢復、重試與 C 保全皆須另立驗收，不能只證明快路徑成功。

**T3：相關來源與專家核准。** B 的 [DAILY_GLYPH_LEARNING.md](DAILY_GLYPH_LEARNING.md)「保存／啟用／重試、證據相容」及[完整規範](V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md) §3.1–3.4／Phase 4：fresh `DIRECT_VISUAL_ACTUAL` 的同一 pair 須同時滿足 project／session、PDF SHA、font-program SHA、occurrence、review 五軸獨立，不能拼接不同 distinct 總數湊 pair；approval 另綁 roster／digest／policy／revision，surface／legacy 不算新 vote。反向 direct reading 仍 quarantine，不藉研究升格。

T3 待研究不同 ID 是否同模板／同錯誤鏈的相關來源；專家資格、scope、直接證據、留痕、撤銷、stale／反例處理及是否仍須 fresh quorum 均須裁定，不能假設已有 expert bypass。T1／T2／T3 未裁定前維持全部現行 proof、每筆交易與信任邊界，不借流程改革改產品。

<a id="flow-20261112-open"></a>
## 少數待裁定與未生效項目

待裁定聚焦：一般局部程式的專案邊界案例；建議 3 輪／連續 2 輪無進展的預算值；新 task 生效點及改革自身 transition；權威原件實際留存位置／存取與保存責任。1／1／2 是本版具體建議，不再加一輪分類或評分決策。

**未生效：** reviewer 減量、新 HEAD 補審、報告分流、新入口與 gate／角色／CI 同步、選測／run 取消、T1／T2／T3 全部未採納或未實作。本次沒有產品變更、full／新量測、安裝、遠端操作或政策啟用。P5 工作管理已結案；其 STOP、原三輪／第四輪歷史、baseline、計數及 symlink 中斷原義不動，不因本稿重啟、解除或追認成功。

---

<a id="flow-history"></a>
## 歷史提案快照：原 G1–G5 與 df-* anchors

以下保留匯入原文的 G1–G5、T1–T3 提及與所有 `df-*` anchors，供追溯原提案。**其中「本輪」「現規」「本次三份」「1/3」「P5／P9 未採納」及其他時態均只描述來源候選當時，不是 2026-11-12 現況或授權。** 新版建議見本文前半；歷史內容不創造政策效力。

候選專屬且本 baseline 不存在的文件不建立，也不當作本地有效連結；下列改以原路徑與來源候選 SHA 文字標記，原提案語義保留。原始完整 bytes 可由文首 blob／physical SHA 追溯；這個歷史副本只新增本標示並調整該等引用。

<a id="df-status"></a>
## 本輪只提出決策

**最新規畫導覽：** 剩餘項目取捨與建議本次 scope 見下一批效能規畫（來源 `a035cd78ca684b01669e5e98403cd72bf4a96660:docs/PERFORMANCE_NEXT_SCOPE_PLAN.md#next-scope-summary`），N0–N5 排序（來源 `a035cd78ca684b01669e5e98403cd72bf4a96660:docs/PERFORMANCE_NEXT_SCOPE_PLAN.md#next-scope-batches`）另列。本輪只規畫；P5／P9 仍未採納、未授權實作，G1–G5／T1–T3 現規不變。原 task／baseline、舊 snapshot 與 corrective 1/3 保持，不把建議改寫為已採納。

2026-11-10 本輪只改文件，不改程式、規範入口或工具，不推送、建立 PR、啟動 CI 或合併。以下 G1–G5 **尚待採納**，不取代 [AGENTS.md](../AGENTS.md)、[現行驗證政策](VALIDATION_POLICY.md)、[完整規範](V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md) 或 [gate 契約](PR_REVIEW_GATE.md)。本輪修改的規範語義不能因副檔名為 Markdown 被視為低風險，也不能用本提案放行自身。

現行程序仍是開發模式短檢查、固定本機候選及獨立審查；正式驗證、兩輪正式 PASS、必要 CI、合併與授權條件各自有效。使用者要求「寫決策稿」並未採納新政策或說「開始驗證」。所有下列同步檔／工具只是日後範圍清單，**本輪不修改**。任務見 B02（來源 `a035cd78ca684b01669e5e98403cd72bf4a96660:docs/PERFORMANCE_CHANGE_BATCHES.md#b02`）；產品效能批次可依現規推進，不必等待流程改革。

<a id="df-matrix"></a>
## 五項決策與採納條件

| ID | 現行要求 | 建議條文 | 採納前必須固定的條件 | 日後同步檔／工具 |
| --- | --- | --- | --- | --- |
| G1 驗收優先序 | [AGENTS §1](../AGENTS.md)「最後才是便利性與效能」 | 正確性、安全結果及可接受等待時間並列驗收；預先定義使用情境、cold／warm、資料規模與分段量測，不能靠放寬安全換達標 | 同時選定正確性負控與可量測的等待預算；超時就是尚未達成效能目標，不掩飾為完成，也不以性能理由略過完整性 | AGENTS、架構草案（來源 `a035cd78ca684b01669e5e98403cd72bf4a96660:docs/ARCHITECTURE_CONTRACTS.md`）、[完整規範](V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md)、各任務驗收／benchmark 說明 |
| G2 依風險安排審查 | AGENTS §17 要求兩輪完整適用獨立審查 | 依下表有限分級調整低風險的審查範圍；核心行為、證據／交易與驗證 gate 保留兩位完整獨立審查 | 人類明確採納分級、客觀 allowlist／升級規則與每級 reviewer 數；協調者提出分類，獨立 reviewer 確認，實作者不能自降級 | AGENTS、DEVELOPMENT_REVIEW（來源 `a035cd78ca684b01669e5e98403cd72bf4a96660:docs/DEVELOPMENT_REVIEW.md`）、[PR_REVIEW_AUTOMATION](PR_REVIEW_AUTOMATION.md)、完整規範、[四角色設定](../.codex/agents/)、[pr_review_gate.py](../scripts/pr_review_gate.py)、其測試 |
| G3 依影響選驗證 | [VALIDATION_POLICY](VALIDATION_POLICY.md) 一般正式 PR 需完整有效非視窗 pytest／對帳與必要附加檢查 | 純排版／有限內容調整依證明過的影響範圍執行；產品程式、核心契約、CI／gate 或未知影響仍完整驗證 | 固定每級必測清單及來源／輸出證明；mixed diff 取最高風險，不靠檔名／副檔名／行數排除；無範圍證明就升級 | VALIDATION_POLICY、[TESTING](../TESTING.md)、[validation_runner.py](../scripts/validation_runner.py)、[validation_evidence.py](../scripts/validation_evidence.py)、[test_entrypoint_audit.py](../scripts/test_entrypoint_audit.py)、[CI](../.github/workflows/ci.yml)、gate 及相關測試 |
| G4 修正停止條件 | AGENTS §17 同 task 最多三輪 corrective，non-code 補證據不計 | 可按事前時間／成本預算、重複根因與改善趨勢決定停止，保留有限自動修正界線；不因仍想嘗試就無限接續 | 採納時必須有可量測預算、耗用計法、最低收斂證據、重複同根因停止規則及人類續做入口；未採納仍三輪 | AGENTS、DEVELOPMENT_REVIEW、完整規範、PR_REVIEW_AUTOMATION、PR_REVIEW_GATE、pr_review_gate.py、歷史 adapter 的明確適用評估與測試 |
| G5 原報告與摘要分流 | AGENTS §17／執行手冊要求本機與 PR evidence 區保留兩輪完整報告 | 每位 reviewer 各自保留一份不可變原報告；其他位置只存其摘要、精確版本、內容 hash 與可存取連結，補審追加新原件 | 指定原件存放／留存／權限／可取回性、hash 校驗與缺件處理；兩位獨立原報告仍存在，不能一份替兩人；採用前工具須能核對 | AGENTS、DEVELOPMENT_REVIEW、PR_REVIEW_AUTOMATION、PR_REVIEW_GATE、pr_review_gate.py、PR evidence 模板及對應測試 |

<a id="df-levels"></a>
## G2／G3 的有限分級候選

下列是供採納的具體方案，並非現行免審／免測權限。風險判斷對完整 cumulative diff 與實際消費者負責；一個任務混有不同級別，整體依最高級別，除非真正獨立拆分且沒有隱藏相依，不能靠拆檔規避。

| 級別 | 客觀適用範圍 | 建議審查／驗證 | 必須升級的情況 |
| --- | --- | --- | --- |
| L：純呈現 | 只有 Markdown／靜態文件的空白、換行、標點或排版；不改語義、命令、連結目標、程式碼區塊、規範詞、工具會解析的欄位、來源證據 bytes | 一位獨立 reviewer 核完整 diff 及消費者；文件結構／連結／render（適用時）、diff／scope 檢查；不要求產品 full | 無法證明語義未變、檔案被工具讀取、產物 hash／引用契約受影響、混入行為修改 |
| M：有限非規範內容 | 僅使用說明／導覽的已知錯字、非規範說明或已核實連結修正；不涉及操作命令、授權、產品答案、資料格式、安全／交易／恢復、測試 gate | 一位獨立 reviewer 核完整 diff、所有直接引用及可證有限消費者；執行已命名的文件／連結／相關消費者檢查，保留選取理由與排除證據 | 出現其他未列入的消費者、入口／命令改變、使用者操作語義改變、需要推測影響、任一負控失敗 |
| H：行為、核心或未知 | 任何產品程式、核心契約、資料／identity／proof／fingerprint／交易、規範指令、CI／gate／測試選取工具；無法分類或超出 L／M allowlist | 保留兩位完整獨立審查及現行正式非視窗全集、對帳、runtime／compile／diff／CI 與適用整合／恢復驗證；開發模式仍先必要短檢查 | 不降級；若任務越界或證據不足，依原 BLOCKED 類別處理 |

分類紀錄至少含候選 SHA、完整 changed files、實際讀取該檔的入口／parser、風險理由、所選檢查與負控、reviewer 確認。Reviewer 發現更高影響即升級；既有高風險工作不因名稱改成「文件整理」轉為 L／M。本次三份決策稿及契約候選修改屬規範語義，保留 H 的審查判斷；本輪明確只做文件階段，不啟動正式測試／CI。

<a id="df-corrections"></a>
## G4 的停止與原任務歷史

採納新的停止規則時，必須在執行前具體記錄時間上限、成本／呼叫或其他可核實資源上限、最大自動修正次數的保底界線、何謂收斂，以及重複同根因的停止條件；任一上限到達或安全證據無法取得就停止，交付原 findings、已耗用預算與下一步選項。不能用「有改善」無限制延長，也不能讓 reviewer 的成功替代人類續做授權。

未採納前仍維持同 task 三輪規則及所有既有有限例外。分批、換分支／session、改檔案名稱、重新開工作區或補原件不重設 baseline、counter 或已確認 blocker；non-code 補證據仍按原分類處理。真正獨立新任務須保存其來源、目標與和舊 findings 的關係，不可用新 task 藏掉舊失敗。新制度如何處理未結案任務須由人類事前明示，不能追認舊 PASS 或覆寫原報告。

<a id="df-reports"></a>
## G5 的單一原件與獨立性

「單一原件」是每份報告只有一個權威 immutable 版本，不是整個任務只剩一位 reviewer 的報告。Round 1 與 round 2 各自保留獨立作者／session、完整 scope、baseline／base／HEAD、原 evidence references、verdict／findings、唯一 report_ref 與內容 hash。摘要只指出該原件與相應版本，不能改寫判定或把 CODE_REVIEWED 當正式 PASS。

補審、supersedes／finalizes 及 resolution evidence 仍新增不可變原件並保存原歷史；連結必須可由審查者取回正確 bytes，不能只有短效本機路徑或權限不足的 URL。原件不可得／hash 不符時停止使用該結果，按 evidence 缺口補足，不捏造重複報告、不藉 CI rerun 清除 finding。只核當次必要 evidence，並不重建已取消的歷史 CI 全閉包；原失敗與 task 計數仍保留。

<a id="df-adoption"></a>
## 採納及工具同步的順序

1. 人類按 G1–G5 逐項決定採納範圍、適用新舊任務、明確生效點與過渡驗收。可以只採納部分，產品 P 項不以全套流程改革為前置條件。
2. 固定當時有效舊契約與原始決策；制度變更本身依該凍結規則驗收，或依人類在執行前明確指定的有限過渡驗收。不能用受審新 gate 證明自身已獲授權、已 PASS 或可 merge。
3. 在另行授權批次同步規範、角色、gate、runner、CI、證據格式與必要負控。只改文件而 consumer 仍舊規則時，明列未生效及缺口；不得讓 dummy check、缺省成功或寬鬆 unknown fallback 繞過舊門檻。
4. 檢驗純排版誤混指令、M 級新增消費者、H 級誤降級、缺原報告／hash 錯誤、同根因重複與計數跨批延續；確認原失敗仍在。正式採納和實際工具啟用狀態分開記錄。

本輪停在決策稿與任務卡，保留先前暫停／遠端操作受拒及舊審查紀錄；新 HEAD 不沿用舊 HEAD 的 PASS。未實測的開發工時節省不提供估算。
