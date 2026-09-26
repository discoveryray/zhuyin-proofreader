# PR29 本次重建的可攜來源包

[portable-reconstruction.zip](portable-reconstruction.zip) SHA256：`5ac2a5f9657a1965aa890c1fe8d021ad00bea9339847c0b58bee8c9195134db4`。

此為2026-09-25本次建立的接續紀錄，保留已知四輪與缺失狀態，不是重新找到的歷史原 task/gate state。固定候選之前的第五輪狀態是 in-progress，不能冒稱第五輪完成或新版本已驗證。

ZIP 解壓根目錄包含 reconstructed-history.json/md、known-findings.json/md、source-index.json、reconstruction-manifest.json、72項已取得sources與原始授權來源。文件內 sources/... 連結以解壓根目錄為準，不是在這個 GitHub 外層目錄。另存 [manifest](reconstruction-manifest.json) 供下載前核對；它描述包內檔案。

既有來源摘要／hash不替代缺失原件；未知保持未知。原完整報告／CI原logs／本次採納來源可回查。N的實測、完整獨立review、完成第五輪snapshot另append於PR evidence，不覆寫本包，不為保存PASS再更動受審N。

## 第五輪效能原始證據與重播包

[benchmark-replay-v5.zip](benchmark-replay-v5.zip) 保留 final-v5 的 75 份 fixture seeds、原始紀錄、runner、來源綁定及獨立核對資料；3,328,767 bytes，SHA256：`3a777d210b135ddcd08679643b22307c4867341005e45453c067213390ae81ed`。[逐檔清冊與 checksum 原件](https://github.com/discoveryray/zhuyin-proofreader/blob/cf444f4c892a660d36c57b34cbb08bf5a270bd66/docs/evidence/pr29_round5/benchmark-replay-v5.sha256.json)（[raw bytes](https://raw.githubusercontent.com/discoveryray/zhuyin-proofreader/cf444f4c892a660d36c57b34cbb08bf5a270bd66/docs/evidence/pr29_round5/benchmark-replay-v5.sha256.json)，SHA256：`7b667c7c3778ca29d938514961297984cb753057cab9ff0a37df5fb816ee818f`）保留於固定祖先提交 `cf444f4c892a660d36c57b34cbb08bf5a270bd66`，與 ZIP 均由本次 round5/benchmark 原封複製。新 HEAD 不另存此 sidecar，避免為證據增加 runtime CSV 專用的 `-text` 屬性；原件與原提交歷史仍可跨電腦取回。來源是同環境、相同 fixture bytes 的 D／整合版本配對量測，不是歷史量測，也不包含正式資料庫或真教材。完整 final-v5 及先前 v4 樣本另保留於 [benchmark result](../review_confirm_round5_benchmark.json)，方法與限制見 [效能報告](../../REVIEW_CONFIRM_PERFORMANCE.md)。

ZIP 內 README.txt 是完整重播說明。原 fixture 的 PDF／XLSX 絕對路徑與 integrity seals 已嵌入 bytes；原路徑為 `C:/Work/zhuyin-proofreader-phase4/tmp/pr-review-automation/review-confirm-responsive/round5/benchmark/paired-final-v4/fixture-g0` 及 `fixture-g8`。若要精確重播，只能在這些位置新建專用空目錄、核對清冊後還原；不得覆寫既有不明檔案。跨電腦解壓閱讀與核對不需要執行量測；改路徑時必須重新建立兩側共用的新 fixture 並標示新資料，不能冒稱原 fixture bytes。保留 recorded Windows／Python／Tcl 環境與 isolation，輸出寫新目錄，不覆寫舊證據。

量測當時程式尚未 commit；包中 raw source hash 不冒充候選 Git SHA 或 Git blob hash。協調者在候選提交後另存 raw bytes 與實際 Git blob 的對應。final-v5 才對應最後的原生 Tcl 視窗關閉清理修正；v4 保留為修正前來源。重播包不構成真人教材驗收，也不取代兩輪獨立審查。
