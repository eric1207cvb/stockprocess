# Stock Keyworder

跨平台圖庫照片關鍵字標注工具。支援 macOS / Windows，可用 OpenAI 或 Gemini 的線上視覺模型批次分析資料夾中的照片，並在介面中顯示可複製的 title、description、keywords。

## 功能

- 選擇照片資料夾後批次分析，單次上限 500 張。
- 可切換 `openai` / `gemini` provider。
- 可直接調整模型名稱。
- 可輸入自訂 prompt，對應各圖庫的 title、description、keywords 規則。
- 可用監看模式，照片放入資料夾後自動加入分析佇列。
- 單檔大小預設上限 64MB，超過會跳過且不呼叫 API。
- 右側結果表直接顯示檔名、標題、描述、關鍵字、備註與複製按鈕。
- GUI 介面預設不建立輸出資料夾，結果直接留在右側表格供複製。
- CLI 仍可產生 CSV、JSON、HTML 作備份。
- 同一支程式支援 GUI 與 CLI，方便手動操作或自動化排程。

## 安裝

macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python stock_keyworder.py
```

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python stock_keyworder.py
```

`Pillow` 用於壓縮送給 API 的照片與產生縮圖。沒有 Pillow 時程式仍可嘗試執行，但批次成本、速度與報表縮圖品質會比較差。

## API Key

GUI 可直接貼上 API key，但程式不會把 API key 寫入設定檔、CSV、JSON 或 HTML 報表。CLI 或排程建議使用環境變數：

macOS:

```bash
export OPENAI_API_KEY="你的 key"
export GEMINI_API_KEY="你的 key"
```

Windows PowerShell:

```powershell
$env:OPENAI_API_KEY="你的 key"
$env:GEMINI_API_KEY="你的 key"
```

CLI 也可從檔案讀取 key，避免 key 出現在 shell history 或系統 process list：

```bash
python stock_keyworder.py \
  --folder ./photos \
  --provider openai \
  --api-key-file ./openai_key.txt
```

瀏覽器介面可勾選 `記住這次輸入的 API Key 到本機`。macOS 會優先存到 Keychain；若 Keychain 不可用，才會退回本機使用者檔案 `~/.stock_keyworder_keys.json`。API key 空白時，如果勾選 `API Key 空白時使用本機暫存`，程式會自動讀取本機暫存 key。

## 資安與防濫用

- API key 不會儲存在 `~/.stock_keyworder_config.json`。
- API key 只有在使用者勾選 `記住這次輸入的 API Key 到本機` 時才會暫存。
- 錯誤訊息與 log 會遮罩常見 OpenAI/Gemini key 格式。
- 單次資料夾上限固定為 500 張。
- GUI 內建單檔大小安全上限，超過上限的照片會列入錯誤列，但不會送 API。
- GUI 內建每日 API request 安全上限，避免 API key 被誤用或濫用；CLI 可用 `--daily-limit` 調整。
- 每張照片至少消耗 1 次 API request；如果啟用重試，失敗重試也會計入每日上限。
- 大量批次或監看模式啟動前會要求確認。CLI 在非互動環境需加 `--yes` 才會執行大量或監看工作。
- 本機用量記錄存在 `~/.stock_keyworder_usage.json`，只記錄日期、provider/model 與 request 次數，不含照片內容或 API key。

## 使用方式

啟動 GUI：

```bash
python stock_keyworder.py
```

預設會開啟本機瀏覽器介面，例如 `http://127.0.0.1:8765/`。資料仍在你的電腦本機處理，瀏覽器只是操作畫面。

macOS 也可以直接雙擊 `start_mac.command`。如果 macOS 顯示沒有執行權限，請在此資料夾執行一次：

```bash
chmod +x start_mac.command
```

Windows 可以直接雙擊 `start_windows.bat`。

如果你看到的是 Python 原始碼，代表你是用編輯器打開了 `stock_keyworder.py`，還沒有執行程式。請改用上面的啟動方式。

備用 Tk 桌面介面：

```bash
python stock_keyworder.py --tk-gui
```

GUI 介面分成四個區塊：

- `1 照片來源`：填入照片資料夾，會顯示目前可分析照片數。
- `2 AI 模型`：選擇 OpenAI 或 Gemini、填入模型名稱與 API key。
- `3 圖庫需求 Prompt`：輸入圖庫規則，或套用通用模板後自行修改。
- `4 執行`：選擇是否監看資料夾，並開始或停止分析；結果會直接顯示在右側表格。

右側會顯示進度與分析結果清單，每列包含檔名、title、description、keywords、notes，並提供複製關鍵字、複製整列與刪除該筆。刪除只會從目前清單移除，不會刪除原始照片。

若來不及把全部照片登入到圖庫，可按 `儲存進度`。下次啟動後按 `載入進度`，會恢復上次剩餘清單。進度存在本機 `~/.stock_keyworder_pending.json`，最多保存 500 筆，不包含 API key。

Prompt 可在介面輸入 `Prompt 檔名` 後按 `儲存 Prompt`，之後從下拉選單選擇並按 `載入 Prompt`。Prompt 檔會存在：

```text
~/.stock_keyworder_prompts/
```

CLI 範例：

```bash
python stock_keyworder.py \
  --folder ./photos \
  --provider openai \
  --model gpt-5.5 \
  --max-images 500 \
  --max-file-mb 64 \
  --daily-limit 500
```

Gemini 範例：

```bash
python stock_keyworder.py \
  --folder ./photos \
  --provider gemini \
  --model gemini-3.5-flash
```

使用 prompt 檔案：

```bash
python stock_keyworder.py \
  --folder ./photos \
  --prompt-file ./stock_prompt.txt
```

監看資料夾：

```bash
python stock_keyworder.py \
  --folder ./photos \
  --provider openai \
  --watch \
  --yes
```

監看模式會等新檔案數秒未再變動才開始分析，避免照片仍在複製時送出 API。可調整輪詢與等待秒數：

```bash
python stock_keyworder.py \
  --folder ./photos \
  --watch \
  --watch-interval 5 \
  --settle-seconds 3
```

## CLI 輸出

GUI 介面預設不建立輸出資料夾，主要工作流是在右側結果表直接複製 `title`、`description`、`keywords` 或整列資訊。

CLI 每次執行預設會在照片資料夾內建立：

```text
stock_keyworder_output_YYYYMMDD_HHMMSS/
  stock_keywords.csv
  stock_keywords.json
  stock_keywords_report.html
  thumbnails/
```

CSV 欄位包含：

- `filename`
- `title`
- `description`
- `keywords`
- `categories`
- `notes`
- `copy_line`
- `status`
- `error`

`copy_line` 格式為：

```text
title<TAB>description<TAB>keyword1, keyword2, keyword3
```

## 單檔大小設定

預設 `64MB` 是為高解析全片幅 JPEG 保留的安全上限，不是固定平均值。不同相機、壓縮品質、細節量與後製輸出設定都會改變 JPEG 檔案大小。GUI 會使用內建安全值；若進階自動化流程需要調整，可用 CLI 參數處理。這個限制主要是避免意外放入 TIFF、RAW 或超大匯出檔造成 API 成本與記憶體風險。

CLI 調整：

```bash
python stock_keyworder.py \
  --folder ./photos \
  --max-file-mb 64
```

## 打包成桌面 App

安裝 PyInstaller：

```bash
python -m pip install pyinstaller
```

macOS:

```bash
pyinstaller --onefile --windowed --name StockKeyworder stock_keyworder.py
```

Windows:

```powershell
pyinstaller --onefile --windowed --name StockKeyworder stock_keyworder.py
```

輸出會在 `dist/` 裡。

## Prompt 建議

預設 prompt 偏向國際圖庫英文 metadata。若要針對不同圖庫，直接在 Prompt 欄位寫入該圖庫規則，例如：

```text
請依照我指定的圖庫規則輸出英文 title、description、keywords。
Title 最多 80 個字元。
Description 使用 1 句自然英文。
Keywords 輸出 35 到 49 個英文關鍵字，最重要的關鍵字放前面。
不要包含品牌、商標、推測地點、推測姓名或不存在的物件。
若有可識別人物、商標、車牌或受保護藝術品，notes 請標示可能風險。
```

## 實作參考

- OpenAI Images and vision guide: https://developers.openai.com/api/docs/guides/images-vision
- Gemini image understanding guide: https://ai.google.dev/gemini-api/docs/image-understanding
