> 2026-10-08 歷史適用範圍：以下原 GUI-once 契約保留供 immutable 歷史查核，
> 不授權任何新的 GUI、preflight、Tk initializer 或 retry 執行。現行
> `validation-flow/no-real-tk/1` 只執行有效非視窗 core；原 manifest、失敗、
> retry_eligible=false、claim／已消耗額度、ZIP 與 skip 語法原樣保留。
>
> 同第五輪限定採納（原問答 SHA256
> `5519099dd0d20aefe752bf7fec2cfa85998e0c70850617a8b7949a5a79219f10`）
> 允許 `_pr43_setup_source` 唯讀固定 d59358cd 原 git blob，完整 source SHA256
> `1373c00997ca98db7562a39b1b0370b818bc9302c1e2c3a9ef7bc915ade416da`，
> 再以 AST 核對下列原 initializer pin；絕不 import／execute 舊 Tk。
> 缺 git object、錯 full-source／AST hash 均拒絕，沒有 current-source fallback。
> 一般 `read_verified_manifest`、`retry_eligible` 及原 claim／ZIP 約束不變。
> 八項歷史安全測試使用明示 LEGACY_SCHEMA 合成歷史資料，不能作正式 GUI PASS；
> current GUI 入口即使套用不開窗 stub 也拒絕。兩輪 review／CI 與原四輪紀錄保留。

# PR43 限定一次 GUI 補驗契約

此附約只適用 `discoveryray/zhuyin-proofreader` PR43、task
`actual-gpt-bbox-export`、baseline/base
`8eab9a1de34b59115658a2c7d3565adb347e59ab`，從已核准的
`d59358cd45cc65a73b1d4dcb0aa228ffe7233435` 新增一個實作 commit。
使用者於 2026-10-06 回覆「核准」，原 authorization SHA256
`39da5b779290cdb2b95eff70c8859122366dd7c5ca44d0fa3cdd4d2aae8bb50b`、
proposal SHA256 `9b0aba33f2cf34b5ec29c290f3388501899e9e577e5a123b55c9bf53212c364a`
及回覆 bytes 均須攜帶、實讀並核對。這是已採納的有限契約實作，bbox
2/3、CFF 2/3 保留；不是把環境失敗改判為 code finding。

唯一來源是 run `37453889278` attempt1、artifact `11412967278`，原 ZIP
SHA256 `200ff76738c7cb565cfad1cc470be97fa0bc3a2197908a63391c1335f0d82235`，
GUI execution `08e193d65afa44a6b9498f631561521b`。原完整 49 個檔案、11 個
setup skip、exit0/failed、`retry_eligible=false` 原樣保留。原核心、較早 f4
核心失敗與所有跨 run/attempt 歷史保留；原核心不能冒充新候選覆蓋。

`restore-history → run core → run gui → aggregate → verify-coverage` 入口不變，
workflow 不變。PR body 額外需要且只接受一個 `pr43-gui-once` marker；它攜帶
固定有限 identity/digests、authorization/proposal/human reply 原 bytes 的 base64
及完成實作後凍結的 feature SHA。它不創造新授權。Root 可在 local commit
完成後使用以下命令產生旁證，將輸出原樣附於既有 PR body；保留原 body/history。

```powershell
python scripts/validation_runner.py pr43-gui-block --head <完整新featureSHA> --authorization <authorization.json> --proposal <approved-proposal.json> --human-reply <human-reply.txt> --output <新的ignored輸出檔.txt>
```

新 feature 的直接 parent 必須是上述 d593；實際 PR integration candidate
的 ordered parents 必須是 baseline/new feature。未知授權、repo/PR/task/base/head、
缺 marker、錯 digest 或缺原 bytes 均 STOP，不能回退到新 tree 的首次 GUI。
一般其他 task 的 legacy 行為保持原契約。

單一 `validate_pr43_gui_once` 供啟動及彙整共用。它實際下載並核對原 ZIP，
逐 member 比對原解壓檔，沿用 immutable manifest reader；原
`retry_eligible()`/`read_verified_manifest()` 不改。原全部不成功事件須且只能
是 11 筆 `ManualActualGuiVisibleLayoutTests.setUpClass` setup skip，JUnit、
events、raw 全部一致。固定 source SHA 綁定實際 `tk.Tk()` 初始化來源；
已知 outer init.tcl discovery wrapper 與 inner init.tcl resource read 格式都
必須存在。一般 skip、泛稱找不到 Tcl、未知 wrapper、非初始化、assertion、
混合失敗或不完整其他 GUI 執行不適用。這不宣稱系統 Tcl 根因已修復。

新候選重新跑一次完整 core；GUI 的實際 Python/dependencies/capture/環境必須
與原環境完全一致，GUI identities 逐項相同。新核心回歸可增加 core 清冊，
所以跨候選不要求整份 inventory SHA 相等；新 core/GUI 仍必須彼此相等。

GUI collection/preflight 前，exclusive/fsync 保存 `pr43-gui-once/claim.json`。
declaration 與 claim 的相對 path/SHA 綁入既有 `history-context.json` artifact。
原 ZIP 以有限子目錄中的 `pr-artifact.zip` 保存，符合既有 workflow upload
白名單；JSON、events、JUnit、raw 及旁證一併還原後仍重算完整鏈。
即使 collection/preflight 失敗、timeout 或中止，已宣告一次也消耗唯一補驗
額度。缺 claim、缺 completed manifest、損壞 ref 或跨 head claim 一律 STOP，
不得刪除或重設。原 GUI 加唯一補驗合计最多兩次，跨候選/目錄/run 不重設。
新 execution 保持真實 candidate/tree/retry_key，`retry_of` 指向原 execution；
不重寫原 false。還原的重複相同檔案可沿既有 immutable history 去重，ref
只可在固定 `pr43-gui-once` namespace 依相同 digest 解析。

`aggregate` 重新消費同一旁證與 claim，只接受新候選完整 core/GUI 成功、
全部必要 GUI 實際通過且無 skip；原 manifest/coverage schema 不增加欄位。
`verify_coverage → aggregate` 以及 post-merge 既有 delegation 使用同一驗證，
不另造成功判斷。任何本次正式失敗即 STOP，沒有第三次 GUI 或額外 core rerun。

開發 regression 使用真實小型 pytest child/CLI、history/aggregate/coverage
串接；外部 GitHub/CI/environment/preflight 與固定 original artifact pins 使用
獨立 bounded fixture，沒有 Tk 視窗，不是正式 Python3.13.0/GUI 覆蓋。
本機实际 Python3.13.5 必須另行披露。兩位新 HEAD 的完整獨立審查、正式一次
新 core/GUI、required CI/protections 與 actual merge 短驗證仍依
[VALIDATION_POLICY](VALIDATION_POLICY.md) 及原 task/gate 契約；沒有 merge PASS
或放寬 bbox/source/actual/expected/Global 安全邊界。
