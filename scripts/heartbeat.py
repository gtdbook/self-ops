"""每日自检：流水线活性 + 数据新鲜度 + Pages 可达性。

异常 → 打开/更新告警 issue（label: self-ops-alert）；恢复 → 自动评论并关闭。
无论健康与否，都向「运行面板」issue 追加一条当日状态。
"""

import json
import os
import sys
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from common import (  # noqa: E402
    DATA_DIR,
    append_panel_comment,
    gh_api,
    http_get,
    load_config,
    now_tz,
    parse_day,
    resolve_alert_issue,
    upsert_alert_issue,
)

PIPELINE_MAX_QUIET_HOURS = 26
DATA_MAX_STALE_DAYS = 2


def check_pipeline(token, repo):
    runs = gh_api(
        f"/repos/{repo}/actions/workflows/pipeline.yml/runs?status=success&per_page=1",
        token=token,
    )
    runs = (runs or {}).get("workflow_runs") or []
    if not runs:
        return False, "从未有过成功运行"
    last = runs[0]
    updated = datetime.strptime(last["updated_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    age_h = (datetime.now(timezone.utc) - updated).total_seconds() / 3600
    if age_h > PIPELINE_MAX_QUIET_HOURS:
        return False, f"上次成功流水线距今 {age_h:.0f} 小时 (> {PIPELINE_MAX_QUIET_HOURS}h)"
    return True, f"上次成功流水线 {age_h:.1f} 小时前 (run #{last['run_number']})"


def check_data_freshness():
    idx_path = DATA_DIR / "index.json"
    if not idx_path.exists():
        return False, "data/index.json 不存在"
    try:
        idx = json.loads(idx_path.read_text(encoding="utf-8"))
    except Exception as e:
        return False, f"index.json 解析失败: {e}"
    days = idx.get("days") or []
    if not days:
        return False, "索引为空"
    latest = parse_day(days[0]["date"])
    cfg = load_config()
    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo(cfg.get("timezone", "Asia/Shanghai"))).date()
    stale = (today - latest).days
    if stale >= DATA_MAX_STALE_DAYS:
        return False, f"最新数据日期 {days[0]['date']}，落后 {stale} 天"
    return True, f"最新数据 {days[0]['date']}（{days[0]['total']} 条）"


def check_pages(repo):
    owner, name = repo.split("/")
    url = f"https://{owner}.github.io/{name}/"
    try:
        status, _ = http_get(url, timeout=25, retries=1, accept="text/html,*/*")
        if status == 200:
            return True, f"Pages 正常 ({url})"
        return False, f"Pages 返回 HTTP {status} ({url})"
    except Exception as e:
        return False, f"Pages 不可达 ({url}): {e}"


def main():
    token = os.environ.get("GH_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print("[fatal] 需要 GH_TOKEN 与 GITHUB_REPOSITORY 环境变量")
        sys.exit(1)

    checks = {
        "流水线": check_pipeline(token, repo),
        "数据": check_data_freshness(),
        "部署": check_pages(repo),
    }
    lines = [f"### 🩺 self-ops 自检报告 · {now_tz().strftime('%Y-%m-%d %H:%M')}", ""]
    bad = []
    for name, (ok, detail) in checks.items():
        mark = "✅" if ok else "❌"
        lines.append(f"- {mark} **{name}**：{detail}")
        if not ok:
            bad.append(f"{name}: {detail}")

    if bad:
        body = "\n".join(lines) + "\n\n> 此 issue 由自检自动创建/更新，恢复后将自动关闭。"
        num = upsert_alert_issue(token, repo, body)
        print(f"[alert] 检出异常，告警 issue #{num}:\n" + "\n".join(bad))
    else:
        resolve_alert_issue(
            token, repo, f"### ✅ 自检全部通过 · {now_tz().strftime('%Y-%m-%d %H:%M')}\n\n自动关闭告警。"
        )
        print("[ok] 自检全部通过")

    append_panel_comment(token, repo, "\n".join(lines))


if __name__ == "__main__":
    main()
