"""内生进化：不依赖人工投票的自我适应引擎。

三个机制（每晨随 evolve 运行一次）：
1. 源适应度 —— 用最近 N 天原始抓取记录评估每个数据源的产量/在场率/报错率，
   得出 fitness ∈ [0,1]。持续低迷的源先降权，仍无起色则自动禁用并安排探针
   复活测试；禁用源到期自动重试，恢复健康后逐步回权。每次决策写入进化账本。
2. 主题自学 —— 从最近 N 天日报的「多源共振」「飙升榜」标题中提炼高频主题词，
   提升其 prefs.json 关键词权重，影响后续日报的条目加权排序（进化闭环）。
3. 自然衰减 —— 所有关键词权重逐日衰减回归中性，避免一次学习永久生效。

产出：data/meta/fitness.json（适应度）、data/evolution.json（进化账本）、
      data/prefs.json（更新后的口味偏好，次日流水线即生效）。
用法：python scripts/self_evolve.py [--dry-run]
"""

import json
import os
import sys
from collections import Counter, defaultdict
from datetime import timedelta

sys.path.insert(0, os.path.dirname(__file__))
from common import (  # noqa: E402
    DATA_DIR,
    DIGEST_DIR,
    RAW_DIR,
    REPO_ROOT,
    atomic_write_json,
    load_config,
    load_json,
    now_tz,
    parse_day,
    tokenize_title,
)

META_DIR = DATA_DIR / "meta"
LEDGER_MAX = 200
TOKEN_CLAMP = 5.0
TOKEN_DROP = 0.15
REAL_SOURCES = ("hackernews", "github_search", "blog_rss", "producthunt", "aihot")

LEDGER_EMOJI = {
    "source_demote": "📉",
    "source_disable": "⛔",
    "source_probe": "🧪",
    "source_reenable": "✅",
    "source_heal": "🌱",
    "token_promote": "🏷️",
}


def load_fitness():
    return load_json(
        META_DIR / "fitness.json", {"updated_at": None, "sources": {}}
    ) or {"updated_at": None, "sources": {}}


def load_ledger():
    return load_json(META_DIR / "evolution.json", {"updated_at": None, "entries": []}) or {
        "updated_at": None,
        "entries": [],
    }


def load_prefs():
    prefs = load_json(
        DATA_DIR / "prefs.json",
        {"sources": {k: 1.0 for k in REAL_SOURCES}, "tokens": {}, "seen_comment_ids": []},
    )
    return prefs or {
        "sources": {k: 1.0 for k in REAL_SOURCES},
        "tokens": {},
        "seen_comment_ids": [],
    }


def git_commit(message):
    import subprocess

    def run(*args):
        subprocess.run(["git", *args], cwd=REPO_ROOT, check=True, capture_output=True)

    run("config", "user.name", "self-ops-bot")
    run("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    run("add", "data/prefs.json", "data/meta/fitness.json", "data/meta/evolution.json")
    if subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=REPO_ROOT
    ).returncode == 0:
        return False
    run("commit", "-m", message)
    run("pull", "--rebase", "origin", "main")
    run("push")
    return True


# ---------------------------------------------------------------- 源适应度


def compute_fitness(window_days):
    """逐源统计最近 window_days 天：在场天数、日均产量、报错天数 → fitness。"""
    days = sorted(
        (p.name for p in RAW_DIR.iterdir() if p.is_dir()), reverse=True
    )[:window_days]
    stats = defaultdict(lambda: {"days_present": 0, "counts": [], "error_days": 0})
    for day in days:
        for path in (RAW_DIR / day).glob("*.json"):
            rec = load_json(path, {})
            key = path.stem
            st = stats[key]
            st["days_present"] += 1
            st["counts"].append(rec.get("count", 0) or 0)
            if rec.get("errors"):
                st["error_days"] += 1
    window = max(len(days), 1)
    out = {}
    for key, st in stats.items():
        mean_items = sum(st["counts"]) / len(st["counts"]) if st["counts"] else 0.0
        presence = st["days_present"] / window
        yield_f = min(1.0, mean_items / 10.0) if st["counts"] else 0.0
        error_f = 1.0 / (1.0 + 0.25 * st["error_days"])
        out[key] = {
            "fitness": round(yield_f * presence * error_f, 3),
            "mean_items": round(mean_items, 1),
            "days_present": st["days_present"],
            "error_days": st["error_days"],
            "window_days": window,
        }
    return out


def evolve_sources(fitness_state, fresh, fcfg, today):
    """按适应度做降权/禁用/探针/回权决策，返回 (状态, 决策行列表)。"""
    disable_below = fcfg.get("disable_below", 0.25)
    disable_streak = fcfg.get("disable_streak", 3)
    probe_days = fcfg.get("probe_days", 7)
    heal_above = fcfg.get("heal_above", 0.6)
    decisions = []
    state = fitness_state.get("sources", {})

    for key, m in fresh.items():
        prev = state.get(key, {"weight_hint": 1.0, "streak_low": 0, "enabled": True})
        if prev.get("enabled") is False:
            probe_at = prev.get("probe_at")
            if probe_at and today >= probe_at:  # 探针复活：禁用到期自动重试
                prev["enabled"] = True
                prev["streak_low"] = 0
                prev["weight_hint"] = 0.8
                decisions.append(
                    (f"source_probe", key, f"fitness={m['fitness']} 禁用期满，自动启用探针重试")
                )
            state[key] = {**prev, **m}
            continue

        if m["fitness"] < disable_below:
            prev["streak_low"] = prev.get("streak_low", 0) + 1
            if prev["streak_low"] >= disable_streak:
                prev["enabled"] = False
                prev["probe_at"] = (parse_day(today) + timedelta(days=probe_days)).isoformat()
                decisions.append(
                    ("source_disable", key,
                     f"连续 {prev['streak_low']} 次 fitness<{disable_below}"
                     f"（日均 {m['mean_items']} 条 / 在场 {m['days_present']} 天）,"
                     f"{probe_days} 天后自动探针重试")
                )
            elif prev.get("weight_hint", 1.0) > 0.6:
                prev["weight_hint"] = round(max(0.6, prev["weight_hint"] - 0.05), 2)
                decisions.append(
                    ("source_demote", key,
                     f"fitness={m['fitness']} 偏低，权重降至 {prev['weight_hint']}")
                )
        else:
            prev["streak_low"] = 0
            if m["fitness"] >= heal_above and prev.get("weight_hint", 1.0) < 1.0:
                prev["weight_hint"] = round(min(1.0, prev["weight_hint"] + 0.1), 2)
                decisions.append(
                    ("source_heal", key, f"fitness={m['fitness']} 恢复健康，权重回升至 {prev['weight_hint']}")
                )
        state[key] = {**prev, **m}
    return state, decisions


# ---------------------------------------------------------------- 主题自学


def collect_theme_tokens(window_days):
    """从最近 N 天日报的共振/飙升板块统计主题词（跨天计数，同天去重）。"""
    cfg = load_config()
    w = {"resonance": 3, "rising": 2}
    daily = []
    for path in sorted(DIGEST_DIR.glob("*.json"), reverse=True)[:window_days]:
        d = load_json(path, {})
        counter = Counter()
        for sec_key, weight in w.items():
            sec = d.get("sources", {}).get(sec_key)
            if not sec:
                continue
            for it in sec.get("items", []):
                for tok in set(tokenize_title(it.get("title", ""))):
                    counter[tok] += weight
        if counter:
            daily.append(counter)
    merged = Counter()
    for c in daily:
        merged.update(c)
    distinct_days = Counter()
    for c in daily:
        for tok in c:
            distinct_days[tok] += 1
    return merged, distinct_days


def evolve_tokens(prefs, today, evcfg):
    """衰减旧权重 + 提升高频主题词。返回 (新 tokens, 提升列表, 决策行)。"""
    decay = evcfg.get("token_daily_decay", 0.95)
    min_freq = evcfg.get("token_promote_min_freq", 3)
    boost = evcfg.get("token_promote_boost", 0.5)
    top_k = evcfg.get("token_promote_top", 8)

    # learn.py 当天已随投票更新过权重（并做过衰减）时，不再重复衰减
    prefs_date = (prefs.get("updated_at") or "")[:10]
    if prefs_date == today:
        decayed = dict(prefs.get("tokens", {}))
    else:
        decayed = {t: round(v * decay, 3) for t, v in prefs.get("tokens", {}).items()}

    freq, distinct = collect_theme_tokens(evcfg.get("fitness_window_days", 7))
    candidates = [
        (t, f)
        for t, f in freq.most_common()
        if f >= min_freq and distinct[t] >= 2
    ][:top_k]

    promoted = []
    for tok, f in candidates:
        old = decayed.get(tok, 0.0)
        new = round(min(TOKEN_CLAMP, old + boost), 3)
        if abs(new) < TOKEN_DROP:
            continue
        decayed[tok] = new
        if new > old + 0.05:
            promoted.append(f"{tok}({f}次→{new:+.2f})")

    tokens = {t: v for t, v in decayed.items() if abs(v) >= TOKEN_DROP}
    tokens = {t: round(max(min(v, TOKEN_CLAMP), -TOKEN_CLAMP), 3) for t, v in tokens.items()}
    line = f"主题自学：提升 {len(promoted)} 个（" + "、".join(promoted) + "）" if promoted \
        else "主题自学：本周无新高频主题，仅自然衰减"
    return tokens, promoted, line


# ---------------------------------------------------------------- 主流程


def main():
    dry_run = "--dry-run" in sys.argv
    cfg = load_config()
    ev = cfg.get("evolve", {})
    fcfg = ev.get("fitness", {})
    window = fcfg.get("window_days", 7)
    today = now_tz(cfg.get("timezone", "Asia/Shanghai")).strftime("%Y-%m-%d")

    fitness_state = load_fitness()
    ledger = load_ledger()
    prefs = load_prefs()

    # 1) 源适应度进化（config 主开关关闭的源不参与）
    fresh = compute_fitness(window)
    fresh = {
        k: v for k, v in fresh.items()
        if cfg.get("sources", {}).get(k, {}).get("enabled", False)
    }
    sources_state, src_decisions = evolve_sources(fitness_state, fresh, fcfg, today)

    # 2) 主题自学 + 3) 自然衰减
    tokens, promoted, token_line = evolve_tokens(prefs, today, ev)

    # 进化账本：决策留痕（源决策逐条，主题合并一条）
    entries = list(ledger.get("entries", []))
    ts = now_tz().isoformat()
    for kind, target, detail in src_decisions:
        entries.append({"ts": ts, "kind": kind, "target": target, "detail": detail})
    if promoted:
        entries.append({
            "ts": ts,
            "kind": "token_promote",
            "target": "prefs.tokens",
            "detail": "、".join(promoted),
        })
    entries = entries[-LEDGER_MAX:]

    fitness_out = {
        "updated_at": ts,
        "sources": sources_state,
    }
    prefs_out = {
        "updated_at": ts,
        "sources": prefs.get("sources", {}),
        "tokens": tokens,
        "seen_comment_ids": prefs.get("seen_comment_ids", []),
        # 稳定先验层（沉淀园种子）原样保留，不参与衰减
        "seed_tokens": prefs.get("seed_tokens", {}),
        "seed_updated_at": prefs.get("seed_updated_at"),
    }

    changed = bool(src_decisions or promoted)
    lines = [
        f"### 🧬 内生进化 · {ts[:16].replace('T', ' ')}",
        "",
        f"- 适应度："
        + " · ".join(
            f"{k} {m['fitness']:.2f}" for k, m in sorted(sources_state.items())
        ),
        f"- {token_line}",
        f"- 关键词偏好库：{len(tokens)} 个",
    ]
    for kind, target, detail in src_decisions:
        lines.append(f"- {LEDGER_EMOJI.get(kind, '⚙')} **{target}**：{detail}")
    print("\n".join(lines))

    if dry_run:
        print(f"[dry-run] 决策 {len(src_decisions)} 条，主题提升 {len(promoted)} 个，关键词库 {len(tokens)} 个")
        return

    atomic_write_json(META_DIR / "fitness.json", fitness_out)
    atomic_write_json(META_DIR / "evolution.json", {"updated_at": ts, "entries": entries})
    prefs_path = DATA_DIR / "prefs.json"
    if changed or not prefs_path.exists() or prefs_date != today:
        prefs_path.write_text(
            json.dumps(prefs_out, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        pushed = git_commit(f"evolve: 内生进化（源决策 {len(src_decisions)}，主题提升 {len(promoted)}）")
        print(f"[ok] 适应度/账本/prefs 已{'提交推送' if pushed else '写入（无变化）'}")
    else:
        pushed = git_commit("evolve: 进化账本心跳（无新决策）")
        print(f"[ok] 无新决策；账本心跳{'已提交' if pushed else '无变化'}")


if __name__ == "__main__":
    main()
