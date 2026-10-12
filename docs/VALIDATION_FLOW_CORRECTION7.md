# validation-flow-reduction 第 7 輪限定接續

2026-10-03 使用者僅對同一 task / PR41 新增一次限定修正，最終7/7，保留 baseline、前六輪與全部成功／失敗／診斷／review原文，不重設計數。其他任務仍一般三輪；本摘要不創造授權。

固定 task `validation-flow-reduction`，repository `discoveryray/zhuyin-proofreader`，branch `chore/validation-flow-reduction`，PR41，baseline/base `457707b4c4109c1b10a0da76d8f8a884aca10341`，starting HEAD `adcee81cf844fcee51c16b94099fe8916458c4dc`。新原文在 ignored `tmp/pr-review-automation/validation-flow-reduction/correction7-main-push-routing/human-authorization.txt`，SHA256 `5f581ffa18d57db0114f23ed26f30fd7d09ba1d8b0cf5a97499b1392d341655a`。原第六／五／四授權原件及 hashes `01df9c9193d00f675add80287feadc9f657b27b6c81fc9114d59448d3c9d9d4d`、`46b2855d3fe7cd873ce1c4aaf9a93a8afa320b4f67e17aa14405f74d06f296d0`、`29b2869d5e6584bab8efbf022f697c8ee7331b7e1241ef0c18df7f4a647ec7fc` 保留、實讀核對。

唯一目標是修正 main push 被排入 develop-only short，及 summary 將所有 push 當 develop merge。一般PR保持grouped，一般develop push保持嚴格actual-merge short；main push以原required Python job執行baseline WindowsPython3.13.0完整pytest、GUI execution、runtime、compile、push diff、clean，不加full unittest。main-only原樣重用已受審same-installation configure接線；不改Python/Tcl/helper/依賴。summary always執行，按真event/ref/原legacy條件核對必要job與main/legacy真step outcomes，missing/skip/failure/cancel不得通過。PR29/simplify精確identity、原雙版本/雙入口/capture條件保持；workflow_dispatch仍不支持，不建立main證據沿用或通用路由。

先以隔離事件資料解析實際workflow條件、matrix、選取真run/uses，再真正執行抽取的PowerShell summary；fixture證據不是main push／hosted full GUI執行。有限定向短測、compile/diff與必要新來源collection；不本機GUI/fullpytest/fullunittest、不操作main/develop、不增加自動retry。固定新候選後原first reviewer審完整baseline→HEAD累積差異，無confirmed blocker才由協調者更新PR與執行本次另授權一次正式CI。adcee成功只屬歷史，不冒充新候選。新HEAD仍需兩輪完整正式PASS、必要CI、未解findings及保護規則核對才merge commit和actual merge短驗證。7輪後再須改受審source或CI失敗即STOP，不自行第8輪／rerun。
