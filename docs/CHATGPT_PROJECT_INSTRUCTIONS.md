# 可貼入 ChatGPT 專案指令

本專案為 discoveryray/zhuyin-proofreader。每次開始相關任務，先讀實際 AGENTS.md 與 docs/V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md；流程細節見 docs/PR_REVIEW_AUTOMATION.md。未指定 ref 時，從核對後的最新 develop 讀取規範；不要以 GitHub 預設 main 的舊版代替。受審 branch 的規範變更仍須與已採納契約交叉核對。以即時 Git／PR／CI 為事實，不沿用歷史 SHA 或狀態。

我明確交付的開發任務，預設授權該任務範圍與階段內的檔案修改、代理委派、測試、commit、push、建立／更新 PR，及通過全部門檻後以 Create a merge commit 合併至 develop、執行 post-merge 驗證。我可另行收窄或撤銷；純討論／唯讀審查不屬於開發交付。不要逐步要求轉貼或重複授權，也不自行開始未交付階段。

由協調者自動安排實作代理、第一輪 cumulative reviewer、第二輪 PR reviewer。Work 或符合完整契約的獨立代理均可正式審查。兩位 reviewer 必須是不同 session、均與所有實作者分離、不修改受審檔案，親自審查完整差異及安全契約；不能用實作摘要、另一輪 PASS 或 CI 綠燈代替審查。

第一輪 PASS 後查找並建立／更新唯一 PR；第二輪 PASS 且必要 CI 全通過才 merge。BLOCKED 交回實作代理新增 corrective commit，重跑必要測試，重新取得兩輪完整 cumulative PASS；同一 task 最多自動修正三輪，跨重啟保留計數。HEAD／base 改變時舊 PASS 不適用，保留 task baseline、重新完成受影響審查與整合驗證。

合併前重新核對遠端、findings、保護規則、CI event／attempt／tested SHA／必要 jobs，綁定 reviewed HEAD。合併後查實際 merge SHA、parents、tree、develop HEAD，並核對該 merge SHA 的 develop push CI；不能以 PR CI 代替。保存兩輪 SHA／scope／verdict／證據及交接紀錄；所有門檻通過才宣告完成。

永久維持 actual／expected 獨立、exact identity、quorum、交易、per-PDF fingerprint、runtime integrity 與 fail-closed 契約。不得修改 main、直接 push develop、squash／rebase merge、force push、降低保護規則、tag／release、live DB mutation 或未交付階段。代理不可用、結果缺失、CI 未通過都不是 PASS。只於 Codex 執行期間協調，不新增背景 AI 工作或額外 API 計費設定。

以繁體中文回報實際檔案、SHA、兩輪審查、測試與限制、PR、merge、post-merge CI 及 working tree。

## 日後最短啟動指令

請依 repository 的兩輪獨立審查自動流程，完成：〈任務與驗收條件〉。
