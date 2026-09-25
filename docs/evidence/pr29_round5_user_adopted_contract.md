我明確採納以下 PR #29 接續契約，取代先前草案「必須找齊所有歷史原件才能開始第五輪」的前置條件。請完成本次授權範圍，交付未合併的原 draft PR。

一、任務與固定版本

Repository：discoveryray/zhuyin-proofreader\
Task：review-confirm-responsive\
PR：[https://github.com/discoveryray/zhuyin-proofreader/pull/29](https://github.com/discoveryray/zhuyin-proofreader/pull/29)\
base／compare：develop／codex/review-confirm-responsive

B＝1593e7af65596d320b4427f1b15bb2bc0bdc949c：原固定 baseline\
H＝a38bdf1c84889c52e7281a0c23c9458e3762afa8：接續起點\
D＝502414b3b38e004a6d8d9cb693cf21b65148765a：指定整合目標

先 fetch，核對實際 refs、working tree、PR、最新 develop 的 AGENTS.md、完整規範、PR\_REVIEW\_AUTOMATION.md、VALIDATION\_POLICY.md，以及 B 的凍結契約。外部版本若已漂移，停止受影響寫入，不自行換目標；本次授權產生的正常後續 commits 不算外部漂移。

二、歷史缺件的處理

允許依 Git objects、PR 原文、現有完整報告與原始 CI，建立「本次重建的接續紀錄」，明確標示建立時間與逐項來源。

原 task／第四輪授權、歷史 gate state、早期報告等尚未取得的原件，保留名稱、已知索引與缺失狀態；不再一律作為第五輪開工或新版本驗收的前置條件。

這不追認歷史授權，不補造舊 PASS、角色/session、gate decision 或 finding resolution，也不宣稱舊紀錄完整。原 task、baseline、已知四輪計數及提交鏈保留，不另開同義任務或歸零。

現有資料能辨識的每項歷史 finding，均須對照新版本處理及驗證，不能因原報告缺失就標為已解除。缺失報告的未知內容維持未知；新審查只對新候選版本負責，不冒稱補回歷史審查。

若缺口使目前安全要求、程式來源、實際修正次數或已知重大問題無法判定，仍須列出具體阻礙；不能只以「某份歷史檔案不存在」反覆停止。若發現超過已知四輪的可靠證據，停止並更新真實計數。

停止在目前環境重複搜尋舊原件。日後取得時 append 保存；發現影響目前版本的新事實，再重新評估。

三、本次新增授權

明確追加第五輪，累計上限五輪。允許：

- 委派實作代理、兩位獨立 reviewer及建立隔離 worktree。
- 從 H 將 D merge 進原 feature，新增必要整合 commits。
- 修正 GUI 衝突、受影響測試、效能量測及必要文件。
- 實作下節限定的 gate／CI 接續支援。
- 通過第一輪審查及適用檢查後，普通 push 原分支、更新原 PR。

原四輪歷史不變。本輪首次固定候選交審後，若 confirmed code finding 需要新增受審修改，即涉及第六輪，停止交回。合法補證據及有原因的 CI 重跑不另計修正輪，不新增空 commit。

不授權 merge PR、release/tag、force push、rebase、修改 main、直接 push develop、降低保護規則、正式 DB mutation、背景 AI／額外 API 計費，或其他後續功能。

四、gate 與 CI 的有限接續

現有 gate 的三輪限制不能靠改計數、刪歷史或人工宣稱成功繞過。

允許建立限定 PR29 的接續 adapter／契約及必要測試，以 B 的 v2 安全決策為基礎：

- 精確綁定 repository、task、PR、branch、B/H/D、本次採納來源及累計第五輪。
- 區分歷史未取得資料與本次必要驗證，缺失項不能充作有效證據。
- 保留所有可取得的歷史原件、索引及 STOP；重建 snapshot 另存。
- 一般任務仍維持原限制，不新增任意放寬輪次的通用入口。
- 不授權 merge；完整決策不得輸出本次可合併或 COMPLETE。

adapter 本身須納入兩輪完整獨立審查，不能靠自己的成功輸出證明自己安全。第一輪以凍結契約及本次明確附約為依據，親讀全部差異。

必要時建模「本機候選 N、遠端仍 H」的普通 push 過渡，核對第一輪 PASS、最新直接 refs、有效授權及原 PR。保留 API 原始 base/head，不偽造已同步；push 後重新核對實際 PR 與 CI。

CI 僅增加本輪 PR29 所需的有限條件，取得 Windows Python 3.12／3.13、full unittest＋full pytest，以及既有 GUI、runtime、compile、diff 等證據。保留目前額外的測試清冊與 GUI 執行核對。不得使用假成功 context。

本輪不預先擴充未授權 merge 的 post-merge 流程。

五、整合與驗收

保留 PR29 的背景保存、增量更新與 Tk 資源清理，同時保留：

- 預覽放大與目標紅框。
- PR28 等待套用與待辦導覽。
- PR31 全部位置、紅框實際可見後人工勾選，A 不預勾。
- PR32 合法輕聲讀取。

GUI 測試改為真實新版操作，不刪安全斷言、不偽造已看過旗標。驗證保存成功才前進、寫入失敗、過期背景結果、連點／快捷鍵、外部資料異動、視窗關閉重開及資源釋放。

永久維持 actual／expected 獨立、exact identity、quorum、交易、per-PDF fingerprint、runtime integrity、fail-closed 與 repair／completion 契約。不加入撤銷新功能、跨電腦互通、預覽快取或統計。

修正期間先跑受影響測試；固定候選後取得原契約要求的完整 Windows／GUI／runtime／compile／diff 驗證與適用 CI。不得把舊測試冒稱新版本已執行。

效能以同環境、相同 fixture bytes 比較 D 與 N，分列首次／穩態、median、P95、介面停頓與記憶體。確認主要暫存情境保有加速與減少卡頓的效果；明顯退化須調查，不能只列數字就宣稱達標。保留防連點及預覽成本，真教材未測須明列。

六、審查與交付

由兩位不同 fresh session、均未參與實作的 reviewer：

- 第一輪親審 B→N 全部 cumulative diff、D 整合及接續機制。
- 第二輪獨立審完整 PR、當前 base/head/merge-base，以及適用 CI 的 run、attempt、tested SHA、parents/tree、完整 jobs 與 logs。

不先重跑舊 HEAD 的兩輪審查再重複審 N；新版本直接取得自己的完整證據。舊 PASS 不代替新結論；已知未解 findings 不得忽略。

保存完整報告、唯一 report\_ref、角色/session、SHA/scope、verdict、blocker\_kind/findings、原始證據及計數。新 HEAD 的審查不得偽裝成同 HEAD 歷史補審。

通過後更新原 draft PR，回報「未合併 PR 已交付」、B/D/N、完整修改清單、兩輪結果、測試與效能、歷史缺件及 working tree；不宣稱整合進 develop 或 COMPLETE。

本次接續證據須保存可跨電腦取回的副本，PR evidence 區保留兩輪全文與可回查索引，不能只留下某台電腦的 C 槽路徑。受審候選固定後，不為保存 PASS 再新增受審 commit。
