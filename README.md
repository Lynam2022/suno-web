# suno-web

用 Playwright 自動化 Suno 網頁版（`suno.com/create`），把音樂生成包成 job 式 HTTP API 與 CLI：送單立刻拿到 `job_id`，輪詢到 `done` 之後下載 mp3。走的是登入帳號的網頁額度，Suno 沒有官方 API，也不按次計費。

架構比照 [gemini-web](https://github.com/yazelin/gemini-web)。寫入走 UI（真瀏覽器填表單按 Create），讀取走網路側錄（攔頁面自己在打的 clip feed JSON），DOM 改版只影響寫入那一半。

瀏覽器層用的是**系統上真正的 Google Chrome**：本服務自己把它啟動起來（帶 `--remote-debugging-port=0`），再用 CDP 接上去。這一點不能改成 Playwright 內建的 Chromium，理由見「已知限制」。

> **先看這個：** 自動化 Suno 網頁違反 Suno 服務條款，帳號有被封的風險。詳見「已知限制」。

## 安裝

需要系統上裝好 **Google Chrome**（不是 Chromium，見「已知限制」）。Ubuntu 可以直接裝官方套件，或把 deb 解到自己的目錄再用 `CHROME_BINARY` 指過去。

還沒發佈到 PyPI，目前從原始碼裝：

```bash
cd ~/suno-web
uv sync --extra dev
```

`suno-web install` 只檢查 Chrome 在不在、找不到就印安裝方式，不下載任何瀏覽器。之後所有指令都可以寫成 `uv run suno-web <子指令>`，或直接叫 `.venv/bin/suno-web`。

## 首次登入 Suno

```bash
uv run suno-web login
```

會開一個普通的 Chrome 視窗（需要桌面環境或 X forwarding）。在裡面登入 Suno，確認看得到 Create 頁面，**把視窗關掉**，指令會自動驗證登入態並印出剩餘點數。登入態存在 `~/.suno-web/profiles/`，之後不必再登入；過期了就重跑同一個指令。

這個視窗刻意**不接 CDP**：Suno 的 Clerk 在登入流程掛了 Cloudflare Turnstile，被程式驅動的瀏覽器過不了那一關（`auth.suno.com/v1/client/verify` 會收到 `captcha_error=600010`，畫面變成 Initialization Error）。沒被驅動的視窗就沒這個問題。

多個帳號用 `-w`：

```bash
uv run suno-web login -w 1    # 第二個帳號，profile 存在 ~/.suno-web/profiles-1/
```

## CLI

```bash
suno-web install   # 檢查真 Chrome 在不在
suno-web login     # 人工登入一次
suno-web serve     # 起 HTTP API（預設 0.0.0.0:8071）
suno-web health    # 打 /api/health 並印出 JSON

# Simple 模式：一句描述
suno-web generate "a cheerful short ukulele tune" -o out/

# Custom 模式：歌詞、曲風、歌名
suno-web generate --lyrics-file lyrics.txt --style "lo-fi hip hop" \
                  --title "深夜寫程式" -o out/

# 純音樂
suno-web generate "a slow jazzy piano interlude" --instrumental -o out/
```

`generate` 需要 `serve` 已經在跑。它負責送單、每 5 秒輪詢一次、跑完把 `downloadable` 為 true 的 clip 存成 `<clip_id>.mp3` 放進 `-o` 指定的目錄；`downloadable` 為 false 的會印一行「跳過」。

| 參數 | 說明 |
|---|---|
| 位置參數 | Simple 模式的一句描述 |
| `--lyrics-file` | Custom 模式的歌詞檔路徑（UTF-8）。帶了這個或 `--style` 就走 Custom 模式，位置參數會被忽略 |
| `--style` | 曲風 |
| `--title` | 歌名，只在 Custom 模式會送出 |
| `--instrumental` | 純音樂 |
| `-o, --output` | 輸出目錄，預設當前目錄 |
| `--server` | 服務位址，預設吃環境變數 `SUNO_WEB_SERVER`，沒設就是 `http://localhost:8071` |
| `--api-key` | API 金鑰，預設讀環境變數 `SUNO_WEB_API_KEY` |

`health` 也吃 `--server`。服務跑在別台機器時，設一次環境變數就不必每次打參數：

```bash
export SUNO_WEB_SERVER=http://192.168.11.11:8071
export SUNO_WEB_API_KEY=<那台服務 .env 裡的其中一把 API_KEYS>
suno-web health
suno-web generate "a gentle lo-fi beat" -o out/
```

## HTTP API

金鑰語意照 gemini-web：`.env` 的 `API_KEYS`（逗號分隔）只要設了任何一把，`/api/generate` 與 `/api/jobs/*` 就都要帶 header `x-api-key`，沒帶回 403；一把都沒設時維持開放，只適合本機開發。`/api/health` 不驗金鑰，方便監控直接打。

### POST /api/generate

```bash
curl -X POST http://localhost:8071/api/generate \
  -H "Content-Type: application/json" \
  -H "x-api-key: YOUR_KEY" \
  -d '{"prompt": "a cheerful short ukulele tune"}'
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| `prompt` | string | Simple 模式的一句描述 |
| `lyrics` | string | Custom 模式的歌詞 |
| `style` | string | Custom 模式的曲風 |
| `title` | string | Custom 模式的歌名 |
| `instrumental` | bool | 純音樂，預設 `false` |
| `timeout` | int | 這一單的秒數上限，預設吃 `DEFAULT_TIMEOUT`（600） |

有 `lyrics` 或 `style` 就走 Custom 模式，此時 `prompt` 會被忽略。回應：

```json
{"job_id": "d9025d66e20a", "status": "queued"}
```

`prompt` 與 `lyrics`／`style` 全空回 400 `invalid_request`；佇列滿（`QUEUE_MAX_SIZE`，預設 10）回 429 `queue_full`。

### GET /api/jobs/{job_id}

```bash
curl -s -H "x-api-key: YOUR_KEY" http://localhost:8071/api/jobs/d9025d66e20a
```

```json
{
  "job_id": "d9025d66e20a",
  "status": "done",
  "clips": [
    {
      "id": "00000000-1111-2222-3333-444444444444",
      "title": "示範曲名",
      "status": "complete",
      "duration": 199.88,
      "downloadable": true,
      "audio_url": "/api/jobs/d9025d66e20a/files/00000000-1111-2222-3333-444444444444.mp3",
      "image_url": "/api/jobs/d9025d66e20a/files/00000000-1111-2222-3333-444444444444.jpeg"
    }
  ],
  "error": null,
  "error_message": null,
  "elapsed_seconds": 187.2
}
```

- `status`：`queued`、`generating`、`done`、`error` 四種。
- `clips`：這一單新生出來的全部 clip。`audio_url` 與 `image_url` 只有檔案真的存下來才會出現。
- VIP 鎖住的 clip 標 `downloadable: false`、只留 metadata，整單不算失敗（本輪驗收沒有遇到這種 clip，見「已知限制」）。
- 失敗時 `error` 是錯誤碼、`error_message` 是一句人看得懂的說明。錯誤碼表在 [AGENTS.md](AGENTS.md)。
- job 記錄寫在 `~/.suno-web/jobs.db`，服務重啟後查舊 job 仍拿得到記錄與已經存下來的音檔。記錄只留最新 1000 筆，建立新 job 時自動裁切；音檔則依 `AUDIO_RETENTION_DAYS` 分開清。

### GET /api/jobs/{job_id}/files/{name}

直接吐 mp3 或封面圖，回應不塞 base64。`job_id` 必須是 12 位十六進位字元、`name` 只允許英數與 `.`、`_`、`-`，不符合一律 404。

### GET /api/health

```bash
curl -s http://localhost:8071/api/health
```

```json
{"status": "ok", "queue_size": 0, "uptime_seconds": 7.6,
 "browser_alive": true, "logged_in": null, "credits": null}
```

`logged_in` 與 `credits` 是惰性觀測值：服務剛起來時兩個都是 `null`，要等第一個 job 真的導覽過 Create 頁面、側錄到帳單 API 之後才有值。剛啟動就讀到 `null` 是預期行為。`credits` 對免費帳號算的是 `monthly_limit - monthly_usage`，也就是這個月還剩幾點。

## 管理台

`/admin`：四頁。

- **總覽**：剩餘點數（直接換算成還能生幾單）、現在在跑什麼、最近完成的幾單附播放器、瀏覽器與登入狀態。有 job 在跑時整頁每 15 秒自動更新
- **金鑰**：現場發、停用、刪除，看每把用過幾次與最後使用時間。金鑰只存 sha256 雜湊，原文只在剛發出來那一次顯示
- **測試**：用指定金鑰送一單，頁面每 5 秒自動更新，跑完當場播。走的是跟真實呼叫端完全一樣的參數處理與佇列，所以不會出現「管理台測得過、API 卻不行」的假象。一次扣 10 點
- **歷史**：近 200 筆 job，含來源金鑰、送出的內容、錯誤碼、耗時，音檔可以直接在頁面上播；底下有一顆按鈕可以立刻清掉過期音檔

版面結構跟 [gemini-web](https://github.com/yazelin/gemini-web) 與 codex-image-service 同一家（頂部列、左側邊欄、頁面標題配說明句、卡片），配色刻意不同：深色底配珊瑚橘與洋紅，免得兩個管理台搞混。

`.env` 的 `API_KEYS` 仍然有效，在管理台是唯讀顯示（要改就改 `.env` 再重啟）。只要靜態或動態任一邊有金鑰，`/api/generate` 與 `/api/jobs/*` 就強制驗證。

## 維運腳本

```bash
python3 scripts/checkup.py           # 唯讀健檢：服務、近 24 小時成功率、失敗明細、音檔佔用
python3 scripts/canary.py --dry-run  # 金絲雀：只檢查、不開 issue
python3 scripts/canary.py            # 壞了就開一張帶 canary label 的 GitHub issue
```

金絲雀刻意**不生成**：一單扣 10 點、一個月只有 100 點，每天真生一單根本跑不起來。改成檢查送得出去之前的每個前提：登入態、五個關鍵 selector、點數夠不夠、有沒有被要求驗證碼。這四項正是實際壞過的地方。

排程（每天早上八點）：

```
0 8 * * * cd ~/suno-web && .venv/bin/python scripts/canary.py >> ~/suno-web/canary.log 2>&1
```

## AI Agent 整合

`suno-web install` 會偵測 Claude Code 與 Gemini CLI，把 slash command 裝進 `~/.claude/commands/suno-web/` 與 `~/.gemini/commands/suno-web/`：

```
/suno 幫我做一段寫程式時聽的背景音樂
```

指令會把敘述擴寫成英文音樂 prompt、送單、等它跑完、把 mp3 存下來，並在生成前提醒還剩幾單額度。

## 環境變數

放在 repo 根目錄的 `.env`，範本見 `.env.example`。

| 變數 | 說明 | 預設 |
|---|---|---|
| `HEADLESS` | 無頭模式。部署時設 `true` | `false` |
| `PROFILE_DIR` | 登入態的瀏覽器 profile 目錄 | `~/.suno-web/profiles` |
| `SUNO_URL` | Suno 生成頁網址 | `https://suno.com/create` |
| `CHROME_BINARY` | 真 Chrome 的執行檔，找不到就給明確錯誤 | `google-chrome` |
| `CHROME_NO_SANDBOX` | 關掉 Chrome 沙箱。只有沙箱起不來的機器才需要（例如把 deb 解到家目錄、`chrome-sandbox` 沒有 root 的 setuid 位元） | `false` |
| `HOST` | 監聽位址 | `0.0.0.0` |
| `PORT` | 服務埠 | `8071` |
| `QUEUE_MAX_SIZE` | 最大排隊單數，滿了回 429 | `10` |
| `DEFAULT_TIMEOUT` | 單筆 job 的秒數上限 | `600` |
| `API_KEYS` | API 金鑰，逗號分隔多把；沒設＝開放 | 無 |
| `GENERATED_DIR` | 音檔落地目錄 | `~/.suno-web/generated` |
| `WORKER_COUNT` | 幾個 Suno 帳號。每個帳號一個 profile 目錄（`profiles`、`profiles-1`…），派工輪流攤平配額 | `1` |
| `DISPATCH_MODE` | 派工模式。`credits`＝挑剩餘點數最多的帳號（預設）；`round-robin`＝單純輪流 | `credits` |
| `IDLE_SHUTDOWN_MINUTES` | 瀏覽器閒置這麼久就關掉省記憶體，`0` 表示永不關 | `10` |
| `AUDIO_RETENTION_DAYS` | 音檔保留天數，超過的在下次生成時順手清掉，也可以在管理台的歷史頁按鈕手動清 | `14` |
| `ADMIN_USERNAME` | 管理台帳號 | `admin` |
| `ADMIN_PASSWORD` | 管理台密碼，**對外開放前一定要改** | `change-me` |
| `ADMIN_SESSION_SECRET` | 管理台 cookie 的簽章密鑰，**上線前一定要改** | `dev-only-session-secret` |
| `ADMIN_URL_PREFIX` | 反代到子路徑時的前綴，例如 `/suno-web` | 空 |
| `ADMIN_DB_PATH` | 動態金鑰的資料庫 | `~/.suno-web/admin.db` |

job 記錄固定寫 `~/.suno-web/jobs.db`，這個位置不吃環境變數。

## 部署

目前跑在 192.168.11.11:8071（Chrome 解在該機家目錄、`CHROME_NO_SANDBOX=true`、開機用使用者 crontab 的 `@reboot` 拉起來）。部署與跨機器驗收的細節見 `docs/acceptance-2026-08-20.md` 第六節。

以下是用 systemd 正式化的方式（需要該機的 sudo 密碼）。

```bash
sudo bash scripts/install-service.sh
```

腳本會寫出 `/etc/systemd/system/suno-web-api.service`，然後 `daemon-reload`、`enable`、`restart`。unit 的幾個重點：

- `ExecStart` 指向 repo 內的 `.venv/bin/suno-web serve`，所以同一台機器要先跑過 `uv sync`。
- `ExecStartPre` 先 `pkill` 掉其他佔用同一份 profile 的 process，避免兩個瀏覽器搶同一個 session。
- `EnvironmentFile` 讀 repo 根目錄的 `.env`。部署時 `HEADLESS` 由 `ExecStart` 強制為 `true`，`.env` 設了也不會蓋掉。
- `PLAYWRIGHT_BROWSERS_PATH` 指到該使用者的 `~/.cache/ms-playwright`。

部署前先在那台機器上跑過 `suno-web login`，profile 才有登入態，這一步需要桌面環境或 X forwarding。看 log：

```bash
sudo journalctl -u suno-web-api -f
```

V1 沒有瀏覽器自動自癒：建議外部監控定期打 `/api/health`，看到 `browser_alive: false` 就 `systemctl restart suno-web-api`。

## 帳號前提

服務綁一個免費 Suno 帳號，以下是 2026-08-20 實測的狀況：

- 模型固定用頁面預設的 v4.5-all。程式不碰模型下拉選單，其他模型要 Pro。
- 一單生成出 2 首 clip，扣 10 點。
- 免費方案是月配額制（實測帳號 100 點／月，等於一個月 10 單）。
- 點數用完之後 Create 按鈕照樣按得下去，但 Suno 後端不會真的排入生成，job 會以 `submit_failed` 收場，`error_message` 是「按了 Create 但 feed 沒出現新 clip」。要判斷是不是這個原因，看 `/api/health` 的 `credits`。
- 一單實測出 4 首：2 首完整長度，另外 2 首是 v5.5 preview，長度固定 60 秒、畫面上掛「Upgrade for full song」。preview 一樣下載得到，買的是完整長度而不是下載權。真的抓不到音檔的 clip 才會標 `downloadable: false`。
- wav 下載要 Pro，本服務只處理 mp3。

## 已知限制

- **一定要用真的 Google Chrome，不能用 Playwright 內建的 Chromium。** Suno 在按下 Create 時會先打 `POST /api/c/check` 問要不要驗證碼。用 Playwright 內建 Chromium 時它回 `{"required": true}` 並跳出 Cloudflare Turnstile 的互動式勾選框，程式化點擊不被接受，生成請求送不出去；改用真 Chrome（本服務自己啟動、再用 CDP 接上）之後同一個端點回 `{"required": false}`，生成正常送出。`channel="chrome"` 讓 Playwright 去啟動也不行，必須自己起、自己接。實測記錄見 `docs/acceptance-2026-08-20.md` 第四、五節。
- **自動化 Suno 網頁違反 Suno 服務條款，帳號有被封的風險。** 這是明講的取捨，要不要用請自己評估。
- 派工預設「點數優先」：挑剩餘點數最多的帳號。帳號的月配額常常不一樣（實測 40／100／300／300），單純輪流會讓點數少的先見底、變成失敗來源。點數要跑過一單才觀測得到，還沒有數字的帳號用輪流去發掘；點數一樣多的帳號之間也輪流。
- 併行度等於 `WORKER_COUNT`。**同一個帳號一次只跑一單**：一個 worker 只有一個瀏覽器分頁，而輪詢期間會定期 reload 它，兩單並行會互相把頁面導覽掉。不同帳號之間則是真的並行。
- 瀏覽器隨用隨開：派工到某個帳號才啟動它的 Chrome，閒置 `IDLE_SHUTDOWN_MINUTES` 後關掉。每單多約 10 到 15 秒的啟動時間，換掉常駐四個 Chrome 的近 5 GB 記憶體。
- 一單通常 2 到 4 分鐘，job timeout 預設 600 秒。
- Suno 改版會斷掉寫入流程。DOM selector 與 feed URL pattern 全部集中在 `src/selectors.py`，改版時只修那一檔。
- `_wait_terminal` 每 20 秒主動 reload 一次頁面：Suno 的 `streaming` 轉 `complete` 走的即時管道（推測是 WebSocket 或 SSE）側錄不到，純被動等會永遠等不到終態。這是實機踩出來的 workaround，reload 頻率改動前先看 `src/suno.py` 的註解。
- 登入態過期要人工重跑 `suno-web login`，需要桌面環境。
- V1 不做：admin webui、動態金鑰、History 頁、多帳號 worker pool。
- 尚未驗證的部分記在 `docs/acceptance-2026-08-20.md`：經 API 或 CLI 走完整條 happy path、Custom 與 instrumental 兩條分支的真實生成、VIP 鎖 clip 的實例，都因為帳號當月點數已經用完而延後到下個月配額重置時補。

## 開發

```bash
uv sync --extra dev
uv run pytest -q
```

79 個測試，另有 1 個標了 `browser` 的測試預設跳過（那個要真的開 Chromium）。瀏覽器層在單元測試裡用假 worker 注入，不碰網路。

## 授權

MIT License，見 `LICENSE`。
