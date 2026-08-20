# suno-web 設計文件

日期：2026-08-20
狀態：已與 yazelin 逐節確認定案

## 這是什麼

用 Playwright 自動化 Suno 網頁版（suno.com/create）的音樂生成服務，架構比照 gemini-web：CLI ＋ HTTP API、profile 持久登入、部署在 192.168.11.11 的 systemd 服務。走的是登入帳號的網頁額度，沒有官方 API、不按次計費。

帳號現況（設計前提）：免費帳號、非 Pro。模型固定用頁面預設的 v4.5-all（其他模型要 Pro）；一次生成出多首 clip，其中通常兩首可下載，有時另有兩首是 VIP 才能下載。

## 已拍板的決定

| 決定 | 內容 |
|---|---|
| 生成模式 | Simple（一句描述）與 Custom（歌詞＋曲風＋歌名）都支援，另有 instrumental 開關 |
| API 形態 | Job 式：送單立刻回 job_id，輪詢拿結果。生成要 2–4 分鐘，同步等太脆 |
| V1 範圍 | 核心先行：login ＋ 單 worker ＋ job API ＋ CLI ＋ .env 金鑰 ＋ systemd。admin webui、動態金鑰、History、canary、多帳號 worker pool 都不做，用起來有需要再長 |
| 自動化路徑 | 混合：寫入走 UI（真瀏覽器填表單按 Create，自然過 Turnstile）、讀取走網路側錄（攔頁面自己輪詢的 clip feed JSON） |
| model 參數 | 不做。固定用頁面預設 v4.5-all，不碰下拉選單 |
| VIP clip | job 結果列出本單全部 clip；抓得到音檔的存檔標 `downloadable: true`，VIP 鎖的只留 metadata 標 `false`。至少下載到一首即算成功 |
| UI 配色 | 任何介面（含未來 admin）用深色底＋珊瑚橘／洋紅 accent，刻意避開 gemini-web 的淺色靛藍，一眼分得出誰是誰 |
| 部署 | .11、port 8071、HEADLESS=true、channel=chromium（gemini-web 在 .11 的教訓：headless_shell 會被擋） |

## Repo 佈局

`~/suno-web`，GitHub public，MIT（林亞澤）。檔名沿用 gemini-web 的命名習慣，維護時可以憑肌肉記憶找檔案：

```
src/
  main.py        # FastAPI app：/api/generate、/api/jobs、/api/health、檔案端點
  browser.py     # Playwright persistent context 管理（啟動、重啟、死活判斷）
  suno.py        # 頁面流程：切模式、填欄位、按 Create、對 feed 側錄結果
  selectors.py   # DOM selector 與 feed URL pattern 全部集中在這一檔，Suno 改版只修這裡
  jobs.py        # asyncio 佇列 + SQLite job store
  cli.py         # login / serve / generate / health / install
  config.py      # 環境變數
  security.py    # API_KEYS 驗證
scripts/
  install-service.sh   # systemd 部署（比照 gemini-web）
tests/
docs/superpowers/specs/
```

資料落點：登入 profile 在 `~/.suno-web/profiles/`，音檔在 `~/.suno-web/generated/<job_id>/`，job 記錄在 `~/.suno-web/jobs.db`。

## API

金鑰語意照 gemini-web：`.env` 的 `API_KEYS`（逗號分隔）設了任何一把，全部 `/api/*` 就要帶 header `x-api-key`，沒帶回 403；完全沒設則維持開放（本機開發）。

### POST /api/generate

| 欄位 | 說明 |
|---|---|
| `prompt` | Simple 模式：一句描述 |
| `lyrics`, `style`, `title` | Custom 模式：有 `lyrics` 或 `style` 就走 Custom（此時忽略 `prompt`）。`prompt` 與 `lyrics`/`style` 全空回 400 |
| `instrumental` | 選填，bool，預設 false |
| `timeout` | 選填，秒，預設 600 |

立刻回 `{"job_id": "...", "status": "queued"}`。佇列滿（`QUEUE_MAX_SIZE=10`）回 429 `queue_full`。

### GET /api/jobs/{id}

```json
{
  "job_id": "...",
  "status": "queued | generating | done | error",
  "clips": [
    {"id": "...", "title": "...", "duration": 123.4,
     "downloadable": true, "audio_url": "/api/jobs/{id}/files/xxx.mp3",
     "image_url": "/api/jobs/{id}/files/xxx.jpeg"}
  ],
  "error": null,
  "elapsed_seconds": 187.2
}
```

clips 列出本單新增的全部 clip；VIP 鎖住的 `downloadable: false` 且沒有 `audio_url`。

### GET /api/jobs/{id}/files/{name}

直接吐 mp3 或封面圖。API 回應一律不塞 base64——音檔太大。

### GET /api/health

`{"status", "browser_alive", "logged_in", "queue_size", "uptime_seconds"}`；點數餘額若能從側錄或頁面便宜拿到就加 `credits` 欄位，拿不到不硬做。

## 瀏覽器層（混合路徑）

單一 Playwright persistent context，channel=chromium。

寫入走 UI：`suno.py` 依 `selectors.py` 切 Simple/Custom、填 prompt 或歌詞／曲風／歌名、切 instrumental、按 Create。真瀏覽器操作自然通過 Cloudflare Turnstile——這正是純內部 API 專案（gcui-art/suno-api）被擋死的地方。

讀取走側錄：`page.on("response")` 攔頁面自己輪詢的 clip feed JSON（URL pattern 也集中在 `selectors.py`）。按 Create 後新出現的 clip id 即為本單的 clip；追蹤狀態直到 `complete` 或 error。音檔用 feed 給的 URL 在瀏覽器 context 內帶 cookie 下載，不點 UI 的下載選單。DOM 改版只影響寫入那一半。

## 佇列、儲存、錯誤

單 worker、asyncio queue，一次一單。job 狀態機：`queued → generating → done | error`。

job 記錄寫 SQLite（stdlib sqlite3，一張表：id、狀態、參數 JSON、clips JSON、error、時間戳）。服務重啟後查舊 job 拿得到記錄與已落地的音檔，不會 404。

錯誤碼：`queue_full`、`not_logged_in`、`submit_failed`（Create 按不下去或出錯誤提示）、`generation_timeout`、`clip_error`（Suno 自己把 clip 標 error）、`download_failed`（一首都沒下載到）、`browser_error`。

音檔保留 `AUDIO_RETENTION_DAYS`（預設 14）天，生成時順手清過期目錄。

## CLI

```bash
suno-web install      # 裝 Chromium（Playwright）
suno-web login        # 開有頭瀏覽器，人工登入 Suno，登入態存 profiles/
suno-web serve        # 起 HTTP API（預設 0.0.0.0:8071）
suno-web health       # 打 /api/health
suno-web generate "a cheerful ukulele tune" -o out/          # Simple
suno-web generate --lyrics-file lyrics.txt --style "lo-fi hip hop" \
                  --title "深夜寫程式" --instrumental -o out/  # Custom
```

`generate` 內部送單＋輪詢＋把可下載的 clip 全部存到 `-o` 目錄。打包用 uv tool（`uv tool install suno-web && suno-web install`），不用 pip。

## 環境變數

| 變數 | 預設 |
|---|---|
| `PORT` | `8071` |
| `HEADLESS` | `false`（serve 部署時設 `true`） |
| `PROFILE_DIR` | `~/.suno-web/profiles` |
| `SUNO_URL` | `https://suno.com/create` |
| `DEFAULT_TIMEOUT` | `600` |
| `QUEUE_MAX_SIZE` | `10` |
| `API_KEYS` | 無（沒設＝開放） |
| `GENERATED_DIR` | `~/.suno-web/generated` |
| `AUDIO_RETENTION_DAYS` | `14` |

## 測試與驗收

單元測試（pytest）蓋 job store、狀態機、API 端點與金鑰驗證——瀏覽器層以假 worker 注入，不碰網路。

真驗收（部署 .11 之前，本機跑）：
1. `suno-web login` 人工登入一次
2. 真生一單 Simple、一單 Custom＋instrumental
3. ffprobe 驗每首下載到的 mp3 時長 > 0
4. 驗 VIP 鎖住的 clip 在 job 結果裡是 `downloadable: false` 而非整單 error
5. 過了才跑 `scripts/install-service.sh` 部署 .11

## 風險與明講的限制

- 自動化 Suno 網頁違反其服務條款，帳號有被封風險（README 比照 gemini-web 明講）
- Suno 改版會斷寫入流程；selector 集中一檔降低修復成本，側錄那半較耐改版
- 免費帳號每日點數有限，用完 job 會失敗——錯誤訊息要能看出是點數用完
- 併行度＝1（單帳號單 worker），其餘排隊

## V1 不做（將來有需要再長）

admin webui、動態金鑰、History 頁、canary、多帳號 worker pool、官方 API fallback（Suno 沒有官方 API 可退）、wav 下載（要 Pro）。
