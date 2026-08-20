# Next

跨檔案、要決定或要人工做的事放這裡。逐項的技術細節在 `docs/acceptance-2026-08-20.md`（八節，記錄每個實測結論），動這條線之前先讀它。

## 現況

服務跑在 192.168.11.11:8071，systemd `suno-web-api`（enabled）。對外走 nginx 的 `https://ching-tech.ddns.net/suno-web/`，管理台在 `/suno-web/admin`。四個 Suno 帳號、合計約 65 單額度、瀏覽器隨用隨開、派工點數優先。

## 待辦

- [ ] 帳號 0 只剩 20 點（2 單），用完之後派工會自動跳過它。要補就再開一個免費帳號，`ssh -X` 到 .11 跑 `uv run suno-web login -w 4`，記得**真人手動生一單**開通（推銷彈窗 + `/api/c/check` 的 `required:true` 都靠這一步解掉），然後把 `.env` 的 `WORKER_COUNT` 加一並重啟。
- [ ] 每月配額何時重置沒實測過。`monthly_usage` 歸零的時間點若不是月初，派工的「還能生幾單」估算會失準一天。下個月初對一次 `/api/health` 的 credits 就知道。
- [ ] `LOGGED_OUT_MARKER` 還沒在真的登出畫面上正面驗證過。登入態哪天過期時，順手確認 job 正確回的是 `not_logged_in`，而非含糊的 `submit_failed`。
- [ ] `.11` 的 Chrome 是把 deb 解在家目錄的，所以 `.env` 設了 `CHROME_NO_SANDBOX=true`。哪天用 apt 正式裝了 Chrome，把那行拿掉恢復沙箱。
- [ ] 音檔目前留 14 天（`AUDIO_RETENTION_DAYS`）。四帳號跑滿一個月約 260 首、每首 2 到 4 MB，磁碟用量到時候看一眼再決定要不要縮短。

## 刻意不做

- 瀏覽器自動自癒：V1 沒有。替代做法是外部監控打 `/api/health` 看 `browser_alive`，false 就 `systemctl restart suno-web-api`。將來要補時，記得一併處理 `SunoRunner._sniffing` 這個旗標（重啟瀏覽器後 sniffer 不會自動裝到新分頁上）。
- Suno 的 extend／cover／persona／stems：scope 大、UI 更複雜，每個都要重新偵察。
- wav 下載、官方 API fallback：前者要 Pro，後者 Suno 沒有給消費者的官方 API。

## 動手前要知道的三件事

1. **瀏覽器層必須是真 Chrome + CDP**，不能改回 Playwright 內建的 Chromium，也不能讓 Playwright 去啟動 Chrome。理由與對照實驗在 acceptance 第五節。
2. **profile 不要跨機複製**。登入態長在哪台就在哪台用，兩台共用同一個帳號會被 Suno 輪換 token 踢掉。
3. **Suno 改版時只修 `src/selectors.py`**。selector 與 feed 的 URL pattern 全部集中在那一檔，每個常數旁邊都有「這是畫面上的什麼、為什麼是這個值」的註解。
