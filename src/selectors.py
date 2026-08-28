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
# DOM 上有兩份（響應式版面各一份）。
# Task 10 實機重驗更正 Task 9 的假設：問題不是「More Options 收合區塊要展開」——
# 頁面上確實有個純文字「More Options」div，但實測點擊它前後，兩份 TITLE_INPUT
# 的可見性完全沒變化，跟這個欄位無關。真正原因是 .first 精準命中了響應式版面
# 裡隱藏的那份（visible=False，直接 fill() 會 timeout），.last 才是目前版面下
# 可見可填的那份。已在 production 預設的 1280x720 viewport、以及加大到
# 1680x1050 兩種情況下都驗證過 .last 可直接 fill() 成功、讀回值一致。
# 呼叫端請用 .last，不要用 .first。
TITLE_INPUT = 'input[placeholder="Song Title (Optional)"]'  # Advanced 分頁歌名框（用 .last）

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

# Advanced 模式「Lyrics mode」radiogroup 裡的 Instrumental 選項（Task 10 實機
# 驗證，非 Task 9 原本的假設）。radiogroup 本身：
# [role="radiogroup"][aria-label="Lyrics mode"]，底下三個 role=radio（Write /
# Prompt / Instrumental）各自沒有 aria-label，只有文字，所以用 Playwright
# 選擇器擴充語法 :has-text()（非標準 CSS，但 page.locator() 原生支援）精準
# 命中文字為 "Instrumental" 的那顆。這三個 radio 都有正常 aria-checked，
# 已實測點選後 aria-checked 會正確變 true/false（不像 INSTRUMENTAL_TOGGLE 讀
# 不到狀態）。
# 已知限制：這顆按鈕在 production 預設 1280x720 viewport 下，常被浮動的側欄
# resize handle（或其他動畫中的元素）蓋住，Playwright 座標式 click() 會被
# 攔截並必現 timeout（即使 force=True 也可能點到蓋住的元素而沒有真正選取）。
# src/suno.py 改用 JS `el.click()` 直接觸發 DOM click 事件、不做座標命中測試
# 繞過遮擋，已在 1280x720 下實測兩個方向（選取/切回 Write）都正確反映在
# aria-checked。
#
# 重要（Task 10 實機踩到的坑）：這個 radiogroup 選到哪個選項會被 Suno 存成
# 帳號的 create 表單草稿，不是單純的頁面內 state——即使每個 job 開始都
# `page.goto()` 全新導覽（controller ruling 1），只要帳號上一次是選
# Instrumental，這次全新頁面的 Lyrics mode 照樣是 Instrumental（實測會導致
# LYRICS_TEXTAREA 整個從 DOM 消失，count=0，不是「存在但隱藏」）。這點跟
# INSTRUMENTAL_TOGGLE（Simple 模式）不同：後者實測即使上一個 job 留著開著，
# 下一次全新頁面還是會重置回關閉。也就是說 ruling 2「新分頁預設一定是 off」
# 這個假設只對 Simple 模式的 INSTRUMENTAL_TOGGLE 成立，對這裡的 Lyrics mode
# radiogroup 不成立——custom 模式無論 want_instrumental 是 True 或 False，
# 都要明確選一次目標選項（用 aria-checked 判斷要不要點，已經對就跳過），
# 不能只在 True 時才點。
LYRICS_MODE_INSTRUMENTAL = (
    '[role="radiogroup"][aria-label="Lyrics mode"] '
    '[role="radio"]:has-text("Instrumental")'
)  # Advanced 分頁「純音樂」選項
LYRICS_MODE_WRITE = (
    '[role="radiogroup"][aria-label="Lyrics mode"] '
    '[role="radio"]:has-text("Write")'
)  # Advanced 分頁「自己寫歌詞」選項（custom+非 instrumental 要明確切回這個）

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
FEED_URL_SUBSTRINGS: list[str] = ["/api/feed"]  # response.url 含此字串即 clip feed

# 偵察時側錄到的點數/方案資訊端點：
#   https://studio-api-prod.suno.com/api/billing/info/
# 回應含頂層 "credits": <int> 欄位。
# 重要：這個帳號是 free tier，"credits" 這個欄位讀到的是「另外購買的點數包」
# 餘額（此帳號偵察當下是 0），不是免費方案的月配額用量；免費月配額另外在同一份
# JSON 的 "monthly_usage"/"monthly_limit" 兩個欄位（此帳號偵察當下是 70/100，
# 代表本月已用 70、還剩 30）。Task 10 的 extract_credits() 已改成優先算
# monthly_limit - monthly_usage 當作「真正剩餘量」，只有算不出來時才退回這裡
# CREDITS_JSON_KEYS 的字面查找——見下方 CREDITS_MONTHLY_USAGE_KEY /
# CREDITS_MONTHLY_LIMIT_KEY。
CREDITS_URL_SUBSTRINGS: list[str] = ["/api/billing/info/"]  # 點數/帳單資訊端點
CREDITS_JSON_KEYS: list[str] = ["credits"]  # 餘額在 JSON 的哪個 key（字面 fallback，見下）

# 免費方案月配額的兩個欄位。extract_credits() 優先算 monthly_limit - monthly_usage
# 當作「真正剩餘量」，因為同一份 payload 就有這兩個欄位、算起來零成本，比字面
# CREDITS_JSON_KEYS（"credits"，對 free tier 帳號恆為 0，見上方說明）更貼近
# 使用者感受到的「這個月還能生成幾首」。算不出來（缺欄位/型別不對，例如付費
# 方案可能沒有月配額概念）才退回 CREDITS_JSON_KEYS 字面查找。
CREDITS_MONTHLY_USAGE_KEY = "monthly_usage"
CREDITS_MONTHLY_LIMIT_KEY = "monthly_limit"
