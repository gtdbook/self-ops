"""学习每日情报 issue 上的口味投票 → 更新 data/prefs.json 并提交回仓库。

投票格式：issue 评论 `like 3 7` / `dislike 5`（或 `赞` / `踩`）。
学到的东西：各源板块权重（影响板块排序）+ 标题关键词权重（影响 HN/GitHub 条目加权）。
偏好随时间衰减，长期不投票会缓慢回归中性。--dry-run 只打印不落盘。

用法：python scripts/learn.py [--dry-run]
"""

import json
import os
import re
import subprocess
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
from common import (  # noqa: E402
    DATA_DIR,
    REPO_ROOT,
    append_panel_comment,
    gh_api,
    load_config,
    now_tz,
    tokenize_title,
)

DECAY = 0.9
CLAMP = 5.0
DROP_BELOW = 0.15
REAL_SOURCES = ("hackernews", "github_search", "blog_rss", "producthunt", "aihot")


def extract_votemap(issue_body):
    m = re.search(r"<!--votemap\s*(\{.*?\})\s*-->", issue_body or "", re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except Exception:
        return {}


def parse_votes(text, positive, negative):
    """返回 [(sign, num_str), ...]。先匹配负向词，再从原文剔除后匹配正向词，避免 dislike 命中 like。"""
    votes = []
    pattern = r"{}[\s:：,，]*((?:\d+[\s,，、+*.]*)+)"

    def nums_of(match):
        return re.findall(r"\d+", match.group(1))[:10]

    for word in negative:
        for m in re.finditer(pattern.format(re.escape(word)), text, re.I):
            votes.extend((-1, n) for n in nums_of(m))
    stripped = text
    for word in negative:
        stripped = re.sub(re.escape(word), " ", stripped, flags=re.I)
    for word in positive:
        for m in re.finditer(pattern.format(re.escape(word)), stripped, re.I):
            votes.extend((1, n) for n in nums_of(m))
    return votes


def load_prefs():
    path = DATA_DIR / "prefs.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "sources": {k: 1.0 for k in REAL_SOURCES},
        "tokens": {},
        "seen_comment_ids": [],
    }


def git_commit(message):
    def run(*args):
        subprocess.run(["git", *args], cwd=REPO_ROOT, check=True, capture_output=True)

    run("config", "user.name", "self-ops-bot")
    run("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    run("add", "data/prefs.json")
    if subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=REPO_ROOT
    ).returncode == 0:
        return False
    run("commit", "-m", message)
    run("pull", "--rebase", "origin", "main")
    run("push")
    return True


def main():
    dry_run = "--dry-run" in sys.argv
    token = os.environ.get("GH_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print("[fatal] 需要 GH_TOKEN 与 GITHUB_REPOSITORY 环境变量")
        sys.exit(1)

    cfg = load_config()
    evolve = cfg.get("evolve", {})
    positive = evolve.get("vote_positive", ["like", "赞"])
    negative = evolve.get("vote_negative", ["dislike", "踩"])

    prefs = load_prefs()
    seen = set(prefs.get("seen_comment_ids", []))
    delta_sources = defaultdict(float)
    delta_tokens = defaultdict(float)
    new_votes = 0

    issues = gh_api(f"/repos/{repo}/issues?state=all&labels=daily&per_page=30", token=token) or []
    for issue in issues:
        votemap = extract_votemap(issue.get("body"))
        if not votemap:
            continue
        for comment in gh_api(issue["comments_url"], token=token) or []:
            cid = comment.get("id")
            if cid in seen:
                continue
            seen.add(cid)
            for sign, num in parse_votes(comment.get("body") or "", positive, negative):
                entry = votemap.get(num)
                if not entry:
                    continue
                new_votes += 1
                src = entry.get("source")
                if src in REAL_SOURCES:
                    delta_sources[src] += sign
                for tok in set(tokenize_title(entry.get("title", ""))):
                    delta_tokens[tok] += sign * 0.6

    if new_votes == 0:
        print("[ok] 没有新投票，偏好保持不变")
        return

    # 衰减 → 应用增量 → 收敛裁剪（每个 key 只应用一次增量，避免重复叠加）
    def clamp(v):
        return round(min(max(v, -CLAMP), CLAMP), 3)

    sources, handled = {}, set()
    for k, v in prefs.get("sources", {}).items():
        handled.add(k)
        sources[k] = clamp(v * DECAY + delta_sources.get(k, 0.0))
    for k, v in delta_sources.items():
        if k not in handled:
            sources[k] = clamp(1.0 + v)

    tokens = {}
    handled = set()
    for k, v in prefs.get("tokens", {}).items():
        handled.add(k)
        nv = v * DECAY + delta_tokens.get(k, 0.0)
        if abs(nv) >= DROP_BELOW:
            tokens[k] = round(nv, 3)
    for k, v in delta_tokens.items():
        if k not in handled and abs(v) >= DROP_BELOW:
            tokens[k] = round(v, 3)

    top_tokens = sorted(tokens.items(), key=lambda x: abs(x[1]), reverse=True)[:8]
    summary_lines = [
        f"### 🎓 口味学习 · {now_tz().strftime('%Y-%m-%d %H:%M')}",
        "",
        f"- 新学到 **{new_votes} 票**，偏好库 {len(tokens)} 个关键词",
        "- 板块权重："
        + " · ".join(f"{k} {sources.get(k, 1.0):+.1f}" for k in sorted(sources)),
        "- 关键词偏好 Top："
        + ("、".join(f"{t}({v:+.1f})" for t, v in top_tokens) or "暂无"),
        "",
        "下一次流水线运行起，板块排序与 HN/GitHub 条目加权将按新偏好生成。",
    ]

    if dry_run:
        print(f"[dry-run] 新票 {new_votes}，delta_sources={dict(delta_sources)}，delta_tokens={dict(delta_tokens)}")
        print("\n".join(summary_lines))
        return

    prefs = {
        "updated_at": now_tz().isoformat(),
        "sources": sources,
        "tokens": tokens,
        "seen_comment_ids": sorted(seen)[-1000:],
    }
    (DATA_DIR / "prefs.json").write_text(
        json.dumps(prefs, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    pushed = git_commit(f"evolve: 学习 {new_votes} 票口味反馈")
    print(f"[ok] 学到 {new_votes} 票；prefs.json {'已提交' if pushed else '无变化'}")
    append_panel_comment(token, repo, "\n".join(summary_lines))


if __name__ == "__main__":
    main()
