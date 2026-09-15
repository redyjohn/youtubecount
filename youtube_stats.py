#!/usr/bin/env python3
"""YouTube playlist like-count bot for dance contest teams."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yt_dlp

TZ_TAIPEI = timezone(timedelta(hours=8))
DEFAULT_PLAYLIST = "https://www.youtube.com/playlist?list=PLJJ9XaApGghM"
TEAM_RE = re.compile(r"《(.+?)》")
GROUP_RE = re.compile(r"第(\d+)組")
ROOT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT_DIR / "output"
DOCS_DIR = ROOT_DIR / "docs"


def now_taipei() -> datetime:
    return datetime.now(TZ_TAIPEI)


def iso_now() -> str:
    return now_taipei().strftime("%Y-%m-%d %H:%M:%S")


def parse_team_name(title: str) -> str:
    match = TEAM_RE.search(title or "")
    return match.group(1).strip() if match else (title or "未知隊伍").strip()


def parse_group_no(title: str) -> int | None:
    match = GROUP_RE.search(title or "")
    return int(match.group(1)) if match else None


def fetch_playlist(url: str) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "ignoreerrors": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        raise RuntimeError(f"無法讀取播放清單：{url}")
    return info


def fetch_video_stats(video_id: str) -> dict:
    url = f"https://www.youtube.com/watch?v={video_id}"
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "ignoreerrors": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        return {"id": video_id, "like_count": None, "view_count": None, "comment_count": None}
    return {
        "id": video_id,
        "like_count": info.get("like_count"),
        "view_count": info.get("view_count"),
        "comment_count": info.get("comment_count"),
        "upload_date": info.get("upload_date"),
        "duration": info.get("duration"),
    }


def fetch_video_stats_api(video_ids: list[str], api_key: str) -> dict[str, dict]:
    stats_map: dict[str, dict] = {}
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i : i + 50]
        params = urllib.parse.urlencode(
            {"part": "statistics", "id": ",".join(chunk), "key": api_key}
        )
        url = f"https://www.googleapis.com/youtube/v3/videos?{params}"
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for item in data.get("items") or []:
            stats = item.get("statistics") or {}
            stats_map[item["id"]] = {
                "id": item["id"],
                "like_count": int(stats["likeCount"]) if "likeCount" in stats else 0,
                "view_count": int(stats["viewCount"]) if "viewCount" in stats else 0,
                "comment_count": int(stats["commentCount"]) if "commentCount" in stats else 0,
            }
    return stats_map


def collect_rows(playlist: dict, workers: int = 6) -> list[dict]:
    entries = [e for e in (playlist.get("entries") or []) if e and e.get("id")]
    api_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    stats_map: dict[str, dict] = {}
    if api_key:
        print("使用 YouTube Data API 讀取按讚數")
        stats_map = fetch_video_stats_api([e["id"] for e in entries], api_key)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(fetch_video_stats, e["id"]): e["id"] for e in entries}
            done = 0
            for future in as_completed(futures):
                video_id = futures[future]
                try:
                    stats_map[video_id] = future.result()
                except Exception as exc:
                    stats_map[video_id] = {"id": video_id, "error": str(exc)}
                done += 1
                print(f"  讀取進度 {done}/{len(entries)}", flush=True)

    rows = []
    for index, entry in enumerate(entries, start=1):
        title = entry.get("title") or ""
        stats = stats_map.get(entry["id"], {})
        likes = stats.get("like_count")
        views = stats.get("view_count")
        comments = stats.get("comment_count")
        rows.append(
            {
                "playlist_index": index,
                "group_no": parse_group_no(title) or index,
                "team": parse_team_name(title),
                "title": title,
                "video_id": entry["id"],
                "url": f"https://www.youtube.com/watch?v={entry['id']}",
                "like_count": likes if isinstance(likes, int) else 0,
                "view_count": views if isinstance(views, int) else 0,
                "comment_count": comments if isinstance(comments, int) else 0,
                "stats_ok": isinstance(likes, int),
            }
        )

    if not any(row["stats_ok"] for row in rows):
        raise RuntimeError("無法取得按讚數（可能被 YouTube 擋下），已中止以免覆蓋舊資料")

    rows.sort(key=lambda r: (-r["like_count"], r["group_no"]))
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
        row.pop("stats_ok", None)
    return rows


def save_outputs(playlist: dict, rows: list[dict], fetched_at: str) -> dict[str, Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "playlist_id": playlist.get("id"),
        "playlist_title": playlist.get("title"),
        "channel": playlist.get("channel") or playlist.get("uploader"),
        "playlist_url": f"https://www.youtube.com/playlist?list={playlist.get('id')}",
        "fetched_at": fetched_at,
        "team_count": len(rows),
        "total_likes": sum(r["like_count"] for r in rows),
        "total_views": sum(r["view_count"] for r in rows),
        "teams": rows,
    }

    json_path = OUTPUT_DIR / "latest.json"
    csv_path = OUTPUT_DIR / "latest.csv"
    html_path = OUTPUT_DIR / "dashboard.html"
    history_dir = OUTPUT_DIR / "history"
    history_dir.mkdir(exist_ok=True)
    stamp = now_taipei().strftime("%Y%m%d-%H%M%S")
    history_path = history_dir / f"{stamp}.json"

    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    history_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["rank", "group_no", "team", "like_count", "view_count", "comment_count", "url"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row[k] for k in writer.fieldnames if k in row})

    html_path.write_text(render_dashboard(payload), encoding="utf-8")

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    docs_html = DOCS_DIR / "index.html"
    docs_html.write_text(render_dashboard(payload), encoding="utf-8")
    (DOCS_DIR / "latest.json").write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")
    (DOCS_DIR / "latest.csv").write_text(csv_path.read_text(encoding="utf-8-sig"), encoding="utf-8-sig")
    (DOCS_DIR / ".nojekyll").write_text("", encoding="utf-8")

    return {
        "json": json_path,
        "csv": csv_path,
        "html": html_path,
        "docs": docs_html,
        "history": history_path,
    }


def render_dashboard(payload: dict) -> str:
    teams = payload["teams"]
    max_likes = max((t["like_count"] for t in teams), default=1) or 1
    rows_html = []
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    for team in teams:
        pct = round(team["like_count"] / max_likes * 100, 1)
        medal = medals.get(team["rank"], "")
        rows_html.append(
            f"""
            <tr>
              <td class="rank">{medal} {team['rank']}</td>
              <td>第{team['group_no']}組</td>
              <td class="team"><a href="{team['url']}" target="_blank" rel="noopener">{escape(team['team'])}</a></td>
              <td class="likes">{team['like_count']:,}</td>
              <td>{team['view_count']:,}</td>
              <td>
                <div class="bar-wrap"><div class="bar" style="width:{pct}%"></div></div>
              </td>
            </tr>
            """
        )

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(payload['playlist_title'])}｜按讚統計</title>
  <style>
    :root {{
      --bg: #0d0c10;
      --panel: #17161c;
      --line: #2b2933;
      --gold: #f0c14b;
      --pink: #ff4d8d;
      --text: #f6f3ea;
      --muted: #9a9588;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Noto Sans TC", "Microsoft JhengHei", sans-serif;
      background:
        radial-gradient(circle at top right, rgba(240,193,75,.18), transparent 28%),
        radial-gradient(circle at left 20%, rgba(255,77,141,.12), transparent 32%),
        var(--bg);
      color: var(--text);
    }}
    main {{ max-width: 1080px; margin: 0 auto; padding: 32px 20px 64px; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; letter-spacing: .04em; }}
    .meta {{ color: var(--muted); margin-bottom: 24px; }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      margin-bottom: 24px;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 16px 18px;
    }}
    .card .label {{ color: var(--muted); font-size: 13px; }}
    .card .value {{ font-size: 28px; font-weight: 700; margin-top: 6px; color: var(--gold); }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      overflow: hidden;
    }}
    th, td {{ padding: 12px 14px; text-align: left; border-bottom: 1px solid var(--line); }}
    th {{ color: var(--muted); font-weight: 600; font-size: 13px; }}
    tr:last-child td {{ border-bottom: 0; }}
    a {{ color: var(--text); text-decoration: none; }}
    a:hover {{ color: var(--gold); }}
    .team {{ font-weight: 700; }}
    .likes {{ font-variant-numeric: tabular-nums; font-weight: 700; color: var(--pink); }}
    .rank {{ white-space: nowrap; font-weight: 700; }}
    .bar-wrap {{ height: 8px; background: #2a2730; border-radius: 99px; overflow: hidden; min-width: 90px; }}
    .bar {{ height: 100%; background: linear-gradient(90deg, var(--pink), var(--gold)); }}
    input {{
      width: 100%;
      margin: 0 0 16px;
      padding: 12px 14px;
      border-radius: 12px;
      border: 1px solid var(--line);
      background: #111016;
      color: var(--text);
      font-size: 15px;
    }}
  </style>
</head>
<body>
  <main>
    <h1>{escape(payload['playlist_title'])}</h1>
    <p class="meta">
      {escape(payload.get('channel') or '')}　｜　更新時間 {escape(payload['fetched_at'])}（台北時間）　｜　
      <a href="{escape(payload.get('playlist_url') or '#')}" target="_blank" rel="noopener">播放清單</a>
    </p>
    <div class="cards">
      <div class="card"><div class="label">參賽隊伍</div><div class="value">{payload['team_count']}</div></div>
      <div class="card"><div class="label">總按讚數</div><div class="value">{payload['total_likes']:,}</div></div>
      <div class="card"><div class="label">總觀看數</div><div class="value">{payload['total_views']:,}</div></div>
    </div>
    <input id="q" type="search" placeholder="搜尋隊名…">
    <table>
      <thead>
        <tr>
          <th>排名</th>
          <th>出場序</th>
          <th>隊名</th>
          <th>按讚</th>
          <th>觀看</th>
          <th>相對熱度</th>
        </tr>
      </thead>
      <tbody>
        {''.join(rows_html)}
      </tbody>
    </table>
    <p class="meta" style="margin-top:24px">網頁由 GitHub Pages 部署。本機更新後推送即可刷新；若已設定 YouTube API Key，GitHub Actions 會每小時自動更新。</p>
  </main>
  <script>
    const q = document.getElementById('q');
    q.addEventListener('input', () => {{
      const keyword = q.value.trim().toLowerCase();
      document.querySelectorAll('tbody tr').forEach(row => {{
        row.style.display = row.innerText.toLowerCase().includes(keyword) ? '' : 'none';
      }});
    }});
  </script>
</body>
</html>
"""


def escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def print_table(rows: list[dict], title: str, fetched_at: str) -> None:
    print()
    print(title)
    print(f"更新時間：{fetched_at}（台北時間）")
    print("-" * 56)
    print(f"{'排名':<4} {'出場':<6} {'按讚':>8}  隊名")
    print("-" * 56)
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    for row in rows:
        medal = medals.get(row["rank"], "  ")
        print(f"{medal}{row['rank']:<2} 第{row['group_no']:<2}組 {row['like_count']:>8,}  {row['team']}")
    print("-" * 56)
    print(f"合計 {len(rows)} 隊，總按讚 {sum(r['like_count'] for r in rows):,}")


def serve_dashboard(html_path: Path, port: int) -> None:
    directory = str(html_path.parent)

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)

        def log_message(self, fmt, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/{html_path.name}"
    print(f"儀表板：{url}")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止本機伺服器")


def run(playlist_url: str, workers: int, open_html: bool, serve: bool, port: int) -> Path:
    print(f"讀取播放清單：{playlist_url}")
    playlist = fetch_playlist(playlist_url)
    print(f"清單名稱：{playlist.get('title')}（{len(playlist.get('entries') or [])} 支影片）")
    rows = collect_rows(playlist, workers=workers)
    fetched_at = iso_now()
    paths = save_outputs(playlist, rows, fetched_at)
    print_table(rows, playlist.get("title") or "播放清單", fetched_at)
    print(f"\nJSON：{paths['json']}")
    print(f"CSV ：{paths['csv']}")
    print(f"儀表板：{paths['html']}")
    if serve:
        serve_dashboard(paths["html"], port)
    elif open_html:
        webbrowser.open(paths["html"].as_uri())
    return paths["html"]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="統計 YouTube 播放清單每隊按讚數")
    parser.add_argument("playlist", nargs="?", default=DEFAULT_PLAYLIST, help="播放清單網址")
    parser.add_argument("--workers", type=int, default=6, help="同時抓取影片數")
    parser.add_argument("--open", action="store_true", help="抓完後開啟儀表板")
    parser.add_argument("--serve", action="store_true", help="啟動本機網頁並開啟瀏覽器")
    parser.add_argument("--port", type=int, default=8765, help="本機網頁埠號")
    args = parser.parse_args()
    run(args.playlist, args.workers, args.open, args.serve, args.port)


if __name__ == "__main__":
    main()
