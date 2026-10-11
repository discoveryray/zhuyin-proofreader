# zhuyin-proofreader

臺灣國小教材 PDF 注音校對工具。v5.8.0 為現有功能穩定版的發布候選，尚未完成正式驗證及發布；不代表全部 v5.8 規劃已完成。

使用 Windows、Python 3.13.0。第一次使用請執行「安裝依賴.bat」，依 `requirements-release-lock.txt` 安裝固定 runtime 依賴及 pip；平常使用請執行「啟動_單機注音校對.bat」。沿用 Windows `py`／`python` 入口，不提供 EXE 安裝版。請確認所選 Python 是 3.13.0。

既有 PDF 校對、actual／expected 獨立判定、人工及 GPT 判定、暫存／批次 actual 更新、報告與相容專案續作均保留。Global 字形庫查閱與單項核准、交付／恢復狀態查閱、舊資料匯入預檢已提供；完整 aggregate metrics、Phase 6 查閱介面的批次 APPLY／恢復執行等項目延期，詳見使用指南。

升級請解壓到獨立程式目錄，先完整備份教材、專案、程式旁使用者資料及 Global，再以「繼續校對」沿用原 session。不要以新包覆蓋個人判定。軟體發布完成與教材全冊校對完成是不同條件；尚有待辦的教材仍可使用本版。

- [使用指南](docs/USER_GUIDE.md)
- [v5.8.0 升級與回復](docs/UPGRADE_V580.md)
- [v5.8.0 六項人工驗收](docs/RELEASE_ACCEPTANCE_V580.md)
- [開發文件目錄（repository 基準）](https://github.com/discoveryray/zhuyin-proofreader/blob/151b90f6f5f2a352e828fa6f73402dfb8c4f5617/docs/README.md)
- [測試說明（repository 基準）](https://github.com/discoveryray/zhuyin-proofreader/blob/151b90f6f5f2a352e828fa6f73402dfb8c4f5617/TESTING.md)
- [專案規範（repository 基準）](https://github.com/discoveryray/zhuyin-proofreader/blob/151b90f6f5f2a352e828fa6f73402dfb8c4f5617/AGENTS.md)
- [版本紀錄](CHANGELOG.txt)
