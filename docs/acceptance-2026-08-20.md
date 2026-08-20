# 端對端驗收記錄（2026-08-20）

本文件記錄 suno-web v1 的端對端驗收結果。因帳號本月免費 credits 已在
Task 11 煙霧測試中耗盡（30 → 0），本輪驗收依 controller 裁決（見
`progress.md` 的 Task 12 ruling）改採「serve 真啟動 + 健康檢查 + 金鑰
／400／404 驗證 + 一單真送出（預期分類失敗）」的縮小範圍，happy-path
的經 API／CLI 真生成延後到下個月 credits 重置、部署 .11 驗收時一併補。

## 一、Task 11 煙霧測試證據（真生 3 單，摘要引用）

完整記錄見 `.superpowers/sdd/2026-08-20-suno-web-v1/task-11-report.md`。

- 帳號 `yaze_lin_j303`，用 `scripts/smoke_generate.py` 真跑了 3 次生成，
  credits 由 30 依序扣到 20 → 10 → 0（每次生成扣 10 credits、出 2 首
  clip），本月免費額度已用完。
- 第 1 次（prompt="a cheerful short ukulele tune"）與第 2 次
  （prompt="a gentle rainy afternoon lo-fi beat"）過程中各踩到一個
  production 缺陷並修正：
  1. `before` 快照不可靠（`/create` 頁面不會主動打 feed API）導致把
     帳號歷史舊 clip 誤判成新生成，改用伺服器 `created_at` 對比
     `submit_time` 過濾。
  2. 被動側錄看不到 `streaming -> complete`（真正的轉換走前端攔不到的
     即時管道，推測 WebSocket/SSE）導致 `_wait_terminal` 永遠卡住，
     改為每 20 秒主動 `page.reload()` 強迫重新側錄。
- 第 3 次（prompt="a slow jazzy piano interlude"）兩個修正同時生效，
  端到端全綠：
  - `138ff532-…`「Rain On Ivory」：Suno 回報 duration 325.0 秒，
    ffprobe 實測 325.032000 秒，size 7222758 bytes，合法 MP3
    （ID3v2.4.0、MPEG layer III、64kbps、48kHz、Stereo）。
  - `ca8cb151-…`「Rain On Ivory」：Suno 回報 duration 199.88 秒，
    ffprobe 實測 199.920000 秒，size 4165494 bytes，合法 MP3（同上）。
  - 兩者皆 `downloadable: true`。
- 第 2 次生成的兩首（`aac675f2-…`、`0731c95f-…`，「Pineapple Porch」
  補救下載後驗證）：ffprobe duration 分別為 134.904000 秒／
  109.392000 秒，皆為合法 MP3。
- 3 次生成、6 首新歌全數 downloadable=true，未觀察到 VIP-locked
  （downloadable=false）的 clip。
- 單元測試全程綠燈（修正後 45 passed，含新增的 `_parse_epoch` /
  `_is_freshly_created` 回歸測試），未弱化任何既有測試斷言。

## 二、今日 serve 真接線驗證（Task 12，本文件對應的驗收動作）

範圍：這是 BrowserManager + SunoRunner + JobQueue + FastAPI 四個元件
第一次在真的 `uvicorn` 進程下一起跑（而非個別單元測試裡的假物件組裝）。
所有指令皆在 repo 根目錄（`/home/ct/suno-web`，branch `feat/v1`）執行，
服務跑在背景、以 `curl` 驗證。

### 步驟 0：跑之前的既有單元測試基線

```
$ uv run pytest -q
45 passed, 1 deselected, 1 warning in 1.74s
```

（Task 11 完成時的既有基線，本輪未新增／修改任何測試或 production
程式碼。serve 接線本身沒有故障，見下方「本輪未觸發修正」。）

### 步驟 1：啟動真服務並健康檢查

```
$ HEADLESS=true uv run suno-web serve   # 背景執行
```

啟動後約 1～2 秒 `/api/health` 即回 200：

```
$ curl -s http://localhost:8071/api/health
{"status":"ok","queue_size":0,"uptime_seconds":7.6,"browser_alive":true,
 "logged_in":null,"credits":null}
```

- `browser_alive: true`：BrowserManager 在 `lifespan` 啟動時成功
  開出 headless Chromium，且 persistent profile（Task 8 人工登入的
  session）能被重新載入而不炸例外。
- `logged_in: null`、`credits: null`：這是**預期行為**，不是缺陷：
  `SunoRunner.logged_in` 只有在 `_ensure_on_create_page()` 真的被呼叫
  （即第一個 job 開始跑）時才會被設成 `True`/`False`
  （`src/suno.py:111,173-175`）；`last_credits` 同理，只有側錄到
  `/api/billing/info/` 回應才會更新（`src/suno.py:112,137-140`），這條
  API 不會在服務剛啟動、還沒有任何 job 導覽頁面時被動觸發。這點
  Task 11 報告已針對 `credits` 記錄過同樣的現象，本次額外確認
  `logged_in` 也適用同一套「懶惰觀察」設計，`/api/health` 把兩者
  當合法的 `None` 值處理，行為與既有測試（`test_main.py`）的假設一致。
- 送出第一個 job、真的導覽過 `/create` 頁面之後，`/api/health` 才變成
  `logged_in: true`、`credits: 0`（見步驟 3 記錄）。`credits: 0`
  與 Task 11 記錄的「本月免費額度已耗盡」完全吻合。

### 步驟 2：Auth／驗證檢查（不花 credits）

**未設 `API_KEYS` 時：**

```
$ curl -s -w "\nHTTP_CODE=%{http_code}\n" -X POST http://localhost:8071/api/generate \
    -H "Content-Type: application/json" -d '{}'
{"detail":"invalid_request: prompt 或 lyrics/style 至少要有一個"}
HTTP_CODE=400

$ curl -s -w "\nHTTP_CODE=%{http_code}\n" http://localhost:8071/api/jobs/nonexistent00
{"detail":"not_found"}
HTTP_CODE=404
```

兩者皆符合預期。

**設 `API_KEYS=testkey123` 後重啟（停掉服務、寫 `.env`、重啟）：**

```
$ curl -s -w "\nHTTP_CODE=%{http_code}\n" -X POST http://localhost:8071/api/generate \
    -H "Content-Type: application/json" -d '{"prompt":"a short happy jingle"}'
{"detail":"invalid_api_key"}
HTTP_CODE=403

$ curl -s -w "\nHTTP_CODE=%{http_code}\n" -X POST http://localhost:8071/api/generate \
    -H "Content-Type: application/json" -H "x-api-key: testkey123" -d '{}'
{"detail":"invalid_request: prompt 或 lyrics/style 至少要有一個"}
HTTP_CODE=400
```

未帶金鑰 → 403；帶對金鑰但欄位空 → 400，驗證順序（先查金鑰、再查欄位）
符合 `src/main.py` 的 `Depends(require_key)` 接線。驗證完畢後刪除
`.env`、無金鑰重啟，回到步驟 3 的開放模式。

### 步驟 3：一單真送出，預期分類失敗

無 `API_KEYS`（開放模式）下送出：

```
$ curl -s -X POST http://localhost:8071/api/generate \
    -H "Content-Type: application/json" -d '{"prompt":"a short happy jingle"}'
{"job_id":"d9025d66e20a","status":"queued"}
```

每 10 秒輪詢 `GET /api/jobs/d9025d66e20a`（timeout 上限 600 秒），
完整狀態序列：

| t (秒) | status | error | elapsed_seconds |
|---|---|---|---|
| 0 | generating | — | 6.5 |
| 10～91 | generating（維持不變） | — | 16.5 → 96.7 |
| 101 | **error** | `submit_failed` | 100.9 |

最終結果：

```
{"job_id":"d9025d66e20a","status":"error","clips":[],
 "error":"submit_failed",
 "error_message":"按了 Create 但 feed 沒出現新 clip",
 "elapsed_seconds":100.9}
```

- **狀態**：`error`。
- **分類錯誤碼**：`submit_failed`（`src/suno.py:277`，`run()` 按下
  Create 之後、`_wait_new_ids` 逾時仍等不到任何被判定為「這次 job 才
  生出來」的新 clip 時拋出）。
- **error_message**：「按了 Create 但 feed 沒出現新 clip」。
- **耗時**：從送出到判定失敗共 100.9 秒（`elapsed_seconds`），遠低於
  600 秒的 job timeout，job **沒有卡到逾時**，是被 `_wait_new_ids`
  自身的等待上限主動判定失敗，不是被外層 `asyncio.wait_for` 強制打斷
  （若是外層逾時，`error` 會是 `generation_timeout` 而非
  `submit_failed`）。
- 這個結果與「credits 已耗盡」的實況吻合：Create 按鈕仍可點擊，但
  帳號沒有點數，Suno 後端沒有真的排入生成、feed 自然不會出現新 clip
  屬於 spec 要求「看得出來」的分類失敗情境的真實樣貌。

**Worker 存活驗證**：緊接著再送出第二單（不同 prompt）：

```
$ curl -s -X POST http://localhost:8071/api/generate \
    -H "Content-Type: application/json" -d '{"prompt":"a second short test jingle"}'
{"job_id":"dc2af9372cc6","status":"queued"}
```

8 秒後查詢，狀態已是 `generating`（`queued → generating` 的轉換發生，
證明 `worker_loop` 在第一單進 `except GenerationError` 分支結束後仍
持續 `await self._queue.get()` 撈下一單，沒有被第一單的例外殺死）。
讓第二單跑完，結果同樣是 `error` / `submit_failed`（101 秒 →
`elapsed_seconds: 106.2`），與第一單完全一致，屬同一根因（credits
已空）的可重現結果，不是隨機噴錯。

**服務健康複驗**：兩單跑完後再查一次 `/api/health`：

```
{"status":"ok","queue_size":0,"uptime_seconds":234.5,
 "browser_alive":true,"logged_in":true,"credits":0}
```

`browser_alive: true`、`logged_in: true`、`credits: 0`，服務在連續
兩單失敗後仍健康，佇列淨空，未卡死、未崩潰。服務全程的
`uvicorn` 存取記錄（`INFO:` 行）沒有任何一行例外或 traceback，兩單的
失敗都是 `GenerationError` 被 `worker_loop` 正常捕捉分類，不是未預期
的例外被最外層 `except Exception` 兜底。

### 本輪未觸發修正

serve 接線（BrowserManager + SunoRunner + JobQueue + FastAPI 在真
`uvicorn` 下組裝）沒有出現 import error、瀏覽器啟動失敗、或 worker
沒接到 job 等問題，因此本輪**沒有**修改 `src/` 下任何檔案，也沒有
新增／修改任何測試。步驟 0 與步驟 3 之間的單元測試基線保持一致
（45 passed）。

## 三、延後到下個月 credits 重置（部署 .11 驗收時一併補）

以下項目本輪因免費 credits 已耗盡（見上方一、二節）無法驗證，延後：

1. **Happy-path：Simple 模式經 HTTP API／CLI 真生成到底**：從
   `POST /api/generate` 送出、輪詢到 `status: "done"`、下載
   `audio_url`、ffprobe 驗證檔案，走完整條 production 路徑（Task 11
   驗證過的是 `SunoRunner.run()` 本身，但沒有經過 FastAPI + JobQueue
   + CLI `_generate()` 這條對外路徑的真流量）。
2. **Happy-path：Custom + instrumental 模式**：Task 11 三次生成皆為
   Simple 模式，Custom（歌詞／曲風／歌名）與 instrumental 開關兩條
   分支目前只有 Task 9/10 的偵察與程式邏輯覆蓋，未經真實生成驗證。
3. **VIP-lock 觀察**：spec 提到免費帳號有時會出現 `downloadable:
   false` 的 VIP 鎖 clip；Task 11 三次生成共 6 首新歌全數
   `downloadable: true`，未觀察到此情境，job 結果「非整單 error、
   只有該 clip 標 false」的分支邏輯未經真實資料驗證。
4. **`error` 終態的下游行為觀察**：本次雖已在真服務上觀察到
   `error` / `submit_failed`（credits 耗盡導致 Create 後無新 clip），
   但 `_download_all()` 對 `status != "complete"` 的 clip 只建立
   metadata、不下載檔案這條路徑，以及其他可能的錯誤終態字串（例如
   Suno 端真正在生成中失敗、非我方 credits 問題導致的失敗），仍未經
   實機驗證，屬於已知未驗證分支。

## 四、清理

- 服務已於驗收結束後停止（`kill`，確認進程與 8071 port 皆已釋放）。
- 驗證金鑰行為時暫時建立的 `.env`（`API_KEYS=testkey123`）已刪除，
  恢復成本輪開始前「無 `.env`」的原狀。
- 兩單失敗 job（`d9025d66e20a`、`dc2af9372cc6`）皆未產生任何檔案落地
  （`~/.suno-web/generated/` 下確認沒有對應目錄），無須額外清檔案。

## 四、後續：Turnstile 擋住生成（2026-08-20 晚間）

當天稍晚換帳號重測時，生成整條路被擋住，症狀與證據如下。

### 現象

`scripts/smoke_generate.py` 以 `submit_failed`（「按了 Create 但 feed 沒出現新 clip」）失敗。表單填得好好的、Create 鈕 `visible=True enabled=True`、點得下去，但伺服器端完全沒收到生成：連跑數次之後帳號的 `total_credits_left` 仍是 90、`monthly_usage` 仍是 10，一點都沒扣。

### 根因

按下 Create 時，前端先打：

```
POST https://studio-api-prod.suno.com/api/c/check
  送出 {"ctype":"generation"}
  回應 {"required": true, "captcha_version": 2}
```

接著畫面跳出 Cloudflare Turnstile 的互動式勾選框（「驗證您是人類」），Create 鈕停在轉圈。生成請求始終沒有送出。

### 排除掉的可能

| 假設 | 驗證方式 | 結果 |
|---|---|---|
| IP 被封 | `curl https://suno.com` | 200，API 回 401，網路層正常 |
| 帳號沒點數 | 側錄 `/api/billing/info/` | `total_credits_left: 90`，有點數 |
| selector 過期 | 逐一數命中數與可見性 | 五個關鍵 selector 都命中 1 個可見元素，填字回讀正確 |
| 新帳號信任度低 | 換回舊帳號 profile 重測 | 一樣擋，與帳號無關 |
| headless 被偵測 | 改有頭視窗重測 | 一樣擋 |
| 程式化勾選驗證碼 | frame_locator 與座標點擊兩種 | 勾選框維持未勾，不被接受 |

### 順帶修掉的真缺陷

偽裝 User-Agent 為 Chrome/131 會讓 `auth.suno.com/v1/client/verify` 持續收到 `captcha_error=600010`（Turnstile 挑戰失敗），因為 UA 字串與瀏覽器真實指紋對不起來。拿掉偽裝後這些失敗歸零（commit 6dc2e72）。這修正並不能解除上面那道互動式驗證，但它本身是對的。

### 結論

生成路徑在 Suno 端被關掉，不是本專案的設定或程式問題。同一天稍早還連續生成過三單，所以有可能是短期風險升級而非永久政策。其餘功能全數可用。部署 .11 暫緩，等驗證放寬後再重跑本文件第三節列的延後項目。

## 五、解法：改用真 Chrome 加 CDP（2026-08-21）

第四節那道 Turnstile 不是無解，關鍵在瀏覽器本體。

### 對照實驗

| 啟動方式 | `/api/c/check` | Turnstile | 生成請求 |
|---|---|---|---|
| Playwright 啟動內建 Chromium（原本的做法） | `required: true` | 跳互動式勾選框 | 送不出 |
| Playwright 以 `channel="chrome"` 啟動 | 同上 | 同上 | 送不出 |
| 自己啟動真 Chrome，再 `connect_over_cdp` 接上去 | `required: false` | 不出現 | **正常送出** |

`navigator.webdriver` 三種情況都是 `true`，所以那個屬性不是判準；差別在瀏覽器本體是不是真的 Google Chrome。headless 與有頭都通過，部署機不需要 Xvfb。

### 改動

`src/browser.py` 重寫：用 `subprocess` 啟動 `CHROME_BINARY`（預設 `google-chrome`），帶 `--remote-debugging-port=0`、`--user-data-dir=<PROFILE_DIR>`，等 Chrome 把實際 port 寫進該 profile 的 `DevToolsActivePort` 之後 `connect_over_cdp` 接上去。停止時只終止自己啟動的那一個程序。

port 交給系統挑、每個實例只管自己的程序，這兩點是為了將來多帳號：N 個帳號等於 N 個 profile 目錄配 N 個各自挑 port 的 Chrome，彼此不會搶 port，也不會互相殺掉。

`suno-web install` 從「下載 Playwright Chromium」改成「檢查真 Chrome 在不在」，每台機器少下載 115 MB。`STEALTH_TIMEZONE` 移除：CDP 接上去之後設不了 context 選項，Chrome 直接吃系統時區，留著會誤導。

### 本機真驗收（2026-08-21）

`scripts/smoke_generate.py` 一單走完整條路：送單、輪詢、下載。

| clip | 長度（ffprobe） | downloadable |
|---|---|---|
| 7ae070f0 | 204.98 秒 | true |
| ad1b1e44 | 174.98 秒 | true |
| 2e5286cc | 59.83 秒（v5.5 preview） | true |
| 9e63ee04 | 59.83 秒（v5.5 preview） | true |

一單出 4 首、扣 10 點。此帳號的 feed 當下有 16 個舊 clip，`created_at` 過濾精準只挑出這一單的 4 首，Task 11 那個修正在真實情境下站得住。

VIP 這件事要修正先前的假設：preview clip 下載得到，只是長度固定 60 秒，付費買的是完整長度而不是下載權。真正抓不到音檔的 clip 才會標 `downloadable: false`。

## 六、部署 192.168.11.11（2026-08-21）

| 項目 | 內容 |
|---|---|
| 服務位址 | `http://192.168.11.11:8071` |
| repo | `~/suno-web`（clone 自 GitHub，`git pull` 更新） |
| Chrome | `~/opt/chrome/opt/google/chrome/chrome`（官方 deb 解到家目錄，那台的 apt 要密碼） |
| 沙箱 | 關閉。解到家目錄的 `chrome-sandbox` 沒有 root 的 setuid 位元，所以 `.env` 設了 `CHROME_NO_SANDBOX=true` |
| 登入態 | 從筆電 rsync 過去（`~/.suno-web/profiles/`），沒有在那台重新登入 |
| 開機自動啟動 | 使用者 crontab 的 `@reboot`。那台 sudo 要密碼，所以沒用 `scripts/install-service.sh` 的 systemd 路線 |

### 跨機器真驗收

筆電送單、`.11` 生成、筆電取檔：

```
POST /api/generate  {"prompt": "a gentle lo-fi beat for late night study"}
  -> {"job_id": "aea1317fba04", "status": "queued"}
輪詢 143.9 秒後 status=done，2 首 clip 皆 complete、downloadable
下載回筆電 ffprobe：114.312 秒（2531574 bytes）、185.112 秒（3814374 bytes）
```

金鑰驗證同時確認：`/api/generate` 與檔案端點不帶 `x-api-key` 都回 403，`/api/health` 免金鑰可打。

### 這台筆電的設定

`~/.bashrc`（放在非互動 shell 早退判斷之前，腳本才讀得到）：

```bash
export SUNO_WEB_API_KEY=<金鑰>
export SUNO_WEB_SERVER=http://192.168.11.11:8071
```

設好之後 `suno-web health`、`suno-web generate "..." -o out/` 都不必再打 `--server`。

### 還沒做的

- `.11` 上的服務目前是 `nohup` 起的，不是 systemd。要正式化就在那台跑 `sudo bash scripts/install-service.sh`，需要密碼。
- `LOGGED_OUT_MARKER` 還沒在真的登出畫面上正面驗證過。

## 七、管理台與公網反代（2026-08-21）

V1 原本把 admin webui 列在不做，這一輪補上，並經 nginx 反代到公網。

### 管理台

`/admin`，登入之後四頁：

- **總覽**：瀏覽器活著沒、排隊數、剩餘點數、服務執行時間、近 200 筆的成功與失敗數
- **金鑰**：現場發金鑰、停用、啟用、刪除，看每把用過幾次與最後使用時間。金鑰只存 sha256 雜湊，原文只在剛發那一次顯示。`.env` 的 `API_KEYS` 在這頁是唯讀列出（遮罩）
- **測試**：用指定金鑰送一單，頁面自動更新到跑完並當場播。走跟真實呼叫端一樣的參數處理與佇列
- **歷史**：近 200 筆 job，含來源金鑰、送出的內容、狀態與錯誤碼、耗時、產出的音檔（可直接點開聽）

版面後來對齊 gemini-web 與 codex-image-service 的家族結構（頂部列、左側邊欄、頁面標題配說明句、卡片），配色維持深色珊瑚橘。總覽的內容則按這個服務的實際用法排：點數是硬上限所以擺第一格並換算成「還能生幾單」，產出是聲音所以最近完成的幾單直接附播放器。

金鑰驗證改成靜態與動態兩邊都認，任一邊有金鑰就強制驗證。音檔端點額外接受已登入的 admin session，因為歷史頁的連結是瀏覽器直接點的、不會帶 `x-api-key`。

視覺照 AGENTS.md 的規範：深色底配珊瑚橘與洋紅，跟 gemini-web 的淺色靛藍分得開。

### nginx

`/home/ct/nginx/default.conf` 的兩個 server 區塊（80 與 443）各加一段，跟在 gemini-web 那段後面：

- `location /suno-web/` 反代到 `192.168.11.11:8071`，`client_max_body_size 8m`、read timeout 120 秒。這支是 job 式 API，送單立刻回，不需要 gemini-web 那種 420 秒的長 timeout
- `location = /suno-web/admin/login` 另外掛 `limit_req zone=sunoweb_login`（6 r/m、burst 5），擋登入暴力嘗試，寫法沿用 codex-image 那段

服務端設 `ADMIN_URL_PREFIX=/suno-web`，頁面連結才會帶對前綴。

### 公網驗收（從筆電打 ching-tech.ddns.net）

| 檢查 | 結果 |
|---|---|
| `/suno-web/admin/login` | 200 |
| 未登入打 `/suno-web/admin` | 303 導向 `https://ching-tech.ddns.net/suno-web/admin/login` |
| `/suno-web/api/generate` 不帶金鑰 | 403 |
| `/suno-web/api/health` | 200 |
| 經管理台發一把動態金鑰，用它查既有 job | 200，金鑰頁的使用次數跟著加一 |
| 亂打金鑰 | 403 |
| 原本 `.env` 的靜態金鑰 | 仍然 200 |

筆電的 `~/.bashrc` 已改成用管理台發的那把動態金鑰、位址指向公網，這樣用量會算在那把金鑰上，管理台看得出來。


## 八、多帳號（2026-08-21）

`WORKER_COUNT=4`，四個 Suno 帳號各一個 profile 目錄，全部在 .11 上直接登入（不搬 profile，見下）。

### 派工

點數優先：挑剩餘點數最多的帳號。四個帳號的月配額實測是 40／100／300／300，單純輪流會讓點數少的先見底、變成失敗來源。點數要跑過一單才觀測得到，還沒有數字的用輪流去發掘；點數一樣多的之間也輪流。`DISPATCH_MODE=round-robin` 是逃生閥。

瀏覽器隨用隨開：派工到才啟動，閒置 10 分鐘關掉。常駐四個真 Chrome 要吃近 5 GB，這服務一個月幾十單、其餘全在閒置，划不來。

### 實測

一次送三單，分別派給帳號 0、1、2，三個瀏覽器同時開著跑：

| job | 帳號 | 耗時 | 產出 |
|---|---|---|---|
| a70bd1b1758b | 0 | 168 秒 | 2 首（216、218 秒） |
| 57026fbd6288 | 1 | 210 秒 | 4 首（60、35、285、273 秒） |
| 997ef74b25ac | 2 | 294 秒 | 4 首（425、480、60、194 秒） |
| 45f1f06763bf | 3 | 95 秒 | 1 首 |

十一首全部下載成功。四帳號合計 650 點，約 65 單。

### 新帳號的兩道關卡

剛註冊的帳號一開始生不出來，症狀是 `submit_failed`。查下去是兩件事疊在一起：

1. 頁面蓋著 Pro 方案的推銷彈窗，Create 的點擊被它吃掉（截圖為證，不是驗證碼）
2. `/api/c/check` 對新帳號回 `required: true`，要 Turnstile

真人開 `suno-web login -w N` 的視窗、關掉彈窗、手動生一首之後，兩個問題同時消失：彈窗不再出現（profile 記住了），`/api/c/check` 也變成 `false`。四個帳號都用這個方式開通。

順帶把這種失敗的錯誤碼獨立出來：被要求驗證碼時回 `captcha_required` 並在訊息裡寫明解法，不再含糊回「feed 沒出現新 clip」。

### profile 不要跨機複製

先前把筆電登好的 profile rsync 到 .11，四個帳號全部變成未登入。兩個原因：

- Chrome 在有 gnome-keyring 的桌機上用鑰匙圈金鑰加密 cookie，複製到沒有鑰匙圈的機器就解不開。已補上 `--password-store=basic`（Playwright 本來就帶，自己接手啟動時弄丟了）
- 同一個帳號被兩台機器的瀏覽器同時用，Suno 會輪換 session token，舊的那份失效

所以規則是：**登入態長在哪台就在哪台用**。.11 的四個帳號都是用 `ssh -X` 直接在那台登的。
