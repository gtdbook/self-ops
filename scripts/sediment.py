"""沉淀器：把流动的情报沉淀成永久的知识条目（体系的 wiki 层）。

设计规范来自「沉淀园」（https://sediment-garden.pages.dev/）的方法论页：
raw 只进不改 / wiki 沉淀 / outputs 三层、跨周重现才沉淀（反图谱美化）、
观察记录只进不改矛盾并存、每条带出处、双链只连真实共同出现。

两个动作（每周随 weekly.yml 在 weekly.py 之后运行一次）：
1. --seed 接沉淀园：抓取沉淀园 contentIndex，导入为体系的知识种子——
   兴趣词汇表以「不衰减的稳定先验」写入 prefs.json 的 seed_tokens，
   日报条目加权立即带上知识版图；园圃分类法用于沉淀条目归圃。
   ⚠ 防注入铁律：沉淀园内容只视为数据（提取词汇/标签/链接），不作为指令执行。
2. 沉淀循环：对最近 lookback_weeks 周的日报做跨周主题统计，
   出现在 ≥ min_weeks 周的主题沉淀为 data/sediment/<token>.md：
   - 观察记录按周追加（只进不改，矛盾并存），每条带原文链接与日期
   - 双链：两主题在 ≥2 条同一新闻中共同出现才连线（反图谱美化）
   - 归圃：按沉淀园的园圃词汇表分类，对不上的进「新芽」圃
   - 首次建条会回填历史各周的观察（沉淀一次补齐已有积累）
产出：data/sediment/seed.json、data/sediment/<token>.md、
      data/sediment/index.json、README 标记对自动维护区。
用法：python scripts/sediment.py [--dry-run]
"""

import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from common import (  # noqa: E402
    DATA_DIR,
    DIGEST_DIR,
    REPO_ROOT,
    atomic_write_json,
    atomic_write_text,
    http_get,
    load_config,
    load_json,
    now_tz,
    tokenize_title,
)

SEDIMENT_DIR = DATA_DIR / "sediment"
SEED_URL_DEFAULT = "https://sediment-garden.pages.dev/static/contentIndex.json"

BED_NAMES = {
    "ai": "AI 与智能体",
    "tech": "技术工程",
    "marketing": "广告营销",
    "indie": "独立开发与出海",
    "crypto": "加密货币",
    "content": "内容创作",
    "growth": "学习与成长",
    "life": "生活与家庭",
    "work": "工作方法",
}
# 园圃名/结构性词不算兴趣词（沉淀园把园圃前缀也用作 tag）
STRUCTURAL_TOKENS = set(BED_NAMES) | {"index", "readme", "导航", "导读"}
SECTION_WEIGHT = {"resonance": 3, "rising": 2}


def git_commit(message):
    import subprocess

    def run(*args):
        result = subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"git {' '.join(args)} 失败: {(result.stderr or result.stdout or '').strip()[:300]}"
            )

    run("config", "user.name", "self-ops-bot")
    run("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    # 进化账本（sediment 决策留痕）一并提交，避免留下未暂存变更阻塞后续 rebase
    run("add", "data/sediment", "data/prefs.json", "data/meta/evolution.json", "README.md")
    if subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=REPO_ROOT
    ).returncode == 0:
        return False
    run("commit", "-m", message)
    run("pull", "--rebase", "origin", "main")
    run("push")
    return True


# ---------------------------------------------------------------- 第一步：接沉淀园


def fetch_seed(scfg):
    """抓沉淀园 contentIndex → 精简种子文件 + 园圃词汇表 + 兴趣词先验。"""
    url = scfg.get("seed_url", SEED_URL_DEFAULT)
    status, body = http_get(url, timeout=40, retries=2)
    raw = json.loads(body.decode("utf-8", "replace"))
    scfg_max = scfg.get("max_seed_tokens", 40)

    entries, bed_vocab = [], defaultdict(Counter)
    vocab = Counter()
    for slug, v in raw.items():
        if slug.startswith("tags/") or slug.startswith("assets/") or "/" not in slug:
            continue
        bed = slug.split("/", 1)[0]
        if bed not in BED_NAMES:
            continue
        tags = [t for t in v.get("tags", []) if len(t) >= 3]
        toks = set(tokenize_title(v.get("title", "")))
        entries.append({
            "slug": slug,
            "title": v.get("title", ""),
            "bed": bed,
            "bed_name": BED_NAMES[bed],
            "tags": tags,
            "url": f"https://sediment-garden.pages.dev/#/{slug}",
        })
        # 标签是库主人自己策展的关键词，权重高于标题词；园圃名等结构性词除外
        for t in tags:
            t = t.lower()
            if t in STRUCTURAL_TOKENS:
                continue
            bed_vocab[bed][t] += 1.0
            vocab[t] += 1.0
        for t in toks:
            if t in STRUCTURAL_TOKENS:
                continue
            bed_vocab[bed][t] += 0.35
            vocab[t] += 0.35

    max_f = max(vocab.values()) if vocab else 1.0
    seed_tokens = {
        t: round(min(0.9, 0.5 + 0.4 * (f / max_f)), 2)
        for t, f in vocab.most_common(scfg_max)
        if f >= 2
    }
    seed = {
        "source": url,
        "fetched_at": now_tz().isoformat(),
        "articles": len(entries),
        "beds": {b: sum(1 for e in entries if e["bed"] == b) for b in BED_NAMES},
        "entries": entries,
        "bed_vocab": {b: dict(c.most_common(80)) for b, c in bed_vocab.items()},
        "seed_tokens": seed_tokens,
    }
    return seed


def apply_seed_tokens(prefs, seed_tokens):
    """兴趣词写入 prefs.json 的稳定先验层（不参与逐日衰减）。"""
    prefs["seed_tokens"] = seed_tokens
    prefs["seed_updated_at"] = now_tz().isoformat()
    atomic_write_json(DATA_DIR / "prefs.json", prefs)


# ---------------------------------------------------------------- 第二步：跨周统计


def week_counters(lookback_weeks):
    """最近 N 个 ISO 周：每周的主题词 Counter（周内同条去重）。"""
    buckets = defaultdict(dict)  # week_key -> day -> Counter
    for path in DIGEST_DIR.glob("*.json"):
        d = load_json(path, {}) or {}
        day = d.get("date") or path.stem
        try:
            iso = datetime.fromisoformat(day).isocalendar()
        except Exception:
            continue
        wk = f"{iso[0]}-W{iso[1]:02d}"
        counter = Counter()
        for sec_key, sec in d.get("sources", {}).items():
            w = SECTION_WEIGHT.get(sec_key, 1)
            for it in sec.get("items", []):
                for tok in set(tokenize_title(it.get("title", ""))):
                    counter[tok] += w
        if counter:
            buckets[wk][day] = counter

    def wk_ord(wk):
        y, w = wk.split("-W")
        return datetime.fromisocalendar(int(y), int(w), 1).toordinal()

    weeks = sorted(buckets, key=wk_ord)[-lookback_weeks:]
    merged = {}
    for wk in weeks:
        c = Counter()
        for day_counter in buckets[wk].values():
            c.update(day_counter)
        merged[wk] = c
    return merged, weeks[-1] if weeks else None


def week_items(lookback_weeks):
    """最近 N 周：week_key -> 可引用条目（标题/url/源/日期，带板块权重）。"""
    out = defaultdict(list)
    for path in DIGEST_DIR.glob("*.json"):
        d = load_json(path, {}) or {}
        day = d.get("date") or path.stem
        try:
            iso = datetime.fromisoformat(day).isocalendar()
        except Exception:
            continue
        wk = f"{iso[0]}-W{iso[1]:02d}"
        for sec_key, sec in d.get("sources", {}).items():
            w = SECTION_WEIGHT.get(sec_key, 1)
            for it in sec.get("items", []):
                out[wk].append({
                    "title": it.get("title", ""),
                    "url": it.get("url", ""),
                    "source": sec.get("title", sec_key),
                    "date": day,
                    "weight": w,
                    "toks": set(tokenize_title(it.get("title", ""))),
                })
    return out


def classify_bed(token, seed):
    """按沉淀园园圃词汇表归圃（标签为强证据、标题词为弱证据；支持单复数回退）。"""
    def lookup(t, vc):
        if t in vc:
            return vc[t]
        if t.endswith("s") and not t.endswith("ss") and t[:-1] in vc:
            return vc[t[:-1]]
        return 0.0

    best_bed, best_f = None, 0.0
    for bed, vc in (seed.get("bed_vocab") or {}).items():
        f = lookup(token, vc)
        if f > best_f:
            best_bed, best_f = bed, f
    if best_bed and best_f >= 0.35:  # 任一标题级证据即归类，标签级更强
        return BED_NAMES[best_bed]
    return "未归类"


def observations_for(token, items_by_week, obs_per_week):
    """某主题在各周的观察：含该词（或其复数形式）的条目按板块权重排序取前 N（周内按 URL 去重）。"""
    variants = {token, token + "s"}
    obs = []
    for wk in sorted(items_by_week):
        seen_urls, matched = set(), []
        for it in items_by_week[wk]:
            if (it["toks"] & variants) and it.get("url") and it["url"] not in seen_urls:
                seen_urls.add(it["url"])
                matched.append(it)
        matched.sort(key=lambda x: -x["weight"])
        for it in matched[:obs_per_week]:
            obs.append({
                "week": wk,
                "date": it["date"],
                "title": it["title"],
                "url": it["url"],
                "source": it["source"],
            })
    return obs


def cooccurrence_links(entry_tokens, items_by_week, min_co):
    """双链：两个主题在 ≥ min_co 条同一新闻中共同出现才连线。"""
    co = Counter()
    for items in items_by_week.values():
        for it in items:
            hits = [t for t in entry_tokens if t in it["toks"]]
            for i in range(len(hits)):
                for j in range(i + 1, len(hits)):
                    co[tuple(sorted((hits[i], hits[j])))] += 1
    links = defaultdict(dict)
    for (a, b), n in co.items():
        if n >= min_co:
            links[a][b] = n
            links[b][a] = n
    return links


# ---------------------------------------------------------------- 渲染


def render_entry_md(token, entry, links):
    lines = [
        "---",
        "type: theme",
        f"token: {token}",
        f"bed: {entry['bed']}",
        f"first_week: {entry['first_week']}",
        f"last_week: {entry['last_week']}",
        f"weeks: {entry['weeks']}",
        f"hits: {entry['hits']}",
        f"status: {entry['status']}",
        "---",
        "",
        f"# {token}",
        "",
        f"> 沉淀判据：跨 ≥2 周反复出现。观察记录**只进不改**、矛盾并存不终审；"
        f"每条带出处。最近沉淀 {entry['last_week']}",
        "",
        "## 观察记录",
        "",
    ]
    cur_week = None
    for o in entry["observations"]:
        if o["week"] != cur_week:
            cur_week = o["week"]
            lines.append(f"### {cur_week}")
            lines.append("")
        lines.append(
            f"- [{o['title']}]({o['url']})"
            f" — {o['source']} · {o['date']}"
        )
    lines.append("")
    lines.append("## 相关页（真实共同出现 ≥2 条）")
    lines.append("")
    real_links = links.get(token) or {}
    if real_links:
        for t, n in sorted(real_links.items(), key=lambda x: -x[1])[:8]:
            lines.append(f"- [[{t}]]（共同出现 {n} 条）")
    else:
        lines.append("（暂无——不为孤立页强行连线）")
    lines.append("")
    return "\n".join(lines)


def update_readme_marker(stats_lines):
    """标记对模式：只替换 README 中 sediment 标记区，其余一字不动。"""
    path = REPO_ROOT / "README.md"
    text = path.read_text(encoding="utf-8")
    block = "<!-- sediment:start -->\n" + "\n".join(stats_lines) + "\n<!-- sediment:end -->"
    new_text, n = re.subn(r"<!-- sediment:start -->.*?<!-- sediment:end -->", block, text, flags=re.S)
    if n == 0:  # 标记对不存在则追加到文末
        new_text = text.rstrip() + "\n\n" + block + "\n"
    if new_text == text:
        return False
    path.write_text(new_text, encoding="utf-8")
    return True


# ---------------------------------------------------------------- 主流程


def main():
    dry_run = "--dry-run" in sys.argv
    cfg = load_config()
    scfg = cfg.get("sediment", {})
    lookback = scfg.get("lookback_weeks", 6)
    min_weeks = scfg.get("min_weeks", 2)
    max_new = scfg.get("max_new_entries", 12)
    obs_per_week = scfg.get("obs_per_week", 3)
    min_co = scfg.get("min_cooccurrence", 2)
    today = now_tz(cfg.get("timezone", "Asia/Shanghai")).strftime("%Y-%m-%d")
    ts = now_tz().isoformat()

    prefs = load_json(DATA_DIR / "prefs.json", {}) or {}
    ledger = load_json(DATA_DIR / "meta" / "evolution.json", {}) or {"entries": []}
    entries_out = list(ledger.get("entries", []))
    SEDIMENT_DIR.mkdir(parents=True, exist_ok=True)

    # ---- 1) 接沉淀园（失败不阻塞沉淀） ----
    seed = load_json(SEDIMENT_DIR / "seed.json", {}) or {}
    try:
        fresh = fetch_seed(scfg)
        if fresh["articles"]:
            seed = fresh
            print(
                f"[ok] 沉淀园种子：{fresh['articles']} 篇 · "
                f"{len(fresh['seed_tokens'])} 个兴趣词 · "
                f"园圃 {sum(1 for b, n in fresh['beds'].items() if n)} 个"
            )
            entries_out.append({
                "ts": ts, "kind": "sediment_seed", "target": "prefs.seed_tokens",
                "detail": f"导入沉淀园 {fresh['articles']} 篇，植入 {len(fresh['seed_tokens'])} 个稳定兴趣词",
            })
            if not dry_run:
                atomic_write_json(SEDIMENT_DIR / "seed.json", seed)
                apply_seed_tokens(prefs, fresh["seed_tokens"])
        else:
            print("[warn] 沉淀园种子为空，沿用上次种子")
    except Exception as e:
        print(f"[warn] 沉淀园种子获取失败（沿用上次）：{e}")

    # ---- 2) 沉淀循环 ----
    counters, latest_week = week_counters(lookback)
    if not counters:
        print("[warn] 无日报数据，跳过沉淀")
        return
    weeks_seen = Counter()
    for wk, c in counters.items():
        for tok in c:
            weeks_seen[tok] += 1
    total_weight = Counter()
    for c in counters.values():
        total_weight.update(c)

    def singular_of(tok):
        return tok[:-1] if tok.endswith("s") and not tok.endswith("ss") else None

    persistent = []
    for tok, w in total_weight.most_common(80):
        if weeks_seen[tok] < min_weeks:
            continue
        sing = singular_of(tok)
        # 单复数只沉淀一条：复数形式让位给同样跨周重现的单数形式
        if sing and weeks_seen.get(sing, 0) >= min_weeks:
            continue
        persistent.append((tok, w))
    pending = [
        {"token": tok, "weight": w, "weeks": weeks_seen[tok]}
        for tok, w in total_weight.most_common(40)
        if weeks_seen[tok] == 1
    ][:20]

    index = load_json(SEDIMENT_DIR / "index.json", {}) or {}
    existing = index.get("entries", {}) or {}
    items_by_week = week_items(lookback)

    # 双链基于本次在库的全部主题
    entry_tokens = sorted({tok for tok, _ in persistent} | set(existing))
    links = cooccurrence_links(entry_tokens, items_by_week, min_co)

    created, updated, retired = [], [], []
    for tok, w in persistent:
        obs = observations_for(tok, items_by_week, obs_per_week)
        if not obs:
            continue
        old = existing.get(tok)
        if old:
            if old.get("last_week") == latest_week:
                continue  # 本周已沉淀过，观察只进不改
            merged_obs = old.get("observations", []) + [
                o for o in obs if o["week"] > old.get("last_week", "")
            ]
            weeks = len({o["week"] for o in merged_obs})
            status = "常青" if weeks >= 3 else "活跃"
            entry = {
                "bed": old.get("bed", classify_bed(tok, seed)),
                "first_week": old.get("first_week", merged_obs[0]["week"]),
                "last_week": latest_week,
                "weeks": weeks,
                "hits": w,
                "status": status,
                "observations": merged_obs[-60:],
            }
            updated.append(tok)
        else:
            if len(created) >= max_new:
                continue
            entry = {
                "bed": classify_bed(tok, seed),
                "first_week": obs[0]["week"],
                "last_week": latest_week,
                "weeks": len({o["week"] for o in obs}),
                "hits": w,
                "status": "常青" if len({o["week"] for o in obs}) >= 3 else "活跃",
                "observations": obs[-60:],
            }
            created.append(tok)
        existing[tok] = entry
        if not dry_run:
            atomic_write_text(SEDIMENT_DIR / f"{tok}.md", render_entry_md(tok, entry, links))

    # 本周未再出现的旧条目标记休眠（条目永不删除——沉淀不蒸发）
    for tok, e in existing.items():
        if e.get("last_week") != latest_week and e.get("status") != "休眠":
            e["status"] = "休眠"
            retired.append(tok)
            if not dry_run:
                atomic_write_text(SEDIMENT_DIR / f"{tok}.md", render_entry_md(tok, e, links))

    # 去掉已沉淀完成的主题，剩余的进新芽圃
    pending = [p for p in pending if p["token"] not in existing]

    index_out = {
        "updated_at": ts,
        "latest_week": latest_week,
        "entries": existing,
        "links": {t: dict(links.get(t, {})) for t in existing if links.get(t)},
        "pending": pending,
        "beds": {
            BED_NAMES.get(b, b): sorted(t for t, e in existing.items() if e["bed"] == BED_NAMES.get(b, b))
            for b in {e["bed"] for e in existing.values()}
        },
        "seed_summary": {
            "source": seed.get("source"),
            "fetched_at": (seed.get("fetched_at") or "")[:10],
            "articles": seed.get("articles", 0),
            "tokens": len(seed.get("seed_tokens", {})),
        },
    }

    stats_lines = [
        f"**🌿 沉淀库**（自动维护区，勿手改）· 更新于 {today}",
        f"- 沉淀条目 **{len(existing)}** 条（"
        + " · ".join(f"{bed} {len(ts_)}" for bed, ts_ in sorted(index_out['beds'].items(), key=lambda x: -len(x[1])))
        + "）",
        f"- 新芽候选 {len(pending)} 个 · 双链 "
        f"{sum(len(v) for v in index_out['links'].values()) // 2} 条",
        f"- 沉淀园种子：{seed.get('articles', 0)} 篇 · 兴趣词 {len(seed.get('seed_tokens', {}))} 个"
        f"（[sediment-garden](https://sediment-garden.pages.dev/)）",
        f"- 知识版图见在线站点 [🌿 沉淀库](https://gtdbook.github.io/self-ops/sediment.html)",
    ]

    changed = bool(created or updated or retired)
    print(
        f"沉淀：新建 {len(created)}（{'、'.join(created[:6])}）· 追加 {len(updated)} · 休眠 {len(retired)}"
        f" · 新芽 {len(pending)}"
    )
    if dry_run:
        print("[dry-run] 完成")
        return

    for tok in created:
        entries_out.append({
            "ts": ts, "kind": "sediment_new", "target": tok,
            "detail": f"跨 {existing[tok]['weeks']} 周重现，沉淀入「{existing[tok]['bed']}」圃",
        })
    if updated:
        entries_out.append({
            "ts": ts, "kind": "sediment_update", "target": "、".join(updated[:8]),
            "detail": f"追加 {latest_week} 周观察记录",
        })
    atomic_write_json(SEDIMENT_DIR / "index.json", index_out)
    atomic_write_json(
        DATA_DIR / "meta" / "evolution.json",
        {"updated_at": ts, "entries": entries_out[-200:]},
    )
    readme_changed = update_readme_marker(stats_lines)
    pushed = git_commit(
        f"sediment: 沉淀 {latest_week}（新建 {len(created)} · 追加 {len(updated)} · 休眠 {len(retired)}）"
    )
    print(f"[ok] 沉淀库{'已提交推送' if pushed else '无变化'}；README 标记区{'已更新' if readme_changed else '无变化'}")


if __name__ == "__main__":
    main()
