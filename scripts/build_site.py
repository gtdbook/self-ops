"""从 data/digest/*.json 生成静态站点到 _site/（首页 + 每日归档页 + 404）。"""

import html
import json
import os
import sys
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, os.path.dirname(__file__))
from common import DIGEST_DIR, SITE_DIR, SITE_URL, atomic_write_text, load_config, now_tz  # noqa: E402

CSS = """
:root{--bg:#f6f7f9;--card:#ffffff;--text:#1a1d21;--muted:#6b7280;--accent:#0969da;
--border:#e5e7eb;--chip:#eef2ff;--rank:#9ca3af}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--card:#161b22;--text:#e6edf3;
--muted:#8b949e;--accent:#58a6ff;--border:#30363d;--chip:#1c2431;--rank:#6e7681}}
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",
"Hiragino Sans GB","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--text);
line-height:1.65}
.wrap{max-width:900px;margin:0 auto;padding:36px 20px 72px}
header h1{font-size:26px;margin:0 0 6px;letter-spacing:.5px}
header .sub{color:var(--muted);font-size:14px;margin-bottom:18px}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:26px}
.chip{background:var(--chip);color:var(--accent);border-radius:999px;
padding:3px 12px;font-size:13px}
section{background:var(--card);border:1px solid var(--border);border-radius:12px;
padding:20px 22px;margin-bottom:22px}
section h2{font-size:17px;margin:0 0 4px;padding-bottom:10px;border-bottom:1px solid var(--border)}
section .err{color:#d1242f;font-size:12.5px;margin:6px 0 0}
.item{display:flex;gap:12px;padding:11px 0;border-bottom:1px dashed var(--border)}
.item:last-child{border-bottom:none}
.item .rank{color:var(--rank);font-variant-numeric:tabular-nums;min-width:22px;
text-align:right;font-size:13px;padding-top:3px}
.item .body{min-width:0;flex:1}
.item a.title{color:var(--text);font-weight:600;text-decoration:none;font-size:15px}
.item a.title:hover{color:var(--accent)}
.item .meta{color:var(--muted);font-size:12.5px;margin-top:2px}
.item .summary{color:var(--muted);font-size:13px;margin-top:3px}
.item .reason{color:var(--accent);font-size:12.5px;margin-top:3px}
nav.days a{display:inline-block;margin:0 10px 8px 0;color:var(--accent);
text-decoration:none;font-size:13.5px}
footer{color:var(--muted);font-size:12.5px;text-align:center;margin-top:34px;
line-height:1.9}
footer a{color:var(--muted)}
a.back{color:var(--accent);text-decoration:none;font-size:13.5px}
"""

PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="alternate" type="application/rss+xml" title="self-ops RSS" href="feed.xml">
<style>{css}</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>📡 self-ops 情报站</h1>
  <div class="sub">自动抓取 · 自动处理 · 自动部署 —— 由 GitHub Actions 驱动，每小时更新</div>
</header>
{body}
<footer>
  数据来源：Hacker News API · GitHub Search · 各官方博客 RSS（版权归原作者所有）<br>
  由 <a href="https://github.com/gtdbook/self-ops">gtdbook/self-ops</a> 流水线自动生成 ·
  <a href="feed.xml">RSS 订阅</a> ·
  更新于 {updated}
</footer>
</div>
</body>
</html>
"""


def esc(s):
    return html.escape(str(s or ""), quote=True)


def render_item(i, it):
    parts = [f'<div class="item"><div class="rank">{i}</div><div class="body">']
    if it.get("url"):
        parts.append(f'<a class="title" href="{esc(it["url"])}" target="_blank" rel="noopener">{esc(it["title"])}</a>')
    else:
        parts.append(f'<span class="title">{esc(it["title"])}</span>')
    if it.get("meta"):
        parts.append(f'<div class="meta">{esc(it["meta"])}</div>')
    if it.get("summary"):
        parts.append(f'<div class="summary">{esc(it["summary"])}</div>')
    if it.get("reason"):
        parts.append(f'<div class="reason">💡 {esc(it["reason"])}</div>')
    parts.append("</div></div>")
    return "".join(parts)


def render_section(sec):
    h = [f'<section><h2>{esc(sec.get("emoji", ""))} {esc(sec["title"])}</h2>']
    if sec.get("errors"):
        last = sec["errors"][-1]
        h.append(f'<p class="err">⚠ 近期该源曾报错：{esc(last.get("error", "")[:160])}</p>')
    h.extend(render_item(i, it) for i, it in enumerate(sec["items"], 1))
    h.append("</section>")
    return "".join(h)


def render_digest(digest):
    return "".join(render_section(sec) for sec in digest["sources"].values())


def load_days(max_days):
    days = []
    for path in sorted(DIGEST_DIR.glob("*.json"), reverse=True)[:max_days]:
        try:
            days.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return days


def build_feed(latest, max_items=80):
    """从最新日报生成 RSS 2.0，让情报站自身成为可订阅源。"""
    try:
        pub = format_datetime(
            datetime.fromisoformat(latest["generated_at"]).astimezone(timezone.utc)
        )
    except Exception:
        pub = format_datetime(datetime.now(timezone.utc))
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<rss version=\"2.0\"><channel>",
        "<title>self-ops 情报站</title>",
        f"<link>{SITE_URL}</link>",
        "<description>自动抓取的每日技术情报：Hacker News / GitHub 新星 / Product Hunt / 博客 RSS</description>",
        "<language>zh-CN</language>",
    ]
    count = 0
    for sec in latest["sources"].values():
        for it in sec["items"]:
            if count >= max_items:
                break
            desc = " · ".join(
                x for x in (sec["title"], it.get("meta", ""), it.get("summary", "")) if x
            )
            parts.append(
                f"<item><title>{escape(it['title'])}</title>"
                f"<link>{escape(it['url'])}</link>"
                f"<description>{escape(desc)}</description>"
                f"<pubDate>{pub}</pubDate>"
                f"<guid>{escape(it['url'])}</guid></item>"
            )
            count += 1
    parts.append("</channel></rss>")
    return "\n".join(parts)


def build():
    cfg = load_config()
    max_days = cfg.get("site_max_days", 60)
    days = load_days(max_days)
    if not days:
        print("[warn] 没有日报数据，站点只生成骨架")
        index_html = PAGE.format(
            title="self-ops 情报站",
            css=CSS,
            body="<section><h2>⏳ 等待第一次抓取</h2><p>流水线尚未产出数据，请稍后再来。</p></section>",
            updated=esc(now_tz().strftime("%Y-%m-%d %H:%M")),
        )
        atomic_write_text(SITE_DIR / "index.html", index_html)
        return

    updated = esc(days[0]["generated_at"][:16].replace("T", " "))
    total_all = sum(
        sum(len(s["items"]) for s in d["sources"].values()) for d in days
    )

    # 首页：统计 + 最新一天全文 + 归档导航
    chips = (
        f'<div class="chips">'
        f'<span class="chip">收录 {len(days)} 天</span>'
        f'<span class="chip">累计 {total_all} 条</span>'
        f'<span class="chip">最新 {esc(days[0]["date"])}</span>'
        f'<span class="chip">数据源 {len(days[0]["sources"])} 个</span>'
        f"</div>"
    )
    archive = '<nav class="days">' + "".join(
        f'<a href="day/{d["date"]}.html">{d["date"]}</a>'
        for d in days[1:]
    ) + "</nav>"
    if len(days) > 1:
        archive_section = f'<section><h2>🗓 历史日报</h2>{archive}</section>'
    else:
        archive_section = ""
    index_html = PAGE.format(
        title="self-ops 情报站",
        css=CSS,
        body=chips + render_digest(days[0]) + archive_section,
        updated=updated,
    )
    atomic_write_text(SITE_DIR / "index.html", index_html)

    # 每日归档页
    for d in days:
        body = (
            f'<p><a class="back" href="../index.html">← 返回首页</a></p>'
            + render_digest(d)
        )
        page = PAGE.format(
            title=f"self-ops 情报 · {d['date']}",
            css=CSS,
            body=body,
            updated=updated,
        )
        atomic_write_text(SITE_DIR / "day" / f"{d['date']}.html", page)

    atomic_write_text(SITE_DIR / "404.html",
        PAGE.format(
            title="404 · self-ops",
            css=CSS,
            body='<section><h2>🤔 页面不存在</h2><p><a class="back" href="index.html">返回首页</a></p></section>',
            updated=updated,
        ),
    )
    atomic_write_text(SITE_DIR / "feed.xml", build_feed(days[0]))
    print(f"站点构建完成：{len(days)} 天日报 + feed.xml，输出到 _site/")


if __name__ == "__main__":
    build()
