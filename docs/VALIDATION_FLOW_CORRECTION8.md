# validation-flow-reduction 第 8 輪限定接續

2026-10-03 人類明確授權同一task / PR41 一次限定修正，最終8/8，保留原baseline、前七輪、全部失敗／成功／reviewer原文，不重設計數。本摘要不創造授權；通用三輪及各歷史adapter上限保持。

固定 task `validation-flow-reduction`、repository `discoveryray/zhuyin-proofreader`、branch `chore/validation-flow-reduction`、PR41、baseline/base `457707b4c4109c1b10a0da76d8f8a884aca10341`、starting HEAD `d974ec5c420feabaa64b449bd25d78aed10c02f0`。新原文在 ignored `tmp/pr-review-automation/validation-flow-reduction/correction8-unittest-step-order/user-authorization.txt`，SHA256 `c156319030dc54f549e6479b369ef718fecdd494aee1817fee515e683032e05a`；第7／6／5／4原始授權hash及bytes均保留並實讀核對，原各summary仍是當時歷史契約。

唯一workflow修正將 Run full unittest suite 的 `id: unittest` 移到 `if` 之後；所有欄位值、if条件、summary引用与路由語義原樣。既有 tests/test_pr29_review_gate.py 原第406行regex即可讀取真正if，再執行原PR29正向及錯repo／branch／SHA／push／dispatch、capture／matrix反例。不能修改任何原斷言或parser、不新增排序鏡像测试、不刪skip／吞TypeError或放寬歷史例外。若最小排序不足以解除原失敗，STOP具體回報，不擴方法。

先原失敗FQN、所屬PR29file，再本輪workflow路由/真PowerShell summary與直接既有workflow readers（required step names、tiny early-preflight CLI複製workflow/artifactglobs）。只有必要授权gate正負向回歸、精確source登記與本限定規範追加；不產品／GUI清理／分類器／依賴／capture／環境／資料修改，不全專案盤點或本機full／GUI／fullunittest。必要collection-only一次原固定timeout、不retry；原正式failure保留，不冒充新候選成功。

固定新corrective HEAD後原first reviewer審完整baseline→HEAD累積及解除證據。無confirmed blocker才更新原PR，新候選本次一次正式CI，不自动retry或舊HEAD替代。新HEAD仍須兩輪完整正式PASS、必要CI、未解findings与保護規則成立才merge commit及actualmerge短驗證。第8輪後仍需改受審source，或正式CI失敗即8/8 STOP，無第9輪；本記錄不清除原reviewer code BLOCKED，也不授權其同HEADsupersession。
