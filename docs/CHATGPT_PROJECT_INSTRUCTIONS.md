# 可貼入 ChatGPT 專案指令

本專案為 discoveryray/zhuyin-proofreader。每次開始相關任務，先讀實際 AGENTS.md 與唯一程序入口 docs/DEVELOPMENT_REVIEW.md，再讀入口所列任務必要契約。AGENTS 是永久安全／授權權威；VALIDATION_POLICY 專責驗證，PR_REVIEW_AUTOMATION 專責現行操作，PR_REVIEW_GATE 專責 schema／有限 adapter，v5.8 完整規範保留產品安全／階段契約與歷史。未指定 ref 時從核對後的 develop 取規範；以即時 Git／PR／CI 與原始證據判定事實，不把 main 舊版、候選文件或受審新規當已啟用政策。

PR #71 未啟用首版已完成驗證整合，本啟用候選尚未生效。依唯一入口的啟用與採納契約逐 task 核對：成功啟用／actual short 之後新建立且明確採用 `development-flow/tiered-review/1` 的合格 `/6` task，才選 1／1／2、有限補審、canonical 原件／摘要及 code／checks／eligibility 分流；其餘、本啟用任務及舊 `/5` 保持原契約。協調者核真實使用者／Git／平台來源及原件可取回性；state、時間／source_ref／hash 或全域常數不認證資格，也不創造授權。本啟用任務仍按 baseline 舊規兩位獨立完整審查，不以新 gate 放行自身。

我明確交付的開發任務，預設授權該 repository、scope 與階段內的修改、代理委派、測試、commit、push、建立／更新 PR，以及通過適用全部門檻後以 Create a merge commit 合併至 develop 並執行 post-merge 驗證。我可收窄、暫停或撤銷；較窄當次指示優先。純討論／唯讀審查不等於開發交付，不重問有效授權，也不自行開始未交付階段。

新任務預設開發模式，只做必要短定向 regression、compile／diff與本機 commit；完整 code review 的 CODE_REVIEWED 是中間狀態，回報「已修改、待正式驗證」。只有我明確說「開始驗證」或已有等效時段授權才固定候選進入正式驗證。完整非視窗 CI、當次原件、保護條件、actual merge 與 push 短驗證仍依入口與驗證政策核對；舊規 task 要兩輪正式 PASS，合格 `/6` 的 reviewer 數及 code／checks 結論依入口選定，不把舊數字反套新流程；不得宣稱未執行的檢查為 PASS。

依入口與 gate 的原版本分類 BLOCKED、保存原件及 append-only 關係；只有 confirmed code finding 的實際修正消耗原 task 三輪 corrective。Non-code 缺口補證據或 STOP，不新增空 commit、不重設 task／baseline／count。G4 延後，不加同 finding 兩輪無進展規則。既有 task 的原件、失敗、STOP／BLOCKED、有限例外與歷史判定不改寫，既有已採納驗證政策的有限遷移依原採納條款；新政策不使舊任務自動完成。

永久維持 actual／expected 獨立、exact identity、quorum、交易／恢復、per-PDF fingerprint、runtime integrity 與 fail-closed。不得修改 main、直接 push develop、squash／rebase merge、force push、降低保護規則、tag／release、live DB mutation 或未交付階段；較窄任務授權也可排除 push／PR／merge。取消真 Tk 自動驗證與歷史 CI 自動閉包的既有政策保持，產品 GUI／Tk 保留。能力不足、原件缺失、CI 未完或失敗都不是 PASS。

以繁體中文如實回報實際檔案、SHA、適用審查、已執行與未執行驗證、限制及 working tree。中斷後從原 task evidence 與 Git 狀態恢復；只在 Codex 執行期間協調，不新增背景 AI、排程或額外 API 計費設定。

## 日後最短啟動指令

請依 repository 的 docs/DEVELOPMENT_REVIEW.md 適用現行流程，完成：〈任務與驗收條件〉。
