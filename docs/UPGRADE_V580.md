# v5.8.0 升級與回復

v5.8.0 是現有功能穩定版的發布候選，尚待正式驗證與發布。本文件提供有限升級步驟，不授權操作正式教材或 Global，也不保證任意歷史資料相容。沒有版本號、來源 commit／tree、逐檔清單及包 checksum 的資料夾不能當作正式發布包。

## 1. 環境與候選包

支援 Windows、Python 3.13.0 與既有 `.bat` 入口，不提供 EXE。確認 `py -3` 選到 3.13.0；未安裝 Windows Python launcher 時確認 `python` 是 3.13.0。兩個入口均先切換到程式所在目錄，使用 `py -3`，無 `py` 時才使用 `python`；不要把另一個虛擬環境的測試成功當成日常入口使用的環境。

解壓到新的獨立程式目錄，保留原程式目錄。`安裝依賴.bat` 使用 `requirements-release-lock.txt`，固定 runtime 直接／間接依賴及 pip；執行會安裝到入口選到的 Python 環境，請先確認環境。`啟動_單機注音校對.bat` 是正式啟動入口。不需要 pytest 等測試工具，也不使用「修正版」入口作正式安裝程序。

候選 ZIP 由 `scripts/build_release_package.py` 從指定乾淨 Git commit 取得 `release/package_allowlist.json` 逐檔列明的內容；不直接壓縮本機工作目錄。包內 manifest 記錄來源 commit／tree、各檔 SHA256，狀態為 `CANDIDATE_NOT_RELEASED`；manifest 不包含自己的 hash，ZIP checksum 在 ZIP 外。使用前核對外部 checksum，再核對解壓檔案清單，不能僅依檔名判斷版本。

## 2. 先建立一致備份

關閉校對程式、相關子程序及正在開啟工作簿的 Excel；確認沒有其他程序或帳號寫入同一 project／Global。將下列資料按同一備份批次完整複製到另一個目錄，原件保留。記錄原程式版本、來源 commit（若已知）、原 project／Global 路徑與備份時間。備份未完成前不要續作、匯入或核准。

| 資料 | 必須保留的範圍 |
| --- | --- |
| 教材 | 原 PDF 完整 bytes 與原有檔名，不重新輸出／轉存成另一份 PDF。 |
| 完整校對專案 | 整個 project，含 `校對工作階段.json`、manifest／ledger、人工／GPT 判定事件與 DB、`_專案證據`、manual actual staging、receipt、journal／outbox、pending／refresh 狀態、`01_實際注音`、`02_候選報告`、判定匯出檔及報告。不要只備份最終 Excel。專案 DB 有 WAL 時保留一致資料組。 |
| 程式旁使用者資料 | 依下表保留舊程式旁可變的 seed／learning／override／conflict／provenance 檔案；還有使用者實際建立的資料。不能只依檔名是否含 user 判斷。 |
| Global | 原 Global root 的整個資料組，含主 DB、存在的 WAL（尤其非空 WAL）、其他伴隨檔案與 receipt／稽核資料；關閉寫入者後一起備份，不刪 WAL、不只複製主 DB。 |

Global 預設 root 是 `%LOCALAPPDATA%\DiscoveryRay\ZhuyinProofreader\GlobalExactGlyphLibrary\`。若原操作使用明確自訂 root，備份及續作都必須使用同一個已核對 root；不得只備份預設位置而遺漏自訂庫。不知道實際 root 或無法停止所有寫入者時，先處理該缺口，不能宣稱一致備份完成。

## 3. 區分內建 seed 與本機後續判定

發布 allowlist 中的 seed 來自固定 baseline bytes，並非這台電腦目前的資料。下列角色不能因名字含 user 就刪除，也不能把舊程式旁整份資料夾直接蓋到新程式。

| 程式根目錄檔案 | 隨包內容與升級處理 |
| --- | --- |
| `校對判定記憶.json` | 既有內建 exact-context 判定 seed。舊程式可能已有後續人工記憶；備份原檔，不能用新包 seed 覆寫該原件。 |
| `可重用expected規則.json` | 既有已批准 expected seed。保留使用者另建規則及其證據；不能因升版重建成空檔。 |
| `manual_actual_occurrence_overrides.csv` | 既有有限定位的內建覆核 seed；不當成全域 truth。舊程式旁的後續 occurrence override 與專案 `_專案證據/actual` 分別保留。 |
| `user_verified_glyf_fingerprints.csv`、`user_verified_cff_glyph_fingerprints.csv` | 隨包是既有 header-only seed；舊程式的有內容檔可能是使用者已驗證資料，須連同來源及衝突／provenance 一起保留。 |
| `glyph_truth_conflicts.csv`、`glyph_truth_provenance.csv` | 隨包是既有 header-only seed；舊程式中的 conflict／稽核紀錄不得丟棄，不能單獨帶 verified truth 卻漏掉這兩檔。 |

先在備份的工作副本中，以新程式選取原 project 的副本續作。若原程式旁上述檔案有後續內容，將備份的一致可變資料組完整複製到新的試用程式目錄對應位置，保留新包原始 seed 副本及來源紀錄；不要手工合併、改 SHA、提高 verification level 或刪除 conflict。其他 manifest-protected runtime 資產維持新包原 bytes。此步不表示任意 legacy 資料會獲得相容性豁免；若既有驗證拒絕，保留原件與診斷再釐清，不用目前內容重算 manifest truth。

## 4. 續作與搬移

選原有 project 後按「繼續校對」，沿用原 session 與人工判定，不按「開始新校對」來接續。既有相容性、seal、actual／expected、衝突及 regression 檢查仍有效；版本同步不變更 schema、semantics epoch 或 identity。尚未確認的教材待辦照常保留，不能因軟體版本更新而視為完成。

整個 project root 可搬移／改名；`01_實際注音`、`02_候選報告` 與其內 workbook 檔名須保留，內容須符合 sealed SHA。明確提供可驗證的原 PDF；檔案缺失、不同 SHA、定位不唯一或不支援的連結不能以猜測補過。不得回寫舊位置、從其他 project 借 workbook 或修改 seal。

actual 暫存待套用不是完成；已提交而更新未完成者只使用既有「繼續 actual 更新（已提交）」路徑。遇到 expected 匯入衝突，保留來源及原目標證據，在本位置獨立裁決，不改 actual 配合 expected。

## 5. 回復

關閉新程式及所有寫入者，另存本次試用產生的完整資料及診斷；不要刪除失敗紀錄。把升級前同一批次的原程式、教材、完整 project、程式旁可變資料及 Global 配套備份恢復到另一個明確工作位置，再用原程式續作。已知自訂 Global root 仍需核對。

不把新版已寫入的 project／Global 直接交給舊程式，亦不將舊 DB 與新版 WAL／receipt 混在一起。即使本版未升 schema，仍不能據此保證所有新版寫入的資料可直接回退；回復依一致配套備份。

發布人工操作使用 [六項驗收表](RELEASE_ACCEPTANCE_V580.md) 的副本及隔離 Global。完成軟體發布不要求完成整本教材；本文件與候選包也不是正式發布證據。
