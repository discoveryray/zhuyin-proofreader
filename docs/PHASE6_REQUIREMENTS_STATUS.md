# Phase 6 需求對照

本表依 [現行完整規範的 Phase 6](V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md)
與 A～D 各任務範圍核對程式。A～C 的固定程式基準為
`52c08a0c2ff193bde475fd3a2ec75c42b6a57b0d`，已分別由
[PR #21](https://github.com/discoveryray/zhuyin-proofreader/pull/21)、
[PR #22](https://github.com/discoveryray/zhuyin-proofreader/pull/22)、
[PR #23](https://github.com/discoveryray/zhuyin-proofreader/pull/23) 整合。
D 的實作由本任務加入；本文件不是獨立審查結論。各次 reviewed HEAD、測試、
Windows GUI、兩輪報告、PR 與實際 merge push CI 以對應 PR evidence 區為準，
不得將舊 SHA 的成功直接當成目前版本的成功，也不據此宣告 v5.8 release。

| 現行 Phase 6 需求 | A～D 提供的功能 | 程式與回歸依據、限制 |
| --- | --- | --- |
| Global state；candidate / ready / verified / quarantine | 6A「全域字形庫」查閱儲存狀態、有效重用狀態及原因，區分 absent、合法空庫與損毀／不相容／忙碌。 | `global_library_inspector.py`、`GlobalExactGlyphRepository.load_inspection_snapshot()`；`tests/test_global_library_inspector_v580.py`。唯讀失敗清除結果，absent 不初始化；只有通過既有安全 gate 的 Global truth 才可重用。 |
| Evidence / source 與歷史 approval | 6A 顯示 direct evidence、來源識別、legacy candidate、provenance、approval／revocation；6B 顯示固定的核准證據清單。 | `record_sections()`、`global_library_approval.py::preview_sections()`；6A inspector 與 `tests/test_global_library_approval_v580.py`。SHA／ID／revision 在詳細資訊，缺少原頁／字形預覽時如實說明。 |
| Promotion approval | 6B 在全域字形庫單一項目中「檢視並核准」，綁定固定 identity、reading、revision、完整 direct roster／digest／policy，明確確認後呼叫既有核准 service。 | `load_approval_preview()`、`approve_global_exact_glyph()`、`lookup_approval_outcome()`；approval 回歸含 stale、late conflict、UNKNOWN 固定請求查證。GUI 不另算 quorum、不製造 fresh actual、不批次核准。 |
| Conflict | 6A 區分 durable quarantine 與 legacy-v0 read-side suppression；6B 區分成功提交衝突交易與核准／拒絕／未知。6D 另列尚未匯入的來源計畫衝突。 | Global inspector／approval、`legacy_migration_inspection.py`；相關 GUI tests 與 `tests/test_global_legacy_migration_review_v580.py`。三者不得互相冒充；read-only suppression 不宣稱已撤銷 approval。 |
| Migration diagnostics | 6D「舊資料匯入預檢」：明確選一個 project 與可選 PDF roots，按「開始預檢」後呈現通過／整批 BLOCKED、候選、映射不足、來源衝突、NON_GLOBAL_ELIGIBLE、來源檔／保留讀音／exact identity、可驗證的 reconfirmation targets。 | `legacy_migration_inspector.py`、`legacy_migration_inspection.py`；只呼叫 `migrate_project(..., apply=False)`，從同一 strict plan／Global snapshot 取得資料。`tests/test_legacy_migration_inspector_v580.py` 與既有 Phase 5 B1／B2／B3／完整 bbox tests。通過不等於 APPLY、核准、重用或未來交易必成。 |
| Delivery / recovery status | 6C「交付與恢復狀態」顯示所選專案的 journal／outbox、PREPARED／COMMITTED、PENDING、Global receipt、待本機 ack、既有 recovery plan。 | `delivery_recovery_service.py`、`delivery_recovery_inspector.py`、`tests/test_delivery_recovery_inspector_v580.py`。分開本機紀錄與目前 Global 查證；沒有 journal 不能推定歷史完成，COMMITTED 不等於 refresh 完成；不新增恢復 executor。 |
| Metrics | 6A 的 stored direct／independent／legacy 數；6C 的本機 outbox 標記數；6D 的 import 項目、其中不同 glyph、import × occurrence targets、不同位置與 noneligible 診斷數。 | 各 inspector summary 及 GUI tests。計數單位明示；來源數不是五軸 quorum，legacy 貢獻永遠 0；失敗不產生虛假零筆成功。Global 既有診斷不混入本次來源統計。這些是 snapshot 筆數，並非下列完整 aggregate metrics。 |
| Operator-facing diagnostics | A～D 使用繁體中文說明狀態、限制與下一步，技術原始欄位留詳細資訊；可篩選查阅與明確重新讀取／預檢。 | D 只在明確開始後讀來源，背景工作不操作 Tk；同時至多一個預檢，來源切換立即清除、晚到結果丟棄。成功後每秒以同一 worker 重查已驗證的 exact source bytes／optional absence；偵測變動即清除，需手動重新開始，不自動重跑 planner。 |

6D 只使用原有來源解析：指定 project 與明確 PDF roots 的 exact filename，
以及 manifest 原本保存的 PDF 路徑；不遞迴、不掃其他專案。報告中的來源路徑及
`source_input_hashes` 是稽核與畫面失效用途，不進入 import identity 或 runtime
fingerprint。原有 strict validation、actual-only proof、完整 PDF bbox、source
前後一致性、quorum、transaction、schema、epochs 與 runtime assets 保持原契約。
查閱 Global 仍可能產生 SQLite 短暫 SHM／空 WAL；驗證要求保留主 DB、非空 WAL
與所有邏輯資料，不能將暫存鎖定檔誤稱為 migration 寫入。

## 尚未提供的完整統計與範圍

[早期 architecture audit §19](../GLOBAL_EXACT_GLYPH_LIBRARY_AUDIT_v5.8.md)
列過下列 aggregate metrics。現行 A～D 的程式與證據尚不能證明它們完整實現；
本輪沒有將其全部採納為驗收或新增實作授權。具體缺口如下：

| 歷史設計指標 | 目前仍缺的資料／計算／呈現 |
| --- | --- |
| Exact global reuse hit rate | 完整 exact-identity occurrence 分母與實際 Global reuse 分子。現有按 identity 聚合的 decoder audit 不等於此比率。 |
| Global rescue rate | 原本 unresolved 且僅由 Global 解決的因果歸因與彙總比率。 |
| Manual actual review reduction | 可比較的 Global 啟用前 baseline，以及 actual-only 人工工作量減少量。 |
| Cross-project exact SHA match rate | 所選專案完整 identity 分母與 candidate／trusted／conflict 的匹配統計。 |
| Promotion funnel | direct → 合格 independent quorum → approval → trusted 的完整彙總與停留原因分布。 |
| Same-source correlation rejection rate | candidate pair 總數與 project／PDF／font 等 correlation 拒絕數，不能用欄位 distinct count 代替。 |
| Time to independent quorum / promotion | first direct → 合格 quorum／approval 的事件時間與 duration 彙總。 |
| Global conflict rate | 進入 durable quarantine 的 identity 分子與具 direct evidence identity 分母；不能混入 read-side suppression。 |
| False promotion rate | 曾 promoted identity 因 contrary direct evidence 被 quarantine 的安全連結與比率。 |
| Cache invalidation precision | 每次 Global 有效變動的因果 identity、invalidated PDF roster 與完整 cache 分母。 |
| Unrelated-cache stability | 實際變動前後無依賴 PDF roster／fingerprint 的彙總觀測。現有 regression 不是操作期統計。 |
| Store reliability | 期間錯誤頻率、retry latency 與 pending outbox age；單次 typed error 不等於可靠性比率。 |
| Migration conversion | legacy candidate → fresh direct → promotion 的可驗證轉換連結與分子／分母。待確認 target 本身不是 fresh evidence。 |

本輪明確不提供 APPLY、批次 migration、adjudication、自動解除 quarantine、
recovery／delivery／ack／refresh 操作、新 staging 或自動預填核准，也不編造 PDF
位置或字形預覽。這些限制與尚缺 aggregate metrics 分開記錄；不得藉需求盤點
自動開始下一任務或擴大 Phase 6D 範圍。
