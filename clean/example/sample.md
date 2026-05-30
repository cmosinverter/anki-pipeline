# Example category

This is a seed file so the pipeline is runnable out of the box. Delete it (and
the `example/` folder) once you've added your own content, then re-run
`python3 tools/md_to_anki.py --prune` to clear the sample card from Anki.

## 範例題：什麼是 spaced repetition？

**題目：**

What is spaced repetition and why does it work?

**重點：**

- 隨複習次數拉長間隔，命中遺忘曲線的臨界點
- 答對 → 間隔變長；答錯 → 間隔縮短
- Anki 的 SM-2 演算法即是其實作

**解答：**

Spaced repetition 是一種把複習排在「即將遺忘」時間點的學習法。每答對一次，
下次複習的間隔就拉長；答錯則縮短。這樣能用最少的複習次數維持長期記憶，比固定
間隔或臨時抱佛腳有效率。Anki 用 SM-2（及新版 FSRS）排程演算法決定每張卡的下次
出現時間。

**出處：** `clean/example/sample.md`（seed 範例）

**標籤：** #example #learning
