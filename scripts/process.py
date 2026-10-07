"""把当日 raw 数据加工成日报（JSON + Markdown）。

板块构成：🔗 多源共振（跨源同一事件聚类）→ 📈 飙升榜（HN 分数轨迹斜率）
→ 各数据源板块（排序受 data/prefs.json 口味权重影响）。
最后更新总索引并清理过期数据。
"""

import json
import os
import shutil
import sys
from datetime import datetime, timedelta

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
    tokenize_title,
)

SOURCE_META = {
    "hackernews": {"title": "Hacker News 热榜", "emoji": "💬"},
    "github_search": {"title": "GitHub 新星榜", "emoji": "🚀"},
    "blog_rss": {"title": "博客与技术媒体", "emoji": "📰"},
    "producthunt": {"title": "Product Hunt 新品", "emoji": "🎯"},
    "aihot": {"title": "AIHOT 精选", "emoji": "🔥"},
    "resonance": {"title": "多源共振", "emoji": "🔗"},
    "rising": {"title": "飙升榜", "emoji": "📈"},
}

# 参与跨源共振聚类的源（github 仓库名与新闻标题形态差异太大，不参与）
RESONANCE_SOURCES = ("hackernews", "blog_rss", "producthunt", "aihot")


def _score_value(key, item):
    if key == "hackernews":
        return item.get("score", 0)
    if key == "github_search":
        return item.get("stars", 0)
    return 0


def _sort_key(key, item):
    if key == "hackernews":
        return item.get("score", 0)
    if key == "github_search":
        return item.get("stars", 0)
    if key == "blog_rss":
        return item.get("published_iso") or ""
    return ""


def _hours_between(iso_a, iso_b):
    try:
        a = datetime.fromisoformat(iso_a)
        b = datetime.fromisoformat(iso_b)
        return (b - a).total_seconds() / 3600
    except Exception:
        return None


def load_prefs():
    path = DATA_DIR / "prefs.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"sources": {}, "tokens": {}}


# ---------------------------------------------------------------- 合成板块


def build_resonance(raw_items, evolve_cfg):
    """跨源标题相似聚类：≥2 个不同源报道同一事件即为共振条目。"""
    threshold = evolve_cfg.get("resonance_min_jaccard", 0.45)
    entries = []
    for key in RESONANCE_SOURCES:
        for it in raw_items.get(key, []):
            if it.get("title"):
                entries.append((key, it))
    n = len(entries)
    if n < 2 or n > 1500:
        return []
    toksets = [set(tokenize_title(it["title"])) for _, it in entries]
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        if not toksets[i]:
            continue
        for j in range(i + 1, n):
            if not toksets[j]:
                continue
            inter = len(toksets[i] & toksets[j])
            if inter < 2:
                continue
            jac = inter / len(toksets[i] | toksets[j])
            # 高相似（如产品名完全一致的短标题）放宽 token 数；普通情况要求 ≥3 个共同词
            if jac >= threshold and (inter >= 3 or jac >= 0.7):
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[rj] = ri

    clusters = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(i)

    out = []
    for members in clusters.values():
        sources = {entries[m][0] for m in members}
        if len(sources) < 2:
            continue
        members.sort(
            key=lambda m: (entries[m][0] == "hackernews", _score_value(*entries[m])),
            reverse=True,
        )
        rep_key, rep = entries[members[0]]
        labels = []
        for m in members:
            label = SOURCE_META.get(entries[m][0], {}).get("title", entries[m][0])
            if label not in labels:
                labels.append(label)
        summary = strip_html(rep.get("summary") or "", 160)
        if not summary and len(members) > 1:
            summary = "另见：" + entries[members[1]][1].get("title", "")
        out.append(
            {
                "title": rep.get("title", ""),
                "url": rep.get("url", ""),
                "meta": " · ".join(labels),
                "summary": summary,
                "reason": None,
                "origin_source": rep_key,
                "_rank": (len(sources), _score_value(rep_key, rep)),
            }
        )
    out.sort(key=lambda x: x["_rank"], reverse=True)
    for it in out:
        it.pop("_rank", None)
    return out[: evolve_cfg.get("resonance_top_n", 8)]


def build_rising(hn_items, evolve_cfg):
    """HN 分数轨迹斜率：过去数小时涨分最快的条目。"""
    min_gain = evolve_cfg.get("rising_min_gain", 20)
    rows = []
    for it in hn_items:
        hist = it.get("history") or []
        if len(hist) < 2:
            continue
        first, last = hist[0], hist[-1]
        span_h = _hours_between(first.get("t"), last.get("t"))
        if span_h is None or span_h < 0.5:
            continue
        gain = (last.get("score") or 0) - (first.get("score") or 0)
        if gain < min_gain:
            continue
        rows.append((gain, span_h, it))
    rows.sort(key=lambda x: x[0], reverse=True)
    return [
        {
            "title": it.get("title", ""),
            "url": it.get("url", ""),
            "meta": f"+{gain} 分 / {span_h:.1f} 小时 · 现 ⭐ {it.get('score', 0)}",
            "summary": "",
            "reason": None,
            "origin_source": "hackernews",
        }
        for gain, span_h, it in rows[: evolve_cfg.get("rising_top_n", 10)]
    ]


# ---------------------------------------------------------------- 主流程


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
    removed = 0
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
                    removed += 1
            except ValueError:
                continue
    return removed


def main():
    cfg = load_config()
    evolve = cfg.get("evolve", {})
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

    records, raw_items = {}, {}
    for path in sorted((RAW_DIR / day).glob("*.json")):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        records[path.stem] = rec
        raw_items[path.stem] = rec.get("items", [])
    if not raw_items:
        print(f"[warn] {day} 没有可用条目，跳过日报生成")
        return

    prefs = load_prefs()
    src_pref = prefs.get("sources", {})
    tokens = prefs.get("tokens", {})

    sections = {}
    resonance = build_resonance(raw_items, evolve)
    if resonance:
        sections["resonance"] = {
            "title": SOURCE_META["resonance"]["title"],
            "emoji": "🔗",
            "errors": [],
            "items": resonance,
        }
        print(f"[i] 多源共振：{len(resonance)} 组")
    rising = build_rising(raw_items.get("hackernews", []), evolve)
    if rising:
        sections["rising"] = {
            "title": SOURCE_META["rising"]["title"],
            "emoji": "📈",
            "errors": [],
            "items": rising,
        }
        print(f"[i] 飙升榜：{len(rising)} 条")

    # 各源板块：按口味权重排序板块；HN/GitHub 条目按 (原始分 + 关键词加成) 重排
    boost_factor = evolve.get("boost_factor", 40)
    for key in sorted(
        (k for k in raw_items if k in SOURCE_META and k not in ("resonance", "rising")),
        key=lambda k: -src_pref.get(k, 1.0),
    ):
        items = list(raw_items[key])
        if key in ("hackernews", "github_search") and tokens:
            def boosted(it, _key=key):
                base = _score_value(_key, it)
                bonus = sum(tokens.get(t, 0) for t in set(tokenize_title(it.get("title", ""))))
                return base + boost_factor * max(min(bonus, 5), -3)

            items.sort(key=boosted, reverse=True)
        else:
            items.sort(key=lambda x: _sort_key(key, x), reverse=True)
        items = items[:top_n]
        if not items:
            continue
        meta = SOURCE_META.get(key, {"title": key, "emoji": "📌"})
        sections[key] = {
            "title": meta["title"],
            "emoji": meta["emoji"],
            "errors": records.get(key, {}).get("errors", [])[-3:],
            "items": [
                {
                    "title": it.get("title", ""),
                    "url": it.get("url", ""),
                    "meta": format_meta(key, it),
                    "summary": strip_html(it.get("summary") or it.get("description") or "", 200),
                    "reason": it.get("reason"),
                    "origin_source": key,
                }
                for it in items
            ],
        }

    generated_at = now_tz().isoformat()
    digest = {"date": day, "generated_at": generated_at, "sources": sections}
    atomic_write_json(DIGEST_DIR / f"{day}.json", digest)
    atomic_write_text(DIGEST_DIR / f"{day}.md", render_markdown(day, generated_at, sections))

    index = rebuild_index()
    removed = prune(cfg.get("retention_days", 60), today)
    print(
        f"日报 {day}：{len(sections)} 个板块，"
        f"共 {sum(len(s['items']) for s in sections.values())} 条"
        + (f"（口味权重生效：{len(tokens)} 个 token）" if tokens else "")
    )
    print(f"索引：{len(index['days'])} 天；清理过期数据 {removed} 项")


if __name__ == "__main__":
    main()
