# v5.8.0 六項人工操作驗收

本表是已採納的有限驗收清單，不是 PASS 紀錄。本輪發布準備只提供操作表，未執行人工 GUI、正式 CI、main merge、tag 或 Release。固定候選後由人工在 Windows Python 3.13.0 操作一次；不要求重跑整份教材、清空待辦或建立自動 Tk 視窗。資料不足時記錄 NOT_EXECUTED／原因，不填 PASS。

## 操作前

依 [升級與回復](UPGRADE_V580.md) 關閉寫入者並建立完整一致備份。解壓已核對 checksum 的候選包到獨立目錄；使用少量教材與完整 project 的工作副本，原件保留。匯入、保存、actual 套用、Global 核准等寫入全部限副本。

GUI 的預設 Global 依啟動程序的 `LOCALAPPDATA` 定位。用新的 PowerShell 視窗，只在該程序設定隔離路徑，再啟動候選包；不要更改系統或使用者永久環境變數。將原 Global 的一致副本放在下列隔離目錄的 `DiscoveryRay\ZhuyinProofreader\GlobalExactGlyphLibrary` 子目錄，不使用正式 root，也不把 Global 放在來源 project 裡。

```powershell
# 路徑為範例，先建立並核對自己的副本；本段不複製或修改正式資料。
$env:LOCALAPPDATA = 'C:\ReleaseAcceptance\isolated-localappdata'
& 'C:\ReleaseAcceptance\program-v5.8.0\啟動_單機注音校對.bat'
```

關閉該 PowerShell 視窗即可結束其環境設定；下一次操作仍須核對 Global root。若使用明確 root 的既有操作入口，必須指定同一隔離 Global 副本。核對開始時畫面／診斷使用的 project 及 Global 位置，未證明隔離前不做核准或任何其他寫入。

| 執行識別 | 人工填寫 |
| --- | --- |
| 日期、操作人 | |
| 顯示版本 | |
| 候選 source commit／tree | |
| ZIP SHA256／manifest 位置 | |
| Windows／Python 版本、實際入口 | |
| project／PDF 工作副本、備份位置 | |
| 隔離 Global root、備份位置 | |

## 固定操作表

每項保存短操作紀錄、必要畫面與錯誤原文；結果空欄由實際操作者填寫。拒絕情境使用第二份可犧牲副本，不修改 seal、原件或正式資料。

| 項 | 有限操作 | 接受條件 | 結果／原件位置 |
| --- | --- | --- | --- |
| 1. 正式入口與續作 | 從其他工作目錄啟動正式 `.bat`，選既有 project 工作副本「繼續校對」，關閉再開。 | 顯示 5.8.0；使用候選程式及 3.13.0，原 session、人工判定與既有待辦保留。不能以重新開始 session 代替。 | |
| 2. expected 保存／撤銷／重存 | 對一筆按獨立語境與證據保存 expected，撤銷後回原位置，再保存；重開查已確認項目。用拒絕寫入的另一副本作一次有限保存失敗情境，還原權限後停止該情境。 | 撤銷目標正確，成功後正常接續且重開保留；失敗留原筆，不跳筆、不填假依據。actual 證據不被改寫。 | |
| 3. actual 暫存／套用 | 在少量需要覆核的位置保存 actual 暫存，關閉重開，核對待套用與已保存 expected，再按套用。 | 有效暫存恢復、勾選位置正確、待套用不冒稱完成；套用及重新比較結果有紀錄；獨立 expected 保留。若已提交而後續更新失敗，記錄真實狀態，不偽造恢復成功。 | |
| 4. 真 producer 匯出／匯入／衝突 | 使用產品正式輸出功能產生小型判定檔，依既有格式填寫、匯入工作副本；用合法來源與本地不同 expected 的一筆，核對衝突提示、獨立本地裁決，再重匯同一來源。 | 實際 producer 格式能匯入；兩份證據保留，原目標未被默默覆寫；本地裁決及重匯不復活已否決來源。writer shim、手造成功檔或只驗 metadata 不能代替真匯出。 | |
| 5. 搬移／拒絕／報告 | 複製完整 project 到新 root，保留內部角色目錄與檔名，以相同原 PDF 續作，開啟最終報告；另一副本製造一個缺檔或 SHA 不符情境並再續作。 | 正常搬移可續作，報告可開啟；不回寫舊位置。異常副本明確拒絕並保留原因，不以更改 seal 或更新 asset SHA 補過。 | |
| 6. 四項 Global／狀態功能 | 在隔離 Global 副本查閱一項 identity；對已符合既有條件的一項預覽並明確核准；查一個副本 project 的交付／恢復狀態；對一個明確來源執行 migration 預檢。 | 查閱區分 absent／空庫／錯誤及有效重用狀態；核准綁定固定證據；journal／receipt 與 refresh 狀態不混稱完成；migration 預檢不寫來源、不冒稱 APPLY／核准。缺合格核准 fixture 時該部分記 NOT_EXECUTED，列發布缺口，不製造 quorum。 | |

任一接受條件失敗或必要素材不足，保留原件，列發布 blocker；不因這張表重開 PR #49 或重設其 functional 10／launcher 2／aggregate 12。只處理本發布任務範圍，超範圍問題另列。

## 歷史證據的限定沿用

| 證據 | 版本／SHA 與適用範圍 | 不能據此宣稱 |
| --- | --- | --- |
| [PR #49](https://github.com/discoveryray/zhuyin-proofreader/pull/49) | 固定 develop 基準 `151b90f6f5f2a352e828fa6f73402dfb8c4f5617` 的已整合程式；原 PR CI run `38111745526/1`、actual merge develop push run `38116943592/1` 的原始證據與限定範圍。沿用前核對原件／tested SHA 與未變依賴。 | 新發布 HEAD 已跑正式 CI、真 GUI／整份教材驗收、或新 main 的 post-merge 完整驗證。 |
| [v5.7.0 Release](https://github.com/discoveryray/zhuyin-proofreader/releases/tag/v5.7.0)／CHANGELOG | v5.7.0《115國小健體2上》人工 staging、batch apply、incremental refresh 的歷史操作記錄；保留原版本原義。 | 本候選全部原生介面已驗收、其他教材已完成、或 Global 新介面已人工通過。 |
| 其他歷史功能證據 | 只有精確版本／SHA、原件及本次未變範圍可核對時再引用。 | 用歷史 NOT STARTED 當現況，或宣稱全部 v5.8 規劃完成。 |

## 軟體發布完成條件

本版功能範圍與六項人工驗收已採納；main 專用審查／證據方案及 main／tag／Release 授權仍待明確採納。準備 PR 為 `base=develop`、`compare=codex/release-v5.8.0-prep`；後續發布整合 PR 為 `base=main`、`compare=固定候選 develop`。現行 gate 只支援 develop，不能偽填 main gate PASS。

「開始驗證」後才進行準備 PR 的正式有效非視窗全集與兩輪獨立審查；準備 PR 合併後核對 actual develop merge 的短 push CI。main 發布 PR 必須有自身完整非視窗 CI 與兩位非實作者對完整適用 diff／原件的獨立 PASS；明確授權後以 merge commit 合併。main 實際 merge push 依現行政策執行完整非視窗驗證，不用 develop short 或 PR CI 替代。CI 任一 failed／pending／缺原件皆不能稱完成。

最終還須核對實際 main merge 的 ordered parents／tree、六項人工驗收結果、逐檔包與 runtime integrity、tag target 指向該 main merge、Release 附件及 checksum 一致，並具各項有效授權。tag／Release 沒有相應現行 workflow 觸發，不能編造 tag CI。發布候選 ZIP 不自動成為正式 Release。

教材全冊完成仍由自己的證據及 completion gate 決定；軟體發布不要求完成整份教材，也不使既有待辦自動結案。
