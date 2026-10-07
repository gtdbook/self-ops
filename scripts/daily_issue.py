"""把最新日报发布成 issue（编号条目 + 隐藏投票映射），作为邮件通知与口味投票的载体。

用法：python scripts/daily_issue.py [--dry-run]
优先发布昨天（北京时间）的完整日报；不存在则发布最新一期。
幂等：同一日期的日报 issue 只发一次。
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from common import (  # noqa: E402
    DIGEST_DIR,
    SITE_URL,
    gh_api,
    load_config,
    now_tz,
)

DAILY_LABEL = "daily"


def pick_digest():
    cfg = load_config()
    # evolve 在北京时间早晨触发，此时「昨天」的日报才是完整的一天
    from datetime import timedelta

    from common import now_tz

    yesterday = (now_tz(cfg.get("timezone", "Asia/Shanghai")) - timedelta(days=1)).strftime(
        "%Y-%m-%d"
    )
    latest = sorted(DIGEST_DIR.glob("*.json"), reverse=True)
    if not latest:
        return None
    # 优先昨天的完整日报；取不到就用最新一期
    target = DIGEST_DIR / f"{yesterday}.json"
    path = target if target.exists() else latest[0]
    return json.loads(path.read_text(encoding="utf-8"))


def build_body(digest):
    total = sum(len(s["items"]) for s in digest["sources"].values())
    lines = [
        f"# 📰 每日情报 · {digest['date']}",
        "",
        f"> 共 {total} 条 · [在线站点]({SITE_URL}) · [RSS 订阅]({SITE_URL}feed.xml) · 数据自动生成",
        "",
    ]
    votemap = {}
    num = 0
    for key, sec in digest["sources"].items():
        lines.append(f"## {sec['emoji']} {sec['title']}")
        lines.append("")
        for it in sec["items"]:
            num += 1
            title_md = f"[{it['title']}]({it['url']})" if it.get("url") else it["title"]
            lines.append(f"{num}. {title_md}")
            if it.get("meta"):
                lines.append(f"   - {it['meta']}")
            votemap[str(num)] = {
                "source": it.get("origin_source", key),
                "title": it["title"],
            }
        lines.append("")
    lines += [
        "---",
        "",
        "💬 **口味投票**：回复 `like 3 7`（喜欢第 3、7 条）或 `dislike 5`，中文用 `赞 3` / `踩 5` 也行。",
        "体系每天学习一次投票，逐渐把板块排序和条目权重调成你的口味。",
        "",
        "<!--votemap\n" + json.dumps(votemap, ensure_ascii=False).replace("--", "—") + "\n-->",
    ]
    return "\n".join(lines), votemap


def main():
    dry_run = "--dry-run" in sys.argv
    token = os.environ.get("GH_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    digest = pick_digest()
    if not digest:
        print("[warn] 没有任何日报，跳过")
        return
    title = f"📰 每日情报 · {digest['date']}"
    body, votemap = build_body(digest)
    if dry_run:
        print(f"[dry-run] 标题：{title}")
        print(f"[dry-run] 投票映射条目数：{len(votemap)}")
        print(body[:2000])
        return
    if not token or not repo:
        print("[fatal] 需要 GH_TOKEN 与 GITHUB_REPOSITORY 环境变量")
        sys.exit(1)

    # 幂等检查
    for issue in gh_api(f"/repos/{repo}/issues?state=all&labels={DAILY_LABEL}&per_page=50", token=token) or []:
        if issue["title"] == title:
            print(f"[skip] {title} 已发布 (#{issue['number']})")
            return
    if gh_api(f"/repos/{repo}/labels/{DAILY_LABEL}", token=token) is None:
        gh_api(
            f"/repos/{repo}/labels",
            token=token,
            method="POST",
            payload={"name": DAILY_LABEL, "color": "1f883d", "description": "每日情报日报"},
        )
    created = gh_api(
        f"/repos/{repo}/issues",
        token=token,
        method="POST",
        payload={"title": title, "body": body, "labels": [DAILY_LABEL]},
    )
    print(f"[ok] 已发布 {title} → issue #{created['number']}（{len(votemap)} 个可投票条目）")


if __name__ == "__main__":
    main()
