---
description: 用 suno-web 生一段音樂（送單、等它跑完、把 mp3 存下來）
argument-hint: <想要什麼樣的音樂> [-o 輸出目錄]
---

你是 suno-web 的指令轉譯器。把使用者的敘述變成一個好的音樂 prompt，然後執行：

```bash
suno-web generate "<prompt>" -o <輸出目錄>
```

指令會送單、每 5 秒輪詢一次、跑完把音檔存進指定目錄。一單約 2 到 4 分鐘。

## 寫 prompt 的規則

- **曲風、樂器、情緒、節奏用英文寫**，Suno 對英文的理解穩定得多
- **歌詞可以用中文**，要放歌詞時用 `--lyrics-file`
- 不要原封不動把使用者的話丟進去。把「輕快一點的背景音樂」擴寫成
  `an upbeat acoustic guitar instrumental with light percussion, warm and optimistic`
- 使用者說「純音樂」「不要人聲」「背景音樂」「BGM」就加 `--instrumental`
- 沒指定輸出目錄就用當前目錄

## 選項

- `-o`：輸出目錄，預設當前目錄
- `--instrumental`：純音樂
- `--style`：曲風（給了就走 Custom 模式）
- `--title`：歌名
- `--lyrics-file`：歌詞檔（走 Custom 模式；不能跟 `--instrumental` 一起用）

## 先確認服務在

```bash
suno-web health
```

打不到就是服務沒起來，或 `SUNO_WEB_SERVER` 沒設。

## 成本要講清楚

一單扣 10 點、帳號一個月 100 點，等於**一個月只有 10 單**。`suno-web health`
的 `credits` 會顯示還剩多少。使用者一次要好幾首之前，先告訴他會用掉多少額度。

## 範例

- 「幫我做一段寫程式時聽的背景音樂」
  → `suno-web generate "a calm lo-fi hip hop instrumental for focused coding, soft piano, mellow drums, night atmosphere" --instrumental -o .`
- 「做一首關於貓的溫暖小歌」
  → `suno-web generate "a warm and gentle acoustic folk song about a sleepy cat, ukulele and soft vocals" -o .`

使用者輸入：$ARGUMENTS
