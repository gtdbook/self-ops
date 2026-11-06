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
from common import (  # noqa: E402
    DATA_DIR,
    DIGEST_DIR,
    SITE_DIR,
    SITE_URL,
    atomic_write_text,
    load_config,
    load_json,
    now_tz,
)

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
table{width:100%;border-collapse:collapse;font-size:13.5px;margin-top:10px}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--border)}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.fitbar{color:var(--accent);letter-spacing:1px}
.samples{color:var(--muted);font-size:12.5px}
.ledgerts{color:var(--rank);font-size:12.5px;min-width:96px;white-space:nowrap}
.ok{color:#1a7f37}.warn{color:#9a6700}.bad{color:#d1242f}
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
  <div class="sub">自动抓取 · 自动处理 · 自动部署 · 自我进化 · 自我沉淀 ——
  <a href="evolution.html">🧬 进化面板</a> ·
  <a href="weekly.html">🗓 每周洞察</a> ·
  <a href="sediment.html">🌿 沉淀库</a> ·
  <a href="feed.xml">RSS 订阅</a></div>
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


# ---------------------------------------------------------------- 进化面板


FIT_BAR_FULL = 5
LEDGER_EMOJI = {
    "source_demote": "📉",
    "source_disable": "⛔",
    "source_probe": "🧪",
    "source_reenable": "✅",
    "source_heal": "🌱",
    "token_promote": "🏷️",
}
SOURCE_NAMES = {
    "hackernews": "Hacker News",
    "github_search": "GitHub 新星",
    "blog_rss": "博客 RSS",
    "cn_rss": "中文技术圈",
    "producthunt": "Product Hunt",
    "aihot": "AIHOT",
}


def fit_bar(v):
    n = max(0, min(FIT_BAR_FULL, round(v * FIT_BAR_FULL)))
    return "▰" * n + "▱" * (FIT_BAR_FULL - n)


def render_fitness_section(fitness):
    sources = fitness.get("sources") or {}
    rows = []
    for key, m in sorted(sources.items(), key=lambda x: -x[1].get("fitness", 0)):
        f = m.get("fitness", 0)
        if m.get("enabled") is False:
            status, cls = f'⛔ 禁用 · 探针 {m.get("probe_at", "?")}', "bad"
        elif m.get("weight_hint", 1.0) < 1.0:
            status, cls = f'📉 降权 ×{m.get("weight_hint")}', "warn"
        else:
            status, cls = "✅ 启用", "ok"
        rows.append(
            f'<tr><td>{esc(SOURCE_NAMES.get(key, key))}</td>'
            f'<td class="num">{f:.2f} <span class="fitbar">{fit_bar(f)}</span></td>'
            f'<td class="num">{m.get("mean_items", 0)}/天</td>'
            f'<td class="num">{m.get("days_present", 0)}/{m.get("window_days", 7)} 天</td>'
            f'<td class="num">{m.get("error_days", 0)}</td>'
            f'<td class="{cls}">{status}</td></tr>'
        )
    body = (
        '<table><tr><th>数据源</th><th class="num">适应度</th><th class="num">产量</th>'
        '<th class="num">在场</th><th class="num">报错</th><th>状态</th></tr>'
        + "".join(rows)
        + "</table>"
        if rows
        else "<p>适应度评估尚未产出（evolve 环首跑后生成）。</p>"
    )
    return f'<section><h2>🧬 数据源适应度（7 日窗口 · 自动降权/禁用/探针复活）</h2>{body}</section>'


def render_prefs_section(prefs):
    tokens = sorted((prefs.get("tokens") or {}).items(), key=lambda x: -abs(x[1]))[:15]
    if not tokens:
        inner = "<p>偏好库尚空：暂无投票，也没有新高频主题。</p>"
    else:
        inner = '<div class="chips">' + "".join(
            f'<span class="chip">{esc(t)} {v:+.1f}</span>' for t, v in tokens
        ) + "</div>"
    return f'<section><h2>🏷️ 自动学到的偏好关键词（每日衰减 + 主题提升 + 投票学习）</h2>{inner}</section>'


def render_garden_section(garden):
    themes = garden.get("themes") or {}
    if not themes:
        return '<section><h2>🌸 知识花园</h2><p>花园尚未播种（周报首跑后生长）。</p></section>'
    rows = []
    for tok, v in sorted(themes.items(), key=lambda x: -x[1].get("hits", 0))[:24]:
        weeks = len(v.get("weeks", []))
        flower = "🌸 " if weeks >= 3 else ""
        samples = v.get("samples") or []
        rep = " / ".join(
            f'<a href="{esc(s.get("url", ""))}" target="_blank" rel="noopener">{esc(s.get("title", ""))[:40]}</a>'
            for s in samples[:1]
            if s.get("url")
        ) or esc((samples[0].get("title", "") if samples else "")[:44])
        rows.append(
            f'<div class="item"><div class="body"><a class="title">{flower}{esc(tok)}</a>'
            f'<div class="meta">追踪 {weeks} 周 · 累计 {v.get("hits", 0)} 次 · 首见 {esc(v.get("first_week", "?"))}</div>'
            f'<div class="samples">{rep}</div></div></div>'
        )
    return (
        f'<section><h2>🌸 知识花园 · 累计 {len(themes)} 个主题（≥3 周重现即常青）</h2>'
        + "".join(rows)
        + "</section>"
    )


def render_ledger_section(ledger):
    entries = list(reversed(ledger.get("entries") or []))[:60]
    if not entries:
        return '<section><h2>🧾 进化日志</h2><p>暂无自动进化决策。</p></section>'
    rows = []
    for e in entries:
        ts = (e.get("ts") or "")[:16].replace("T", " ")
        emoji = LEDGER_EMOJI.get(e.get("kind"), "⚙")
        rows.append(
            f'<div class="item"><div class="ledgerts">{esc(ts)}</div><div class="body">'
            f'<a class="title">{emoji} {esc(e.get("target", ""))}</a>'
            f'<div class="summary">{esc(e.get("detail", ""))}</div></div></div>'
        )
    total = len(ledger.get("entries") or [])
    return (
        f'<section><h2>🧾 进化日志 · 最新 {len(entries)} / 共 {total} 条自动决策</h2>'
        + "".join(rows)
        + "</section>"
    )


def build_evolution_page():
    fitness = load_json(DATA_DIR / "meta" / "fitness.json", {}) or {}
    prefs = load_json(DATA_DIR / "prefs.json", {}) or {}
    garden = load_json(DATA_DIR / "garden.json", {}) or {}
    ledger = load_json(DATA_DIR / "meta" / "evolution.json", {}) or {}
    body = (
        render_fitness_section(fitness)
        + render_prefs_section(prefs)
        + render_garden_section(garden)
        + render_ledger_section(ledger)
    )
    page = PAGE.format(
        title="进化面板 · self-ops 情报站",
        css=CSS,
        body='<p><a class="back" href="index.html">← 返回首页</a></p>' + body,
        updated=esc(now_tz().strftime("%Y-%m-%d %H:%M")),
    )
    atomic_write_text(SITE_DIR / "evolution.html", page)


# ---------------------------------------------------------------- 周报页


def render_themes_block(themes):
    lines = []
    for th in themes:
        rep = " / ".join(
            f'<a href="{esc(s.get("url", ""))}" target="_blank" rel="noopener">{esc(s.get("title", ""))}</a>'
            if s.get("url")
            else esc(s.get("title", ""))
            for s in th.get("samples", [])[:2]
        )
        lines.append(
            f'<div class="item"><div class="body"><a class="title">🌱 {esc(th["token"])}</a>'
            f'<div class="meta">权重 {th.get("weight", 0)} · 出现 {th.get("days", 0)} 天</div>'
            + (f'<div class="samples">{rep}</div>' if rep else "")
            + "</div></div>"
        )
    return "".join(lines)


def render_stats_table(stats):
    if not stats:
        return ""
    rows = []
    for key, s in stats.items():
        arrow = "↑" if s.get("delta", 0) > 0 else ("↓" if s.get("delta", 0) < 0 else "→")
        rows.append(
            f'<tr><td>{esc(SOURCE_NAMES.get(key, key))}</td>'
            f'<td class="num">{s.get("mean", 0)}/天</td>'
            f'<td class="num">{s.get("prev_mean", 0)}/天</td>'
            f'<td class="num">{arrow}{abs(s.get("delta", 0))}</td>'
            f'<td class="num">{s.get("error_days", 0)}</td></tr>'
        )
    return (
        '<table><tr><th>源</th><th class="num">本周日均</th><th class="num">上周日均</th>'
        '<th class="num">变化</th><th class="num">报错天数</th></tr>'
        + "".join(rows)
        + "</table>"
    )


def render_weekly_section(rep, full=True):
    g = rep.get("garden") or {}
    chips = (
        f'<div class="chips">'
        f'<span class="chip">主题 {len(rep.get("themes") or [])} 个</span>'
        f'<span class="chip">Top {len(rep.get("top_items") or [])} 条</span>'
        + (f'<span class="chip">花园 {g.get("total_themes", 0)} 主题（常青 {g.get("evergreen", 0)}）</span>' if g else "")
        + "</div>"
    )
    parts = [chips]
    if rep.get("themes"):
        parts.append(render_themes_block(rep["themes"]))
    if rep.get("top_items"):
        items = rep["top_items"] if full else rep["top_items"][:3]
        parts.append("".join(render_item(i, it) for i, it in enumerate(items, 1)))
    stats_html = render_stats_table(rep.get("stats") or {})
    if stats_html:
        parts.append(stats_html)
    if rep.get("evolution_lines"):
        parts.append(
            '<div class="samples">🧬 '
            + esc("；".join(rep["evolution_lines"][:5]))
            + "</div>"
        )
    return "".join(parts)


# ---------------------------------------------------------------- 沉淀库


STATUS_CLASS = {"常青": "ok", "活跃": "warn", "休眠": ""}
_sediment_links = {}


def render_sediment_entry(token, e):
    cls = STATUS_CLASS.get(e.get("status", ""), "")
    obs_html = []
    for o in (e.get("observations") or [])[-3:]:
        obs_html.append(
            f'<div class="samples">· <a href="{esc(o.get("url", ""))}" target="_blank" rel="noopener">'
            f'{esc(o.get("title", ""))[:64]}</a>（{esc(o.get("source", ""))} · {esc(o.get("date", ""))}）</div>'
        )
    related = "、".join(
        f'<a href="sediment.html#{esc(t)}">{esc(t)}</a>'
        for t in sorted(_sediment_links.get(token, {}), key=lambda x: -_sediment_links[token][x])[:4]
    ) or "（暂无——不为孤立页强行连线）"
    md_url = f"https://github.com/gtdbook/self-ops/blob/main/data/sediment/{token}.md"
    return (
        f'<div class="item" id="{esc(token)}"><div class="body">'
        f'<a class="title" href="{md_url}" target="_blank" rel="noopener">{esc(token)}</a> '
        f'<span class="{cls}">{esc(e.get("status", ""))}</span>'
        f'<div class="meta">{esc(e.get("bed", ""))} · 追踪 {e.get("weeks", 0)} 周 · '
        f'累计 {e.get("hits", 0)} 次 · {esc(e.get("first_week", ""))} → {esc(e.get("last_week", ""))}</div>'
        + "".join(obs_html)
        + f'<div class="samples">双链：{related}</div>'
        f"</div></div>"
    )


def render_sediment_section(title, tokens, entries):
    if not tokens:
        return ""
    items = "".join(render_sediment_entry(t, entries[t]) for t in tokens if t in entries)
    return f'<section><h2>{esc(title)}</h2>{items}</section>' if items else ""


def build_sediment_page():
    global _sediment_links
    idx = load_json(DATA_DIR / "sediment" / "index.json", {}) or {}
    seed = load_json(DATA_DIR / "sediment" / "seed.json", {}) or {}
    entries = idx.get("entries") or {}
    _sediment_links = idx.get("links") or {}

    n_links = sum(len(v) for v in _sediment_links.values()) // 2
    chips = (
        f'<div class="chips">'
        f'<span class="chip">沉淀 {len(entries)} 条</span>'
        f'<span class="chip">园圃 {len(idx.get("beds") or {})}</span>'
        f'<span class="chip">新芽 {len(idx.get("pending") or [])}</span>'
        f'<span class="chip">双链 {n_links}</span>'
        f'<span class="chip">兴趣词 {len(seed.get("seed_tokens") or {})}</span>'
        f"</div>"
    )
    intro = (
        '<section><h2>🌿 沉淀库</h2>'
        "<p>流动的情报按「跨 ≥2 周反复出现」沉淀为永久知识条目：观察记录只进不改、矛盾并存，"
        "每条带原文出处；双链只连真实共同出现的主题。方法论与园圃分类法源自 "
        f'<a href="https://sediment-garden.pages.dev/" target="_blank" rel="noopener">沉淀园 · Sediment Garden</a>'
        f"（本库种子：{seed.get('articles', 0)} 篇 · 摄于 {esc((seed.get('fetched_at') or '')[:10])}）。</p></section>"
    )

    parts = [chips, intro]
    beds = idx.get("beds") or {}
    for bed, toks in sorted(beds.items(), key=lambda x: -len(x[1])):
        sec = render_sediment_section(f"🪴 {bed}", toks, entries)
        if sec:
            parts.append(sec)
    pending = (idx.get("pending") or [])
    if pending:
        chips_pending = "".join(
            f'<span class="chip">{esc(p["token"])}（{p["weight"]}）</span>' for p in pending[:20]
        )
        parts.append(
            f'<section><h2>🌱 新芽圃（出现 1 周，未达沉淀判据，不硬造条目）</h2>'
            f'<div class="chips">{chips_pending}</div></section>'
        )
    seed_tokens = sorted((seed.get("seed_tokens") or {}).items(), key=lambda x: -x[1])[:20]
    if seed_tokens:
        chips_seed = "".join(f'<span class="chip">{esc(t)} {v:+.1f}</span>' for t, v in seed_tokens)
        parts.append(
            f'<section><h2>🧭 来自沉淀园的兴趣先验（稳定权重，不衰减）</h2>'
            f'<div class="chips">{chips_seed}</div></section>'
        )

    page = PAGE.format(
        title="沉淀库 · self-ops 情报站",
        css=CSS,
        body='<p><a class="back" href="index.html">← 返回首页</a></p>' + "".join(parts),
        updated=esc(now_tz().strftime("%Y-%m-%d %H:%M")),
    )
    atomic_write_text(SITE_DIR / "sediment.html", page)


def build_weekly_page():
    weekly_dir = DATA_DIR / "weekly"
    files = sorted(weekly_dir.glob("*.json"), reverse=True) if weekly_dir.exists() else []
    monthly_dir = DATA_DIR / "monthly"
    mfiles = sorted(monthly_dir.glob("*.json"), reverse=True) if monthly_dir.exists() else []
    if not files and not mfiles:
        body = '<section><h2>🗓 每周洞察</h2><p>周报尚未产出（每周一早晨合成）。</p></section>'
    else:
        parts = []
        for i, f in enumerate(files[:12]):
            rep = load_json(f, {}) or {}
            title = f'🗓 每周洞察 · {rep.get("week", f.stem)}'
            parts.append(f'<section><h2>{esc(title)}</h2>{render_weekly_section(rep, full=(i == 0))}</section>')
        for f in mfiles[:6]:
            rep = load_json(f, {}) or {}
            title = f'🗓 月度回顾 · {rep.get("month", f.stem)}'
            parts.append(f'<section><h2>{esc(title)}</h2>{render_weekly_section(rep, full=False)}</section>')
        body = "".join(parts)
    page = PAGE.format(
        title="每周洞察 · self-ops 情报站",
        css=CSS,
        body='<p><a class="back" href="index.html">← 返回首页</a></p>' + body,
        updated=esc(now_tz().strftime("%Y-%m-%d %H:%M")),
    )
    atomic_write_text(SITE_DIR / "weekly.html", page)


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
    ledger = load_json(DATA_DIR / "meta" / "evolution.json", {}) or {}
    garden = load_json(DATA_DIR / "garden.json", {}) or {}
    weekly_glob = list((DATA_DIR / "weekly").glob("*.json")) if (DATA_DIR / "weekly").exists() else []
    sediment_idx = load_json(DATA_DIR / "sediment" / "index.json", {}) or {}
    n_sediment = len(sediment_idx.get("entries") or {})
    panel_nav = (
        '<section><h2>🧭 体系面板</h2><nav class="days">'
        '<a href="evolution.html">🧬 进化面板</a>'
        '<a href="weekly.html">🗓 每周洞察</a>'
        '<a href="sediment.html">🌿 沉淀库</a>'
        '<a href="feed.xml">📡 RSS 订阅</a>'
        "</nav></section>"
    )

    # 首页：统计 + 面板导航 + 最新一天全文 + 归档导航
    chips = (
        f'<div class="chips">'
        f'<span class="chip">收录 {len(days)} 天</span>'
        f'<span class="chip">累计 {total_all} 条</span>'
        f'<span class="chip">最新 {esc(days[0]["date"])}</span>'
        f'<span class="chip">数据源 {len(days[0]["sources"])} 个</span>'
        f'<span class="chip">进化决策 {len(ledger.get("entries") or [])} 条</span>'
        f'<span class="chip">追踪主题 {len(garden.get("themes") or {})}</span>'
        f'<span class="chip">周报 {len(weekly_glob)} 期</span>'
        f'<span class="chip">沉淀 {n_sediment} 条</span>'
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
        body=chips + panel_nav + render_digest(days[0]) + archive_section,
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

    build_evolution_page()
    build_weekly_page()
    build_sediment_page()
    print(
        f"站点构建完成：{len(days)} 天日报 + feed.xml + 进化面板 + 周报页 + 沉淀库，输出到 _site/"
    )


if __name__ == "__main__":
    build()
