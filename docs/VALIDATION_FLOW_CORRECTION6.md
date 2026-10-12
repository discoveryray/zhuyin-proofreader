# validation-flow-reduction 第 6 輪限定接續

2026-10-03 使用者明確授權同一 task / PR41 的一次限定修正，最終 6/6。原 baseline、前五輪、所有診斷與失敗、review 原文保持不變；第四、第五輪摘要仍是各自當時契約。本摘要不創造授權，其他 task 一般三輪上限不變。

固定 task `validation-flow-reduction`、repository `discoveryray/zhuyin-proofreader`、branch `chore/validation-flow-reduction`、PR41、baseline/base `457707b4c4109c1b10a0da76d8f8a884aca10341`；起始 HEAD `811ff56f256370eb19cb5de0d76ce6a8c12d61be`。第六輪原文保存在 ignored `tmp/pr-review-automation/validation-flow-reduction/correction6-owner-thread-cleanup/human-authorization.txt`，SHA256 `01df9c9193d00f675add80287feadc9f657b27b6c81fc9114d59448d3c9d9d4d`。保留第五輪 authorization `46b2855d3fe7cd873ce1c4aaf9a93a8afa320b4f67e17aa14405f74d06f296d0` 及第四輪 `29b2869d5e6584bab8efbf022f697c8ee7331b7e1241ef0c18df7f4a647ec7fc` 原始 bytes/hash。

唯一修正是 ExpectedResolutionGuiTests 的視窗生命週期，以及直接必要的支援、回歸、精確分組來源登記及 task-bound gate 例外。正常／例外退出均釋放 body locals，等待本案已啟動的 save worker，銷毀視窗及 pending polls，再於 owner thread 釋放 app/widget cycles，最後退役 root。weakref 及實際執行緒證據須證明物件已退役；只呼叫 destroy 或 gc.collect 不足。保留原保存、重開、拒絕寫入與資料一致性斷言；不改產品、其他 GUI 類別、分類器、capture、環境、依賴或正式資料。

先完成物件退役／中途例外與原三案同程序原順序短回歸、精確新來源 collection。固定新 corrective HEAD 後原第一位 reviewer 審完整 baseline→HEAD 累積差異；無 confirmed code blocker 才更新原 PR41，執行本次另外授權的一次正式 CI：hosted preflight → core 全組 →必要 GUI 全組，完整 coverage/runtime/compile/diff。舊 core 成功僅是歷史證據，不冒充新版本或新增沿用框架。CI 失敗即停止、不重試；第六輪後若仍須改受審來源，6/6 STOP，不進第七輪。兩輪正式 PASS、必要 CI 及保護规则全部成立才依原授權 merge commit，實際 merge SHA 短驗證；不追加本機／合併後全套。
