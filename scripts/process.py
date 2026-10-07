"""把当日 raw 数据加工成日报（JSON + Markdown），更新总索引并清理过期数据。"""

import json
import os
import shutil
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from common import (  # noqa: E402
    DATA_DIR,
    DIGEST_DIR,
    RAW_DIR,
    atomic_write_json,
    atomic_write_text,
    format_meta,
    load_config,
    now_tz,
    parse_day,
    strip_html,
    today_key,
)

SOURCE_META = {
    "hackernews": {"title": "Hacker News 热榜", "emoji": "💬"},
    "github_search": {"title": "GitHub 新星榜", "emoji": "🚀"},
    "blog_rss": {"title": "博客与技术媒体", "emoji": "📰"},
    "producthunt": {"title": "Product Hunt 新品", "emoji": "🎯"},
    "aihot": {"title": "AIHOT 精选", "emoji": "🔥"},
}


def _sort_key(key, item):
    if key == "hackernews":
        return item.get("score", 0)
    if key == "github_search":
        return item.get("stars", 0)
    if key == "blog_rss":
        return item.get("published_iso") or ""
    return 0


def build_sections(day, top_n):
    raw_dir = RAW_DIR / day
    sections = {}
    if not raw_dir.exists():
        return sections
    for path in sorted(raw_dir.glob("*.json")):
        key = path.stem
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        items = sorted(record.get("items", []), key=lambda x: _sort_key(key, x), reverse=True)
        items = items[:top_n]
        if not items:
            continue
        meta = SOURCE_META.get(key, {"title": key, "emoji": "📌"})
        sections[key] = {
            "title": meta["title"],
            "emoji": meta["emoji"],
            "errors": record.get("errors", [])[-3:],
            "items": [
                {
                    "title": it.get("title", ""),
                    "url": it.get("url", ""),
                    "meta": format_meta(key, it),
                    "summary": strip_html(it.get("summary") or it.get("description") or "", 200),
                    "reason": it.get("reason"),
                }
                for it in items
            ],
        }
    return sections


def render_markdown(day, generated_at, sections):
    lines = [f"# 📡 每日情报 · {day}", ""]
    total = sum(len(s["items"]) for s in sections.values())
    lines.append(f"> 自动生成于 {generated_at} · 共 {total} 条 · 由 self-ops 流水线驱动")
    lines.append("")
    for key, sec in sections.items():
        lines.append(f"## {sec['emoji']} {sec['title']}")
        lines.append("")
        for i, it in enumerate(sec["items"], 1):
            lines.append(f"{i}. [{it['title']}]({it['url']})")
            if it["meta"]:
                lines.append(f"   - {it['meta']}")
            if it.get("summary"):
                lines.append(f"   - {it['summary']}")
            if it.get("reason"):
                lines.append(f"   - 💡 {it['reason']}")
        lines.append("")
    return "\n".join(lines)


def rebuild_index():
    days = []
    for path in DIGEST_DIR.glob("*.json"):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        days.append(
            {
                "date": d["date"],
                "total": sum(len(s["items"]) for s in d.get("sources", {}).values()),
                "per_source": {k: len(s["items"]) for k, s in d.get("sources", {}).items()},
            }
        )
    days.sort(key=lambda x: x["date"], reverse=True)
    index = {"updated_at": now_tz().isoformat(), "days": days[:120]}
    atomic_write_json(DATA_DIR / "index.json", index)
    return index


def prune(retention_days, today):
    deadline = parse_day(today) - timedelta(days=retention_days)
    removed = []
    for base in (RAW_DIR, DIGEST_DIR):
        if not base.exists():
            continue
        for child in base.iterdir():
            try:
                if parse_day(child.name) < deadline:
                    if child.is_dir():
                        shutil.rmtree(child)
                    else:
                        child.unlink()
                    removed.append(child.name)
            except ValueError:
                continue
    return removed


def main():
    cfg = load_config()
    today = today_key(cfg)
    top_n = cfg.get("digest", {}).get("top_n_per_source", 15)

    # 若当日尚无 raw 数据（如刚跨日、抓取全部失败），回落到最近一天，避免误产空日报
    day = today
    if not (RAW_DIR / day).exists():
        candidates = sorted((p.name for p in RAW_DIR.iterdir() if p.is_dir()), reverse=True)
        if candidates:
            day = candidates[0]
            print(f"[warn] 当日无 raw 数据，回落到 {day}")
        else:
            print("[warn] 无任何 raw 数据，跳过处理")
            return

    sections = build_sections(day, top_n)
    if not sections:
        print(f"[warn] {day} 没有可用条目，跳过日报生成")
        return
    generated_at = now_tz().isoformat()
    digest = {"date": day, "generated_at": generated_at, "sources": sections}
    atomic_write_json(DIGEST_DIR / f"{day}.json", digest)
    atomic_write_text(DIGEST_DIR / f"{day}.md", render_markdown(day, generated_at, sections))

    index = rebuild_index()
    removed = prune(cfg.get("retention_days", 60), today)
    print(f"日报 {day}：{len(sections)} 个板块，共 {sum(len(s['items']) for s in sections.values())} 条")
    print(f"索引：{len(index['days'])} 天；清理过期数据 {len(removed)} 项")


if __name__ == "__main__":
    main()
