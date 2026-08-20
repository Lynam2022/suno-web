"""Suno 頁面的 selector 與網路 pattern 全部集中在這裡，Suno 改版只修這一檔。
值來自 scripts/probe.py 偵察（2026-08-20，帳號 yaze_lin_j303，headless Chromium）。
偵察發現頁面上兩個分頁叫 Simple / Advanced（不是 Custom），計畫沿用的常數名稱
CUSTOM_TAB 對應到 Advanced 分頁——只是名字沿用計畫，實際指向 Advanced。

重要：Simple 與 Advanced 兩個分頁的表單元件同時掛載在 DOM 上，只是用
visibility:hidden 互相隱藏未啟用的那一份（不是 conditional render）。
這代表：
  1. 用 count() 驗證 selector「存在」不夠，一定要先切到對的分頁、
     再用 is_visible() 或直接 fill() 驗證，否則會拿到 count=1 但實際不可互動的元素。
  2. PROMPT_TEXTAREA、INSTRUMENTAL_TOGGLE 只在 Simple 分頁可見；
     LYRICS_TEXTAREA、STYLES_INPUT、TITLE_INPUT 只在 Advanced 分頁可見。
"""

# ---- 寫入（UI 操作）----

# 分頁按鈕本身用 aria-label 精準比對（aria-label 內容與可見文字一致，"Simple"/"Advanced"）
SIMPLE_TAB = '[aria-label="Simple"]'          # 切回 Simple 模式的分頁鈕
CUSTOM_TAB = '[aria-label="Advanced"]'        # 切到 Advanced（=計畫概念裡的 Custom）模式的分頁鈕

# Simple 模式「Song Description」描述框。
# 注意：這個 textarea 沒有 aria-label，且 placeholder 是會輪替的範例文字
# （例如 "Depressive southern rock song about gender dynamics"、
#  "Comedic chiptune song about clubbing" 每次載入都不同），
# 不能拿 placeholder 字面值當 selector。改用結構穩定的 maxlength="3000"
# （偵察時 Style 框 maxlength=1000、Prompt 框 maxlength=3000、
#  另一個不相關的「Describe the sound you want」框 maxlength=500，三者可區分）。
PROMPT_TEXTAREA = 'textarea[maxlength="3000"]'  # Simple 模式歌曲描述框

# Advanced 模式歌詞框。實際是 Lexical 富文字編輯器的 contenteditable div
# （不是 <textarea> 標籤），用 aria-label="Lyrics editor" 精準命中。
LYRICS_TEXTAREA = '[aria-label="Lyrics editor"]'  # Advanced 分頁「Lyrics」歌詞輸入框

# Advanced 模式曲風框。這個 textarea 同樣沒有 aria-label，且 placeholder
# 也是輪替範例（"vivace, 2 step, bass-boosted..." / "neo soul, male powerful
# voice..."），改用其外層容器的 data-testid="create-form-styles-wrapper"
# 往下找 textarea，這個 data-testid 是頁面上唯一找得到的 data-testid 之一。
STYLES_INPUT = '[data-testid="create-form-styles-wrapper"] textarea'  # Advanced 分頁「Styles」曲風框

# Advanced 模式歌名框（純 input，無 aria-label、無 data-testid，用 placeholder）。
# 偵察發現 DOM 上實際有兩份（響應式版面各一份，只有一份可見），用 .first。
# 重要限制：這個輸入框位在 Advanced 分頁的「More Options」收合區塊裡，
# 偵察時「More Options」預設是收合的，此時 TITLE_INPUT 是 count=2 但兩份都
# visibility:hidden、無法 fill()。呼叫端必須先展開「More Options」才能寫入，
# 這裡沒有現成 selector 給「More Options」（它是純文字 div，無 aria-label/
# data-testid），Task 10 實作時要另外處理或加一個新 selector。
TITLE_INPUT = 'input[placeholder="Song Title (Optional)"]'  # Advanced「More Options」內的歌名框

# Simple 模式「純音樂」開關（pill 按鈕，非 <input type=checkbox>）。
# 重要限制：偵察逐一檢查過 aria-checked / aria-pressed / data-state 三種常見
# 「目前狀態」屬性，這顆按鈕全部沒有——狀態純粹是按鈕內 SVG 圖示的顏色 class
# （例如 text-background-tertiary）在切換，讀不到語意化的 on/off 狀態。
# 這代表 Task 10 範本裡「用 aria-checked 讀目前狀態」的假設不成立，
# _set_instrumental() 那段邏輯永遠讀到 state=None，永遠不會真的點擊。
# 另外此按鈕只在 Simple 分頁可見；Advanced 分頁沒有這顆按鈕，
# Advanced 分頁對應的是歌詞模式 radiogroup（aria-label="Lyrics mode"，
# 內有 Write / Prompt / Instrumental 三個 role=radio 選項，這三個「有」
# aria-checked，可正常讀狀態，但和這裡的 INSTRUMENTAL_TOGGLE 是不同元素）。
INSTRUMENTAL_TOGGLE = '[aria-label="Check this to generate an instrumental only song"]'  # Simple 模式「Instrumental」開關

# Create 按鈕在兩個分頁都存在（同一顆，count=1），aria-label 穩定。
CREATE_BUTTON = '[aria-label="Create song"]'  # 送出生成的 Create 按鈕

# 未登入才會出現的元素：偵察時用已登入態的 profile，無法直接看到登出畫面，
# 改用「文字節點在已登入頁面上 count()==0」反向驗證：對 "Sign in" 全文比對，
# 在目前已登入的 workspace 頁面上出現 0 次（同時試過 Sign In / Log in /
# Log In 皆為 0）。屬合理但未在真正登出頁面上正面驗證過的選擇，見任務報告。
LOGGED_OUT_MARKER = "text=Sign in"  # 只有未登入時才會出現的「Sign in」文字

# ---- 讀取（網路側錄）----

# 偵察時側錄到的 clip 列表端點實際 URL：
#   https://studio-api-prod.suno.com/api/feed/v3
# 回應格式：{"clips": [...], "next_cursor": ..., "has_more": bool}，
# 每個 clip 物件含 id/status/title/audio_url/image_url/metadata.duration 等欄位。
FEED_URL_SUBSTRINGS: list[str] = ["/api/feed/v3"]  # response.url 含此字串即 clip feed

# 偵察時側錄到的點數/方案資訊端點：
#   https://studio-api-prod.suno.com/api/billing/info/
# 回應含頂層 "credits": <int> 欄位。
# 重要：這個帳號是 free tier，"credits" 這個欄位讀到的是「另外購買的點數包」
# 餘額（此帳號目前是 0），不是免費方案的月配額用量；免費月配額另外在同一份
# JSON 的 "monthly_usage"/"monthly_limit" 兩個欄位（此帳號偵察當下是 70/100）。
# extract_credits() 只認 CREDITS_JSON_KEYS 這個 int/float 欄位，所以目前設定
# 對 free tier 帳號會回報 0，不會反映真正剩餘的月配額；如果之後要顯示免費
# 方案的剩餘量，需要另外算 monthly_limit - monthly_usage，這裡先誠實留著
# 只抓 "credits" 這個字面欄位。
CREDITS_URL_SUBSTRINGS: list[str] = ["/api/billing/info/"]  # 點數/帳單資訊端點
CREDITS_JSON_KEYS: list[str] = ["credits"]  # 餘額在 JSON 的哪個 key
