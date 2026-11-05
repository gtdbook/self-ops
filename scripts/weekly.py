"""周度合成：把最近 7 天日报提炼成「每周洞察」，并维护跨周主题知识花园。

产出（每周一早晨随 weekly workflow 运行）：
  data/weekly/<ISO周键>.json|.md   周报：主题聚类、Top 条目、源统计、进化回顾
  data/monthly/<YYYY-MM>.json|.md  月度回顾（每月首个周一自动合成上一个自然月）
  data/garden.json                 知识花园：跨周主题追踪，反复出现的主题权重增长，
                                   长期无人问津的主题 8 周后自动凋零清除
同时发布「🗓 每周洞察」issue（编号条目 + 投票映射，口味学习同样吃得到），
并把 8 天前的日报 issue、35 天前的周报 issue 自动归档关闭。
用法：python scripts/weekly.py [--dry-run]
"""

import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from common import (  # noqa: E402
    DATA_DIR,
    DIGEST_DIR,
    RAW_DIR,
    REPO_ROOT,
    SITE_URL,
    append_panel_comment,
    atomic_write_json,
    atomic_write_text,
    gh_api,
    load_config,
    load_json,
    md5_key,
    now_tz,
    tokenize_title,
)

WEEKLY_DIR = DATA_DIR / "weekly"
MONTHLY_DIR = DATA_DIR / "monthly"
GARDEN_PATH = DATA_DIR / "garden.json"

WEEKLY_LABEL = "weekly"
SECTION_BASE = {"resonance": 40, "rising": 25}


def load_prefs():
    return load_json(DATA_DIR / "prefs.json", {"sources": {}, "tokens": {}})


def git_commit(message):
    import subprocess

    def run(*args):
        subprocess.run(["git", *args], cwd=REPO_ROOT, check=True, capture_output=True)

    run("config", "user.name", "self-ops-bot")
    run("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    run("add", "data/weekly", "data/monthly", "data/garden.json")
    if subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=REPO_ROOT
    ).returncode == 0:
        return False
    run("commit", "-m", message)
    run("pull", "--rebase", "origin", "main")
    run("push")
    return True


# ---------------------------------------------------------------- 周度聚合


def week_days(cfg, days_back=7):
    """最近 days_back 天的日期键（昨天往前推，即完整的一周）。"""
    tz = cfg.get("timezone", "Asia/Shanghai")
    yesterday = now_tz(tz).date() - timedelta(days=1)
    return [(yesterday - timedelta(days=i)).isoformat() for i in range(days_back)]


def load_week(days):
    out = {}
    for day in days:
        path = DIGEST_DIR / f"{day}.json"
        if path.exists():
            d = load_json(path, {})
            if d.get("sources"):
                out[day] = d
    return out


def stars_of(item):
    """从 digest 元信息行里还原热度数值（HN 分数 / GitHub star）。"""
    m = re.search(r"⭐\s*(\d+)", item.get("meta") or "")
    return int(m.group(1)) if m else 0


def build_top_items(week_data, prefs, top_n=10):
    """跨天跨板块聚合去重，按合成热度分挑出本周 Top 条目。"""
    pool = {}
    for day, digest in sorted(week_data.items()):
        for sec_key, sec in digest.get("sources", {}).items():
            for it in sec.get("items", []):
                key = it.get("url") or md5_key(it.get("title", ""))
                rec = pool.setdefault(
                    key, {"days": set(), "sections": set(), "item": it}
                )
                rec["days"].add(day)
                rec["sections"].add(sec_key)
                if len(it.get("summary") or "") > len(rec["item"].get("summary") or ""):
                    rec["item"] = it

    tokens = prefs.get("tokens", {})
    scored = []
    for rec in pool.values():
        it = rec["item"]
        base = max((SECTION_BASE.get(s, 0) for s in rec["sections"]), default=0)
        heat = min(30.0, stars_of(it) * (0.15 if "hackernews" in rec["sections"] else 0.03))
        tok_bonus = max(-20.0, min(20.0, 8.0 * sum(
            tokens.get(t, 0.0) for t in set(tokenize_title(it.get("title", "")))
        )))
        score = base + heat + 5.0 * len(rec["days"]) + tok_bonus
        scored.append((score, rec))
    scored.sort(key=lambda x: x[0], reverse=True)

    out = []
    for score, rec in scored[:top_n]:
        it = rec["item"]
        out.append(
            {
                "title": it.get("title", ""),
                "url": it.get("url", ""),
                "meta": " · ".join(filter(None, [
                    f"本周出现 {len(rec['days'])} 天",
                    it.get("meta"),
                ])),
                "summary": it.get("summary", ""),
                "reason": it.get("reason"),
                "origin_source": it.get("origin_source", ""),
                "_score": round(score, 1),
            }
        )
    return out


def build_themes(week_data, wcfg):
    """主题词聚类：跨天加权统计标题词，选出本周主题及其代表条目。"""
    min_weight = wcfg.get("theme_min_weight", 4)
    top_n = wcfg.get("theme_top", 12)
    weight_of = {"resonance": 3, "rising": 2}

    per_day = []
    for day, digest in sorted(week_data.items()):
        counter = Counter()
        for sec_key, sec in digest.get("sources", {}).items():
            w = weight_of.get(sec_key, 1)
            for it in sec.get("items", []):
                for tok in set(tokenize_title(it.get("title", ""))):
                    counter[tok] += w
        per_day.append(counter)

    merged, distinct = Counter(), Counter()
    for c in per_day:
        merged.update(c)
        for tok in c:
            distinct[tok] += 1

    # 代表条目：包含该主题词、板块权重最高的条目（共振/飙升优先）
    item_weight = {}
    for day, digest in sorted(week_data.items()):
        for sec_key, sec in digest.get("sources", {}).items():
            w = weight_of.get(sec_key, 1)
            for it in sec.get("items", []):
                key = it.get("url") or it.get("title", "")
                if key not in item_weight or w > item_weight[key][0]:
                    item_weight[key] = (w, it)

    themes = []
    for tok, weight in merged.most_common():
        if len(themes) >= top_n:
            break
        if weight < min_weight or distinct[tok] < 2:
            continue
        reps, seen = [], set()
        for key, (w, it) in sorted(
            item_weight.items(), key=lambda x: x[1][0], reverse=True
        ):
            if tok in set(tokenize_title(it.get("title", ""))) and it["title"] not in seen:
                reps.append({"title": it["title"], "url": it.get("url", "")})
                seen.add(it["title"])
            if len(reps) >= 2:
                break
        themes.append({"token": tok, "weight": weight, "days": distinct[tok], "samples": reps})
    return themes


def source_stats(days, prev_days):
    """各源本周日均产量与上周对比（读原始记录，未截断的真实产量）。"""

    def mean_of(day_list, key):
        counts = []
        for d in day_list:
            rec = load_json(RAW_DIR / d / f"{key}.json", {})
            if rec:
                counts.append(rec.get("count", 0) or 0)
        return sum(counts) / len(counts) if counts else 0.0

    def errors_of(day_list, key):
        n = 0
        for d in day_list:
            rec = load_json(RAW_DIR / d / f"{key}.json", {})
            if rec and rec.get("errors"):
                n += 1
        return n

    keys = set()
    for d in days:
        if (RAW_DIR / d).exists():
            keys |= {p.stem for p in (RAW_DIR / d).glob("*.json")}
    stats = {}
    for key in sorted(keys):
        cur, prev = mean_of(days, key), mean_of(prev_days, key)
        stats[key] = {
            "mean": round(cur, 1),
            "prev_mean": round(prev, 1),
            "delta": round(cur - prev, 1),
            "error_days": errors_of(days, key),
        }
    return stats


# ---------------------------------------------------------------- 知识花园


def update_garden(themes, week_key, prune_weeks):
    """主题跨周累积：反复出现的主题加权、添加样本；8 周不见的主题凋零清除。"""
    garden = load_json(GARDEN_PATH, {"themes": {}})
    g = garden.setdefault("themes", {})
    for th in themes:
        cur = g.get(th["token"]) or {
            "first_week": week_key,
            "weeks": [],
            "hits": 0,
            "samples": [],
        }
        if week_key not in cur["weeks"]:
            cur["weeks"].append(week_key)
        cur["hits"] += th["weight"]
        cur["last_week"] = week_key
        titles = {s["title"] for s in cur.get("samples", [])}
        for s in th["samples"]:
            if s["title"] not in titles and len(cur["samples"]) < 3:
                cur["samples"].append(s)
                titles.add(s["title"])
        g[th["token"]] = cur

    def week_ord(wk):
        try:
            y, w = wk.split("-W")
            return datetime.fromisocalendar(int(y), int(w), 1).toordinal()
        except Exception:
            return 0
    cur_ord = week_ord(week_key)
    for tok in list(g):
        if cur_ord - week_ord(g[tok].get("last_week", "")) > prune_weeks * 7:
            del g[tok]

    garden["updated_at"] = now_tz().isoformat()
    garden["week"] = week_key
    return garden


# ---------------------------------------------------------------- 月度回顾


def maybe_monthly_rollup(yesterday_iso, wcfg):
    """每周一早晨检查：若上一个自然月已结束且未生成回顾，则合成月度回顾。"""
    yesterday = datetime.fromisoformat(yesterday_iso)
    if yesterday.day > 7:
        return None, None
    prev_month = (yesterday.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    out_md = MONTHLY_DIR / f"{prev_month}.md"
    if out_md.exists():
        return None, None

    days = sorted(p.stem for p in DIGEST_DIR.glob(f"{prev_month}-*.json"))
    if not days:
        return None, None
    week_data = {}
    for d in days:
        d_data = load_json(DIGEST_DIR / f"{d}.json", {})
        if d_data.get("sources"):
            week_data[d] = d_data

    themes = build_themes(week_data, {**wcfg, "theme_top": 8})
    top_items = build_top_items(week_data, load_prefs(), wcfg.get("monthly_top", 15))
    total = sum(
        len(s.get("items", []))
        for d in week_data.values()
        for s in d.get("sources", {}).values()
    )
    monthly = {
        "month": prev_month,
        "days": days,
        "generated_at": now_tz().isoformat(),
        "themes": themes,
        "top_items": top_items,
        "total_digest_items": total,
    }
    atomic_write_json(MONTHLY_DIR / f"{prev_month}.json", monthly)
    atomic_write_text(MONTHLY_DIR / f"{prev_month}.md", render_report(
        f"🗓 月度回顾 · {prev_month}", monthly, monthly=True
    ))
    return prev_month, monthly


# ---------------------------------------------------------------- 渲染


def render_report(title, report, monthly=False):
    lines = [f"# {title}", ""]
    gen = report.get("generated_at", "")[:16].replace("T", " ")
    lines.append(f"> 自动生成于 {gen} · 由 self-ops 周度合成驱动")
    lines.append("")

    themes = report.get("themes", [])
    if themes:
        lines.append("## 🌱 本期主题")
        lines.append("")
        for th in themes:
            rep = " / ".join(
                f"[{s['title']}]({s['url']})" if s.get("url") else s["title"]
                for s in th["samples"][:2]
            )
            lines.append(
                f"- **{th['token']}**（权重 {th['weight']} · 出现 {th['days']} 天）"
                + (f"：{rep}" if rep else "")
            )
        lines.append("")

    top = report.get("top_items", [])
    if top:
        lines.append("## 🏆 本期 Top 条目")
        lines.append("")
        for i, it in enumerate(top, 1):
            url = it.get("url")
            lines.append(f"{i}. [{it['title']}]({url})" if url else f"{i}. {it['title']}")
            if it.get("meta"):
                lines.append(f"   - {it['meta']}")
            if it.get("summary"):
                lines.append(f"   - {it['summary']}")
        lines.append("")

    stats = report.get("stats")
    if stats:
        lines.append("## 📊 源产量（本周日均 / 上周日均）")
        lines.append("")
        lines.append("| 源 | 本周日均 | 上周 | 变化 | 报错天数 |")
        lines.append("|---|---|---|---|---|")
        for key, s in stats.items():
            arrow = "↑" if s["delta"] > 0 else ("↓" if s["delta"] < 0 else "→")
            lines.append(
                f"| {key} | {s['mean']} | {s['prev_mean']} | {arrow}{abs(s['delta'])} | {s['error_days']} |"
            )
        lines.append("")

    if report.get("evolution_lines"):
        lines.append("## 🧬 本周进化回顾")
        lines.extend(f"- {l}" for l in report["evolution_lines"])
        lines.append("")

    g = report.get("garden")
    if g:
        lines.append(
            f"## 🌸 知识花园 · 累计追踪 **{g['total_themes']}** 个主题"
            f"（常青 {g['evergreen']} · 本周新增 {g['new_this_week']}）"
        )
        lines.append("")
        for tok in g["top"][:8]:
            lines.append(f"- {tok}")
        lines.append("")

    if monthly and report.get("total_digest_items"):
        lines.append(f"> 本月日报累计收录 **{report['total_digest_items']}** 条。")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- issue


def publish_issue(token, repo, week_key, report):
    title = f"🗓 每周洞察 · {week_key}"
    for issue in gh_api(
        f"/repos/{repo}/issues?state=all&labels={WEEKLY_LABEL}&per_page=30", token=token
    ) or []:
        if issue["title"] == title:
            print(f"[skip] {title} 已发布 (#{issue['number']})")
            return None
    if gh_api(f"/repos/{repo}/labels/{WEEKLY_LABEL}", token=token) is None:
        gh_api(
            f"/repos/{repo}/labels",
            token=token,
            method="POST",
            payload={"name": WEEKLY_LABEL, "color": "8250df", "description": "每周洞察周报"},
        )

    body = render_report(title, report)
    body += f"\n---\n\n🌐 [在线周报页]({SITE_URL}weekly.html) · [进化面板]({SITE_URL}evolution.html)\n"
    # 给 Top 条目附带投票映射（口味学习同样处理周报投票）
    votemap = {
        str(i): {"source": it.get("origin_source", ""), "title": it["title"]}
        for i, it in enumerate(report.get("top_items", []), 1)
    }
    if votemap:
        body += (
            "\n💬 **口味投票**：回复 `like 1 3` / `dislike 2`，周报条目同样参与口味学习。\n"
            "<!--votemap\n"
            + json.dumps(votemap, ensure_ascii=False).replace("--", "—")
            + "\n-->"
        )
    created = gh_api(
        f"/repos/{repo}/issues",
        token=token,
        method="POST",
        payload={"title": title, "body": body, "labels": [WEEKLY_LABEL]},
    )
    print(f"[ok] 已发布 {title} → issue #{created['number']}")
    return created["number"]


def housekeeping(token, repo, daily_keep=8, weekly_keep=35):
    """归档关闭过期 issue：日报 >daily_keep 天，周报 >weekly_keep 天。"""
    cutoff_daily = (now_tz() - timedelta(days=daily_keep)).date()
    cutoff_weekly = (now_tz() - timedelta(days=weekly_keep)).date()
    closed = 0
    for issue in gh_api(
        f"/repos/{repo}/issues?state=open&per_page=100", token=token
    ) or []:
        if issue.get("pull_request"):
            continue
        labels = {l["name"] for l in issue.get("labels", [])}
        created = datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00"))
        if "daily" in labels and created.date() <= cutoff_daily:
            reason = f"📦 自动归档：{issue['title']} 已沉淀至 data/digest，在线站点仍有存档页。"
        elif WEEKLY_LABEL in labels and created.date() <= cutoff_weekly:
            reason = f"📦 自动归档：{issue['title']} 已沉淀至 data/weekly。"
        else:
            continue
        gh_api(issue["comments_url"], token=token, method="POST", payload={"body": reason})
        gh_api(
            f"/repos/{repo}/issues/{issue['number']}",
            token=token,
            method="PATCH",
            payload={"state": "closed"},
        )
        closed += 1
    return closed


# ---------------------------------------------------------------- 主流程


def main():
    dry_run = "--dry-run" in sys.argv
    cfg = load_config()
    wcfg = cfg.get("weekly", {})
    ev = cfg.get("evolve", {})
    tz = cfg.get("timezone", "Asia/Shanghai")
    now = now_tz(tz)
    days = week_days(cfg, 7)
    prev_days = week_days(cfg, 14)[7:]
    iso = datetime.fromisoformat(days[0]).isocalendar()  # days[0] = 昨天（周日）
    week_key = f"{iso[0]}-W{iso[1]:02d}"

    week_data = load_week(days)
    if len(week_data) < 3:
        print(f"[warn] 本周只有 {len(week_data)} 天日报，不足合成（≥3 天才产出）")
        return

    prefs = load_prefs()
    themes = build_themes(week_data, wcfg)
    top_items = build_top_items(week_data, prefs, wcfg.get("top_items", 10))
    stats = source_stats(days, prev_days)

    # 进化回顾：本周账本条目 + 花园快照
    ledger = load_json(DATA_DIR / "meta" / "evolution.json", {"entries": []}) or {"entries": []}
    evolution_lines = [
        f"{e.get('target', '')}：{e.get('detail', '')}"
        for e in ledger.get("entries", [])
        if (e.get("ts") or "")[:10] >= days[-1]
    ]
    evolution_lines = evolution_lines[-10:] or ["本周无自动进化决策"]

    # 知识花园生长
    garden = update_garden(themes, week_key, wcfg.get("garden_prune_weeks", 8))
    evergreen = [t for t, v in garden["themes"].items() if len(v["weeks"]) >= 3]
    garden_summary = {
        "total_themes": len(garden["themes"]),
        "evergreen": len(evergreen),
        "new_this_week": sum(
            1 for v in garden["themes"].values() if v.get("first_week") == week_key
        ),
        "top": [
            f"{'🌸 ' if len(v.get('weeks', [])) >= 3 else ''}{t}"
            f"（追踪 {len(v.get('weeks', []))} 周 · 累计 {v.get('hits', 0)} 次）"
            for t, v in sorted(
                garden["themes"].items(), key=lambda x: -x[1].get("hits", 0)
            )
        ],
    }

    report = {
        "week": week_key,
        "days": sorted(week_data),
        "generated_at": now.isoformat(),
        "themes": themes,
        "top_items": top_items,
        "stats": stats,
        "evolution_lines": evolution_lines,
        "garden": garden_summary,
    }

    print(f"周报 {week_key}：主题 {len(themes)} · Top {len(top_items)} · 源 {len(stats)} 个")
    print(f"花园：{garden_summary['total_themes']} 主题（常青 {garden_summary['evergreen']}，新增 {garden_summary['new_this_week']}）")
    if dry_run:
        print(render_report(f"🗓 每周洞察 · {week_key}", report)[:1500])
        return

    # 月度回顾（跨月的第一期自动合成上月）
    prev_month, _ = maybe_monthly_rollup(days[0], wcfg)
    if prev_month:
        print(f"[ok] 已生成月度回顾 data/monthly/{prev_month}.md")

    WEEKLY_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(WEEKLY_DIR / f"{week_key}.json", report)
    atomic_write_text(WEEKLY_DIR / f"{week_key}.md", render_report(f"🗓 每周洞察 · {week_key}", report))
    atomic_write_json(GARDEN_PATH, garden)

    token = os.environ.get("GH_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print("[fatal] 需要 GH_TOKEN 与 GITHUB_REPOSITORY 环境变量")
        sys.exit(1)

    issue_num = publish_issue(token, repo, week_key, report)
    closed = housekeeping(token, repo, wcfg.get("daily_issue_keep_days", 8), wcfg.get("weekly_issue_keep_days", 35))

    pushed = git_commit(f"weekly: {week_key} 周报与知识花园（主题 {len(themes)}，Top {len(top_items)}）")
    print(f"[ok] 数据{'已提交推送' if pushed else '无变化'}；归档关闭 {closed} 个过期 issue")

    panel = [
        f"### 🗓 每周洞察 · {week_key}",
        "",
        f"- 主题：{'、'.join(t['token'] for t in themes[:6]) or '无'}",
        f"- Top 条目 {len(top_items)} 条 · 周报 issue "
        + (f"#{issue_num}" if issue_num else "已存在"),
        f"- 知识花园：{garden_summary['total_themes']} 主题（常青 {garden_summary['evergreen']}）",
        f"- 归档关闭 {closed} 个过期 issue",
    ]
    append_panel_comment(token, repo, "\n".join(panel))


if __name__ == "__main__":
    main()
