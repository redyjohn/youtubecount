# YouTube 街舞按讚統計

統計 [2026一中舞極限街舞大賽青少組初賽播放清單](https://www.youtube.com/playlist?list=PLJJ9XaApGghM) 每一隊的隊名與目前按讚數。

## 網頁

https://redyjohn.github.io/youtubecount/

## 本機更新

```bash
pip install -r requirements.txt
python youtube_stats.py
git add docs output
git commit -m "Update like counts"
git push
```

Windows 也可雙擊 `更新統計.bat`，再把 `docs/` 與 `output/` 推上 GitHub。

## 每小時自動更新（選用）

GitHub 雲端主機會被 YouTube 擋下，因此自動更新需要 [YouTube Data API](https://console.cloud.google.com/apis/library/youtube.googleapis.com) 金鑰：

1. 建立 API Key
2. 在 repo 的 Settings → Secrets and variables → Actions 新增 `YOUTUBE_API_KEY`
3. 之後可在 Actions 手動執行 **Update stats**，或等每小時排程
