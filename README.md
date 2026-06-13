# Stock Keyworder

跨平台圖庫照片關鍵字標注工具。支援 macOS / Windows，可用 OpenAI 或 Gemini 的線上視覺模型批次分析資料夾中的照片，並在介面中顯示可複製的 title、description、keywords。

## 功能

- 選擇照片資料夾後批次分析，單次上限 500 張。
- 可切換 `openai` / `gemini` provider。
- 可直接調整官方 API model ID，內建 `gpt-5.5`、`gpt-4o-mini`、`gpt-4o`、`gemini-3.1-flash-lite`、`gemini-3.5-flash` 等官方選項。
- 可輸入自訂 prompt，對應各圖庫的 title、description、keywords 規則。
- 可用監看模式，照片放入資料夾後自動加入分析佇列。
- 單檔大小預設上限 64MB，超過會跳過且不呼叫 API。
- 預設啟用本機相似圖沿用：同批或續跑中若判定照片高度相似，會沿用前一張 metadata，不再呼叫 API，降低 500 張批次的 token/request 消耗。
- 右側結果表直接顯示縮圖、檔名、中文摘要、標題、描述、關鍵字、備註與複製按鈕。
- 同一張照片可依 prompt 顯示多組圖庫關鍵字，例如 Adobe Stock 英文 49 個、日本圖庫日文 50 個；每組都有獨立複製按鈕。
- 進度區會顯示目前檔名、API 嘗試次數、已等待時間、重試倒數、完成/錯誤/剩餘統計，避免長時間 API 等待被誤認為當機。
- 可儲存/載入進度，並從已完成紀錄續跑未完成照片。
- GUI 介面預設不建立輸出資料夾，結果直接留在右側表格供複製。
- CLI 仍可產生 CSV、JSON、HTML 作備份。
- 同一支程式支援 GUI 與 CLI，方便手動操作或自動化排程。

## 安裝

每台新電腦只要執行啟動檔即可。啟動檔會先找 Python 3.9 以上；如果找不到，會詢問是否自動安裝 Python。Python 就緒後，啟動檔會自動建立 `.venv`、安裝/更新 requirements，然後開啟程式。

- Windows 會優先使用 Microsoft `winget` 安裝 Python。
- macOS 會優先使用 Homebrew 安裝 Python；若沒有 Homebrew，會詢問是否先安裝 Homebrew。
- 若使用者拒絕安裝，或該電腦沒有可用的套件管理工具，請改到 Python 官網手動安裝。

macOS:

```bash
./start_mac.command
```

Windows:

```powershell
.\start_windows.bat
```

如果已經有 Python，只想初始化環境、不啟動程式，可執行：

```bash
python3 setup_environment.py
```

Windows:

```powershell
py -3 setup_environment.py
```

`Pillow` 用於壓縮送給 API 的照片與產生縮圖。啟動檔會自動安裝 requirements；如果套件變更或環境損壞，再次執行啟動檔會自動修復。

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
- GUI 預設每日 API request 安全上限為 600：500 張照片 + 100 次重試緩衝，避免 API key 被誤用或濫用；CLI 可用 `--daily-limit` 調整。
- 每張非沿用照片至少消耗 1 次 API request；如果啟用重試，失敗重試也會計入每日上限。
- 相似圖沿用只在本機用縮圖雜湊判斷，不會上傳第二張相似照片。沿用列會在 notes 標示來源檔名與相似距離，方便上架前確認。
- 若模型回 `503 high demand` 或暫時限流，程式會改用較長等待重試；連續滿載時會先暫停批次，避免整批 500 張都變成錯誤。
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

- `1 照片來源`：可按 `選擇資料夾` 開啟系統資料夾選擇視窗，或手動貼上照片資料夾路徑。
- `2 AI 模型`：選擇 OpenAI 或 Gemini、填入官方 API model ID 與 API key。
- `3 圖庫需求 Prompt`：輸入圖庫規則，或套用通用模板後自行修改。
- `4 執行`：選擇是否監看資料夾，並開始或停止分析；結果會直接顯示在右側表格。若只想處理目前資料夾內照片，請取消勾選 `監看資料夾`；監看模式會在目前照片處理完後繼續等待新照片，直到按 `停止` 或達到 500 張上限。

右側會顯示進度與分析結果清單，每列包含縮圖、檔名、中文摘要、title、description、keywords、notes。複製區提供標題、描述、標題+描述、主整列，以及每個圖庫 keyword group 的「關鍵字」與「整列」按鈕。Keywords 欄會顯示每組目前輸出的 keyword 數量，方便檢查日文圖庫 50 個關鍵字等規則。刪除只會從目前清單移除，不會刪除原始照片。

若 AI 辨識錯誤，可在該列按 `修正重辨`，輸入正確資訊，例如「這張是鹹蛋苦瓜，不是炒高麗菜」。程式會用同一張原圖與修正資訊重新送 API，成功後直接替換原本那列，不會新增一筆或多佔 500 張名額。修正重辨前請先停止目前批次或監看工作。

監測紀錄預設收合在右側底部，只佔一行；需要查 API 重試、偵測新照片或錯誤訊息時再展開。

若來不及把全部照片登入到圖庫，可按 `儲存進度`。下次啟動後按 `載入進度`，會恢復上次剩餘清單；按 `繼續未完成` 會分析同一資料夾中尚未完成的照片。若 provider、model、prompt 或 metadata 欄位規格已變更，舊完成資料會被視為舊規則產物並重新分析。進度存在本機 `~/.stock_keyworder_pending.json`，最多保存 500 筆，不包含 API key。

分析中若進度條暫時不動，請看進度區的「目前檔名」「已等待時間」與「重試倒數」。模型正在回應或等待重試時，這些數字會持續更新；只有瀏覽器完全停止更新或 log 不再刷新很久，才需要重啟程式。

500 張批次建議流程：

1. 將要處理的照片放進同一個資料夾，最多 500 張。
2. 開啟程式，填入資料夾路徑、provider/model、API key 與圖庫 prompt。
3. 按 `開始`，程式會逐張分析並把結果顯示在右側表格。
4. 登入圖庫完成的列可按 `刪除` 移出清單；程式仍會記住該照片已完成，續跑時不會重複分析。
5. 中途要休息時按 `停止`，再按 `儲存進度`。
6. 下次開啟後按 `載入進度`，再按 `繼續未完成`。
7. 若某列因模型輸出格式錯誤而失敗，續跑時會移除該錯誤列並重新嘗試。
8. 若 Gemini 顯示模型滿載，先等幾分鐘後按 `繼續未完成`；也可以改用下拉建議中的其他 Gemini 模型或 OpenAI。

模型欄位使用官方 API model ID。OpenAI 下拉清單包含 `gpt-5.5`、`gpt-4o-mini`、`gpt-4o`；Gemini 下拉清單包含 `gemini-3.1-flash-lite`、`gemini-3.5-flash`、`gemini-3.1-pro-preview`、`gemini-3-flash-preview` 等官方 ID。程式仍接受 `3.1flash-light`、`3.1 pro` 這類常見輸入，但送出前會自動轉成官方 ID。

若要同一張照片同時產生多個圖庫的關鍵字，請在 prompt 明確分組，例如「Adobe Stock：英文 49 個 keywords；日本圖庫：日文 50 個 keywords」。程式會要求模型回傳多組 `keyword_groups`，並在同一個 Keywords 欄內分組顯示。

重複或相似圖組的節省策略預設開啟。程式會先用本機縮圖雜湊比對已完成照片；距離在安全門檻內時，直接沿用前一張的 title、description、keywords 與 copy line，該張不會送 API。這適合連拍、同場景小幅構圖差異或同物件不同裁切；若照片內容其實不同，請在表格中刪除該列後調整 prompt 或關閉 CLI 的 `--no-reuse-similar` 重新跑。

Prompt 可在介面輸入 `Prompt 檔名` 後按 `儲存 Prompt`，之後從下拉選單選擇並按 `載入 Prompt`。不需要的 Prompt 可從下拉選單選取後按 `刪除 Prompt`。Prompt 檔會存在：

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
  --daily-limit 600
```

Gemini 範例：

```bash
python stock_keyworder.py \
  --folder ./photos \
  --provider gemini \
  --model gemini-3.1-flash-lite
```

關閉相似圖沿用：

```bash
python stock_keyworder.py \
  --folder ./photos \
  --provider openai \
  --model gpt-4o-mini \
  --no-reuse-similar
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
- `zh_summary`
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

預設 prompt 偏向國際圖庫英文 metadata。`keywords` 會依照 Prompt 中指定的圖庫、語言、數量與排序規則輸出；如果寫日文圖庫規則，keywords 會用日文常用搜尋詞並依搜尋權重排序。如果寫英文圖庫規則，keywords 會用英文並把最重要的詞放前面。

英文圖庫範例：

```text
請依照我指定的圖庫規則輸出英文 title、description、keywords。
Title 最多 80 個字元。
Description 使用 1 句自然英文。
Keywords 輸出 35 到 49 個英文關鍵字，最重要、最可能被買家搜尋的 10 個放前面。
不要包含品牌、商標、推測地點、推測姓名或不存在的物件。
若有可識別人物、商標、車牌或受保護藝術品，notes 請標示可能風險。
```

日文圖庫範例：

```text
請依照日文圖庫上架規則輸出 metadata。
Title 使用自然日文，最多 60 字。
Description 使用 1 句自然日文。
Keywords 輸出 30 到 45 個日文關鍵字，使用日本買家常搜尋的詞。
前 10 個關鍵字依序放：主要被攝體、場景、用途、情緒或概念。
可使用常見片假名搜尋詞，但不要混入英文，除非是日本圖庫常用外來語。
不要加入不可確認的品牌、人物姓名、活動名稱或不存在的物件。
```

## 實作參考

- OpenAI Images and vision guide: https://developers.openai.com/api/docs/guides/images-vision
- Gemini image understanding guide: https://ai.google.dev/gemini-api/docs/image-understanding
