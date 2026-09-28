# v5.6.2 Implementation Audit

## 修正目標

- 讓 v5.6.1 已完成人工判定、待處理為 0 的專案可直接續作，不要求整批重解碼。
- 修正三上來源限定「轉動」規則造成 `M25-ZHUAN-DONG` 強制回歸失敗。
- 區分「同冊其他分檔尚未定位」與「歷史來源版本不適用」，避免舊頁碼案例永久阻擋現版教材。
- 讓 actual 待判定為 0 成為正常 no-op，而不是例外與代碼 1。

## 實作

1. `standalone_proofread.py`
   - 新增 `--repair-project`／`repair_project_state()`。
   - 由現有 sealed session 找回相同 PDF，交給既有增量 pipeline；actual fingerprint 相容時 reuse，expected 資產變更時重建 candidate，再重播人工事件並重算 completion gate。
   - `export_actual_pending_for_gpt()` 在 0 組時回傳 `None`；CLI 顯示明確訊息並正常結束。
   - expected／差異匯入完成後不再以舊 manifest 顯示中途 actual 數量；只引導使用本次報告／`pipeline_status.json`，升級規則時再執行修復。

2. 歷史回歸適用性
   - `pronunciation_regressions.csv` 使用 `applicability_mode`、`source_pdf_sha256`、`target_occurrence_index`、`locator_context` 分離來源版本與 occurrence identity。
   - `hard_gate` 指定 SHA 時只對 exact PDF bytes 適用；SHA 不符為 `SOURCE_NOT_APPLICABLE`。既有未綁 SHA 的 hard gate 維持原頁碼 locator 相容行為，新跨版本 gate 不再沿用此隱式模式。
   - `reference_only` 不再於函式入口直接跳過。現版仍依頁碼、完整詞語、目標字、詞內序號與必要 context 嘗試定位；找到後記錄 PASS／FAIL，找不到記錄 `REFERENCE_NOT_FOUND`，所有結果均不進 completion denominator。
   - 新增 `portable_gate`：歷史頁碼與歷史 actual／expected 均不參與 identity；全冊唯一命中才執行，0 筆為 `NOT_EXECUTED`，多筆為 `IDENTITY_AMBIGUITY/FAIL`，split 重複執行同樣 fail closed。
   - 三上 2026-08-15 的 15 筆案例逐筆覆核後，只有 `NEG-3UP-005`「一級棒／一」以完整詞 locator 升為 portable gate；其餘 14 筆因短詞、重複 occurrence、頁面／glyph 來源限制或缺少 context，維持 reference-only。沒有捏造 source PDF SHA。
   - candidate workbook、authoritative payload hash、全冊 split 聚合與技術稽核報告均保存適用性狀態、是否計入完成門檻、locator context 與命中數。

3. 「轉動」
   - 撤銷 `V38-MOE-ZHUANDONG-SPIN-3UP` 的ㄓㄨㄢˋ結果，依使用者既有回標「旋轉、轉動、轉速、轉盤均ㄓㄨㄢˇ」改為ㄓㄨㄢˇ。
   - actual 證據不參與此 expected 規則修正。

4. GUI
   - 「更新報告」改為「修復／更新報告」。
   - `PROCESSING_FINISHED` 且仍有失敗門檻時，直接列出 `failed_gates`。

## 安全邊界

- actual 與 expected 證據鏈仍完全分離。
- mandatory regression 仍必須 required>0、全數執行、0 fail、0 not-executed、0 duplicate。
- hard／portable PDF regression 仍必須 required==executed、0 fail、0 not-executed、0 duplicate；reference-only PASS／FAIL 只進非阻擋稽核統計。
- historical actual／expected truth 只在 occurrence 獨立定位後進行 evaluation，不得參與 locator。
- session／review identity、PDF SHA、輸出檔 hash、來源資產 manifest 與集合對帳均保留。

## 驗證

- Runtime asset manifest：20 assets，`ok=True`，0 errors，0 warnings。
- 三上檔名範圍 mandatory integration：36/36 executed，36/36 PASS。
- unittest suite：115 passed，1 個桌面顯示測試 skipped。
- 其餘 pytest-style top-level tests：32 passed。
- 新增測試涵蓋：exact SHA hard gate、reference-only 實際稽核、portable 唯一／零／多重定位、讀音真值不參與 identity、split 唯一 owner／重複執行、definition drift、non-gating reference 與 mandatory 安全隔離，以及三上 15 筆分類。

### 第二階段 develop 驗證（2026-08-26）

- applicability／completion／既有 regression semantics／第一階段 reusable expected preservation：42/42 通過；其中第一階段保護 7/7 通過。
- historical locator：6/6 通過；dual-evidence architecture：5/5 通過。
- 技術稽核報告在只於隔離測試副本校正既有資產 SHA 後：1/1 通過，含「歷史回歸稽核」新欄位。
- 完整 unittest discovery：142 項，137 通過、2 失敗、2 錯誤、1 個無顯示環境 GUI 測試 skipped。兩個失敗與報告錯誤均由 develop 基準既有的 14 個本次範圍外資產 SHA 不符造成；另一錯誤是環境未安裝 pytest，但該檔 6 個 locator 測試已直接執行全數通過。未降低或自動更新 fail-closed 資產檢查。

## Production runtime asset manifest integrity（2026-08-27）

### 成因與判定

- Production root 的 `validate_asset_manifest()` 原始結果為 `ok=False`：20 個固定資產均存在、actual／expected chain roster 正確、14 個 CSV 的 schema／列數／唯一鍵／注音 canonicalization 全部通過，唯一錯誤是 raw SHA-256 不符。
- 14 個檔案在 v5.6.1 匯入 commit `005ccf0440ec9800be69f41ce770ec1e34578944`、初始 v5.6.2 `daa1d5a3fa5c2be5309f095439f60a0d1e4cb702`、第一階段 `448931d96158bd87a88db0049572384fa18033cd`、第二階段 `85858102b351359c4eccc993e406fd211563e7e5`、第三階段 `93edd7112ef23bd15629e97c6b590d4b97464e66` 的 Git blob 均逐檔相同；最近一次內容 commit 也全部是 `005ccf0 Import current zhuyin proofreader`。
- 每個 manifest recorded SHA 都精確等於同一份 UTF-8 BOM CSV 將 LF 換回 CRLF 後的 SHA；不存在任何欄位、列、順序、規則、glyph hash、verification、source 或 note 差異。v5.6.1 audit 在 Windows 工作樹記錄 `ok=True`，而第二階段 Linux checkout 揭露 14 個 mismatch，亦與跨平台 EOL normalization 成因一致。
- 分類為 **B / restore asset bytes**：Git 初次匯入時無 `.gitattributes`，將 14 份已核准 CRLF bytes 正規化為 LF，manifest 並非接受未知內容的 stale hash。保留原 manifest SHA，將 14 份 Git 資產 bytes 恢復為原核准 CRLF，並以 `.gitattributes -text` 凍結所有 18 份 manifest-protected CSV 的 raw bytes。
- 不升 manifest schema：資料 schema、資產 roster 與 evidence semantics 完全未變。沒有修改 `runtime_asset_manifest.json`、actual／expected fingerprint 程式或 source validation；既有 Windows/CRLF fingerprints 因此不會因本修正被無謂改寫。

### 原始 mismatch 與五個基準版本

下表的「五基準 Git SHA-256」分別是 v5.6.1/`005ccf0`、`daa1d5a`、`448931d`、`8585810`、`93edd71` 的 raw Git LF payload；五者逐檔完全相同。「修復後」為恢復 CRLF 後的 raw bytes，且等於未修改的 manifest recorded SHA。

| asset / path | chain | manifest recorded SHA = 修復後 | 修復前 LF SHA = 五基準 Git SHA-256 | manifest schema requirement | 結論 |
|---|---|---|---|---|---|
| handbook_pronunciation_rules / `統一用字手冊_注音規則.csv` | expected | `94ec5cf41da7551f1abdf63b4ff61bc29333635d54292a07f82cb06c0bde8015` | `4001c0bc802b13d617ce2fb665c502e7166ea23a1bc221d59f1947ff9fc41917` | 382 rows；key=`rule_id`；`expected_reading` 可 canonicalize | restore asset bytes |
| handbook_pronunciation_constraints / `統一用字手冊_字音限制.csv` | expected | `96778d9b85aacc51ccd73d859c503fdb358c5d3aac0d06adfb6997cdbd18876e` | `121e8e933e1ae77466117659d820ec156e15271fedcb1225a283b056faa7a496` | 73 rows；key=`char`；`allowed_readings` 可 canonicalize | restore asset bytes |
| character_overrides / `character_overrides.csv` | expected | `605e27fed2dea54873421b1fd9cb9b870c4b5cff65031c62617e765a27becf6b` | `6d1db8e7c996c42c87d4c2808d5481d0bdbecbd17e11956e528d20f73d864539` | 2 rows；key=`pdf_contains,physical_page,x0,y0,original_char` | restore asset bytes |
| source_context_overrides / `source_context_overrides.csv` | expected | `8ecde58da4d65c0352aef2daff4d2b1e40d895df17ada850a1a94113990256a0` | `a854736eb8505ca0bb7de17ea3de8b9548acf9d3dbc4d9f840a0ca40bcd7071f` | 15 rows；key=`pdf_contains,physical_page,target_char,x0,y0` | restore asset bytes |
| zhuyin_component_map / `zhuyin_component_map.csv` | actual | `53a5369e96f60a552aeae6503b1b2584741bf3930facedf1f4d273048c90b291` | `590c3bb57cb0380ebc8ce74626822b36e36afa6811ec9cc645c1ea904ac57266` | 1,384 rows；key=`font,zhuyin_component_id`；`bopomofo` 可 canonicalize | restore asset bytes |
| font_compatibility_groups / `font_compatibility_groups.csv` | actual | `e15300cd4ee0e23258c642c5fe749bb6163561729a608f180717b7317df55443` | `41451fc0f1b8887fd1ae54babb0b02958aca760384b308b74c6ffb05bae82298` | 6 rows；key=`group_id,font_regex` | restore asset bytes |
| cff_bopomofo_symbol_map / `cff_bopomofo_symbol_map.csv` | actual | `c2db9748ec04ed5457f772539e4c4fbf7bf5008f4bc7a410795cbd5a2956efc5` | `8e7fbf3eb026191e5525e538e514432fae7f7557e7a9ba851f26597d16203ebc` | 746 rows；key=`style_group,signature`；`bopomofo_symbol` 可 canonicalize | restore asset bytes |
| cff_crossfamily_cid_consensus / `cff_crossfamily_cid_consensus.csv` | actual | `9953988a45ea28b9e1214b1fa9c3e6d9e02e23f3c5562d8e39284f676cf35e33` | `f442959c602c7260d7ca8841426498cdf4ad43081305d1f81e5b648e75529085` | 2,589 rows；key=`reference_mode,variant,glyph_id` | restore asset bytes |
| ttf_xref_component_overrides / `ttf_xref_component_overrides.csv` | actual | `693b06d52f4314c980c4b06bc3a4484812d85c1d762413d97b295928db9a2151` | `0b5e19844b8983d6bb52aa4337a085f99968e480c280849fcc7cd53a1b3b7a60` | 100 rows；key=`pdf_contains,page,font_xref,font,zhuyin_component_id`；`bopomofo` 可 canonicalize | restore asset bytes |
| ttf_verified_component_transforms / `ttf_verified_component_transforms.csv` | actual | `1c4429794ee93ea720a610b66899d6a8734ea9ff685b1599bbff9d2dbf6ed08f` | `deb468a1891f8665a177619168f8ab6133bf911a3631a0a6e099db9aa473b2da` | 455 rows；key=`target_font,target_component_id,target_glyph_sha256`；`actual_zhuyin` 可 canonicalize | restore asset bytes |
| ttf_verified_glyf_fingerprints / `ttf_verified_glyf_fingerprints.csv` | actual | `1710cace49cc7f2d797b163c022701fd65d572e2d9e26fb8cc24f5014fedf526` | `9a9b22a8d6e67a517daf556240b6fad08e7e3154c882758e36b055c2be9c1a05` | 225 rows；key=`glyph_sha256`；`bopomofo` 可 canonicalize | restore asset bytes |
| ttf_verified_outline_signatures / `ttf_verified_outline_signatures.csv` | actual | `d00e60764d976fbd19934f20399d5fa9326cae12039fbd45a5f9973368a8f4a0` | `c5302d57af7b0fa5d97f38be0b183c8942e5e1c06e3bcc4e8e8c7c175862f014` | 1,563 rows；key=`outline_signature`；`bopomofo` 可 canonicalize | restore asset bytes |
| ttf_verified_symbol_templates / `ttf_verified_symbol_templates.csv` | actual | `0c7949b01411615782bab1e61ebcbfc61e6aad13e238d6be5e33edd25c4ec5fa` | `3821fb4d82191469ac9334a9527817d081d2eed55607092d5660ac7a2f5ab9c8` | 633 rows；key=`template_type,outline_signature` | restore asset bytes |
| structural_detection_exclusions / `structural_detection_exclusions.csv` | actual | `d9b685b83514d66aeb4626a714d22b7f14f48dd2149f5a63c30ac90168f94573` | `9e39590beb216bccb3e99837c1b74696c1b502713ba4afebfa8dfaae718b3bbc` | 2 rows；9-field occurrence key | restore asset bytes |

### 證據與作用域覆核

- Handbook：382 筆規則仍為 378 筆 `handbook_highest`（統一用字手冊）＋4 筆具日期的 `user_truth`；73 筆字音限制全部保留手冊來源與章節。沒有用 actual 反推 expected。
- Occurrence／context overrides：2 筆字元覆寫與 15 筆句境重建逐欄未變；所有定位仍綁 PDF、頁面、座標與字元，句境 note 仍明載不提供 actual/expected 注音答案，沒有泛化為全域規則。
- Zhuyin component map：1,384 筆 `font + component_id` 與所有 verification 說明逐字未變，沒有接受新的 decoder mapping。
- CFF：746 筆 symbol map 的 verification/provenance 未變；2,589 筆 consensus 仍為 2,587 筆無衝突、2 筆 conflict，沒有降低 conflict gate。
- TTF：100/455/225/1,563/633 筆 xref、transform、glyf、outline、template 資產逐欄未變；455 筆 transform confidence 仍全為 `safe`，glyph/outline hash 與 verification/validation 未被覆蓋或降級。
- Structural exclusions：仍只有 2 筆同一「有效期限」不可見注音的 exact PDF/page/xref/glyph/coordinate 排除，沒有增加列或擴大 selector。

### 防再發

- `.gitattributes` 只把 manifest 中 18 份固定 CSV 標記為 `-text`，使 Git 保存並交付 manifest 實際核准的 raw bytes；5 份不在 manifest 的專案動態 actual 證據 CSV 不在此清單，未改其資料鏈行為。
- `tests/test_runtime_asset_manifest_integrity_v562.py` 驗證 production root 全綠、14 份修復檔的 CRLF raw SHA 等於原 manifest、正規化回 LF 後等於五個歷史基準 payload、全部 manifest CSV 均受 byte-freeze policy 保護，以及 actual/expected roster 完全未變。

### 最終驗證

- Production `validate_asset_manifest()`：`ok=True`；schema `2.6.2`；20 assets；expected 9 / actual 11；0 SHA mismatch；0 schema error；0 missing/invalid asset；0 error；0 warning。
- 新完整性測試＋architecture：46/46 通過，包含 raw hash fail-closed 與 actual/expected fingerprint isolation。
- 全部 unittest-based modules：154 tests，`OK (skipped=1)`；唯一 skipped 為無 `$DISPLAY` 的 Tk 可見版面測試。
- 環境未安裝 pytest（`No module named pytest`），不是程式測試失敗。7 個 pytest-style 檔案改以不寫入 repository 的 direct runner 執行：32/32 通過，其中 historical locator 6/6、dual-evidence 5/5、glyph truth conflict 3/3、structural/context 2/2。
- `git diff --check` 通過；14 個資產以 `--ignore-cr-at-eol` 比較均為 0 semantic diff。
- 沒有 production source validation blocker；本階段結論為 `SOURCE ASSETS READY FOR FINAL REVIEW`，但依工作範圍不進行 final release review。
