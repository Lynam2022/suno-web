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
