# YouTube 街舞按讚統計

統計 [2026一中舞極限街舞大賽青少組初賽播放清單](https://www.youtube.com/playlist?list=PLJJ9XaApGghM) 每一隊的隊名與目前按讚數。

## 網頁

https://redyjohn.github.io/youtubecount/

GitHub Actions 每小時會重新抓取 YouTube 數據並部署。

## 本機更新

```bash
pip install -r requirements.txt
python youtube_stats.py
```

Windows 也可雙擊 `更新統計.bat`。
