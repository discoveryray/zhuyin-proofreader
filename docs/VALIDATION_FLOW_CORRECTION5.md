# validation-flow-reduction 第 5 輪限定接續

本記錄摘要 2026-10-03 使用者對同一 task／PR41 新增的一輪明確授權，不創造新授權。原第 4 輪授權、全部 baseline／history／成功／失敗／review 原文保留；[第 4 輪摘要](VALIDATION_FLOW_CORRECTION4.md) 仍是當時的歷史契約。本次新原文才授權第 5 輪，最終 `5/5`，所有其他 task 一般三輪上限不變。

| 綁定 | 唯一適用值 |
|---|---|
| task / repository / branch / PR | `validation-flow-reduction` / `discoveryray/zhuyin-proofreader` / `chore/validation-flow-reduction` / `41` |
| baseline / PR base | `457707b4c4109c1b10a0da76d8f8a884aca10341` |
| 第 4 輪 to HEAD / 第 5 輪 starting HEAD | `90b408794415509dd919a6d7a91c911f3724fd1f` |
| 第 3 輪 to HEAD / 第 4 輪 from HEAD | `a0e647952ae5d973ea30130264294eee4e6982fa` |
| 追加 / 最終上限 | 一輪；`5/5` |
| 第 5 輪 raw authorization SHA256 | `46b2855d3fe7cd873ce1c4aaf9a93a8afa320b4f67e17aa14405f74d06f296d0` |
| 保留第 4 輪 raw authorization SHA256 | `29b2869d5e6584bab8efbf022f697c8ee7331b7e1241ef0c18df7f4a647ec7fc` |

第 5 輪原文保存在 ignored `tmp/pr-review-automation/validation-flow-reduction/correction5-preflight-records/user-authorization.txt`；第 4 輪原件仍在 `correction4-tcl-wiring/user-authorization.txt`。Gate 只核對 saved bytes/hash、固定 identity 及保留的連續輪次，不認證人類授權、review 或原始證據真實性；協調者仍親讀並確認授權有效。

唯一修正目標是 confirmed F1：early preflight 寫入 `started.json` 卻不是正式 group，真 `history` scanner 因缺 sibling `manifest.json` 回傳 exit2，成功預檢也擋住後續 core。修正 producer 使用 `preflight-start.json`／`preflight-result.json`，schema `zhuyin-early-preflight/1`、kind `early_preflight`；保留開始／結果／命令／source hash／CI identity／環境／raw log／JUnit／child result。現有 CI `**/*.json`、XML／log globs 保存這些檔案。真正 group 的 `started.json`／manifest 完整性與 scanner 保持不變；不忽略未完成 group、不刪歷史、不產生 fake manifest 或 coverage。

驗證先用隔離 tiny fixture 跑真正 producer CLI→history reader→core CLI，核對 exit code、actual testcase marker、raw events／JUnit／manifest。只有 Tk child 可 stub；Git/CI/history-index 都明示 synthetic，runner/history/pytest/CLI 實際執行，不當 hosted／正式功能 coverage。失敗／未完成預檢須在真 subprocess sequencing 阻止 core；真 grouped start 缺 manifest 仍 fail closed。純 preflight 的 aggregate 缺 core/gui 仍 exit2，不把它叫 aggregate 成功。

本輪不改 Tcl/Tk 修正方向、產品、分類器、runtime data／assets、依賴、capture、retry allowlist、runner/history validator 或沿用架構。固定一次新 corrective commit 後原第一位 reviewer 檢查完整累積差異及 F1 resolution；新 HEAD 仍需兩輪完整 scope 正式 PASS。無 confirmed blocker 才沿用前次已授權而未使用的一次 hosted正式 CI：preflight成功→core／GUI各一次→完整 coverage／其他必要門檻，不新增 CI retry／本機全套／full unittest。第 5 輪後再有 confirmed code blocker，或正式 CI 失敗即 STOP，保留原因與 `5/5`，沒有第 6 輪。必要兩 review／CI／保護規則全成立才依原授權 merge commit、實際 merge push 原短驗證；不追認 PR39/40 或旧結果。
