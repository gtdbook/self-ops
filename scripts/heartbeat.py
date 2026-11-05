"""每日自检 + 自愈看门狗：流水线活性 + 数据新鲜度 + Pages 可达性 + 周报/进化环。

异常 → 打开/更新告警 issue（label: self-ops-alert）；恢复 → 自动评论并关闭。
自愈：流水线漏跑（GitHub 定时任务偶发跳度）> 26h 或 evolve 环 > 30h 未跑时，
通过 workflow_dispatch 自动补触发对应 workflow，实现闭环自恢复。
无论健康与否，都向「运行面板」issue 追加一条当日状态。
用法：python scripts/heartbeat.py [--dry-run]
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
    load_json,
    now_tz,
    parse_day,
    resolve_alert_issue,
    upsert_alert_issue,
)

PIPELINE_MAX_QUIET_HOURS = 26
EVOLVE_MAX_QUIET_HOURS = 30
DATA_MAX_STALE_DAYS = 2
WEEKLY_MAX_STALE_DAYS = 9
EVOLVE_MAX_STALE_DAYS = 3


def check_pipeline(token, repo):
    runs = gh_api(
        f"/repos/{repo}/actions/workflows/pipeline.yml/runs?status=success&per_page=1",
        token=token,
    )
    runs = (runs or {}).get("workflow_runs") or []
    if not runs:
        return False, "从未有过成功运行", 999
    last = runs[0]
    updated = datetime.strptime(last["updated_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    age_h = (datetime.now(timezone.utc) - updated).total_seconds() / 3600
    if age_h > PIPELINE_MAX_QUIET_HOURS:
        return False, f"上次成功流水线距今 {age_h:.0f} 小时 (> {PIPELINE_MAX_QUIET_HOURS}h)", age_h
    return True, f"上次成功流水线 {age_h:.1f} 小时前 (run #{last['run_number']})", age_h


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


def check_weekly():
    """周报环：最新周报不超过一周（新环节尚未产出时暂不告警）。"""
    weekly_dir = DATA_DIR / "weekly"
    files = sorted(weekly_dir.glob("*.json")) if weekly_dir.exists() else []
    if not files:
        return True, "周报尚未产出（环节待首跑）"
    latest = load_json(files[-1], {}) or {}
    gen = (latest.get("generated_at") or "")[:10]
    if not gen:
        return False, f"周报文件损坏：{files[-1].name}"
    stale = (parse_day(now_tz().strftime("%Y-%m-%d")) - parse_day(gen)).days
    if stale > WEEKLY_MAX_STALE_DAYS:
        return False, f"最新周报 {latest.get('week', files[-1].stem)} 已 {stale} 天未更新"
    return True, f"最新周报 {latest.get('week', files[-1].stem)}（{stale} 天前）"


def check_evolve():
    """进化环：进化账本（含心跳戳）不超过 3 天。"""
    ledger = load_json(DATA_DIR / "meta" / "evolution.json", {}) or {}
    ts = (ledger.get("updated_at") or "")[:10]
    if not ts:
        return True, "进化账本尚未建立（环节待首跑）"
    stale = (parse_day(now_tz().strftime("%Y-%m-%d")) - parse_day(ts)).days
    if stale >= EVOLVE_MAX_STALE_DAYS:
        return False, f"进化账本已 {stale} 天未更新（evolve 环疑似停摆）"
    prefs = load_json(DATA_DIR / "prefs.json", {}) or {}
    return True, f"进化账本正常（{len(ledger.get('entries', []))} 条决策，{len(prefs.get('tokens', {}))} 个关键词）"


def check_sediment():
    """沉淀环：种子新鲜度（>3 周未刷新视为断链）+ 沉淀库状态。"""
    seed = load_json(DATA_DIR / "sediment" / "seed.json", {}) or {}
    if not seed.get("fetched_at"):
        return True, "沉淀种子未导入（环节待首跑）"
    fetched = (seed.get("fetched_at") or "")[:10]
    stale = (parse_day(now_tz().strftime("%Y-%m-%d")) - parse_day(fetched)).days
    if stale > 21:
        return False, f"沉淀园种子 {stale} 天未刷新（来源可能失效）"
    idx = load_json(DATA_DIR / "sediment" / "index.json", {}) or {}
    n = len(idx.get("entries") or {})
    pending = len(idx.get("pending") or [])
    return True, f"沉淀库 {n} 条（新芽 {pending}）· 种子 {seed.get('articles', 0)} 篇（{stale} 天前）"


def self_heal(token, repo, pipeline_age_h, evolve_ok):
    """漏跑自愈：补触发停摆的 workflow。返回自愈说明行列表。"""
    notes = []
    if pipeline_age_h > PIPELINE_MAX_QUIET_HOURS:
        try:
            gh_api(
                f"/repos/{repo}/actions/workflows/pipeline.yml/dispatches",
                token=token,
                method="POST",
                payload={"ref": "main"},
            )
            notes.append(f"🛠 已自动补触发 pipeline（距上次成功 {pipeline_age_h:.0f}h）")
        except Exception as e:
            notes.append(f"🛠 补触发 pipeline 失败：{e}")
    if not evolve_ok:
        try:
            gh_api(
                f"/repos/{repo}/actions/workflows/evolve.yml/dispatches",
                token=token,
                method="POST",
                payload={"ref": "main"},
            )
            notes.append("🛠 已自动补触发 evolve（进化环停摆）")
        except Exception as e:
            notes.append(f"🛠 补触发 evolve 失败：{e}")
    return notes


def main():
    dry_run = "--dry-run" in sys.argv
    token = os.environ.get("GH_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print("[fatal] 需要 GH_TOKEN 与 GITHUB_REPOSITORY 环境变量")
        sys.exit(1)

    ok_pipeline, detail_pipeline, age_h = check_pipeline(token, repo)
    checks = {
        "流水线": (ok_pipeline, detail_pipeline),
        "数据": check_data_freshness(),
        "部署": check_pages(repo),
        "周报环": check_weekly(),
        "进化环": check_evolve(),
        "沉淀环": check_sediment(),
    }
    heal_notes = self_heal(token, repo, age_h, checks["进化环"][0]) if not dry_run else []

    lines = [f"### 🩺 self-ops 自检报告 · {now_tz().strftime('%Y-%m-%d %H:%M')}", ""]
    bad = []
    for name, (ok, detail) in checks.items():
        mark = "✅" if ok else "❌"
        lines.append(f"- {mark} **{name}**：{detail}")
        if not ok:
            bad.append(f"{name}: {detail}")
    for note in heal_notes:
        lines.append(f"- {note}")
    if heal_notes:
        bad.append("已触发自愈补跑（见上）")

    if dry_run:
        print("\n".join(lines))
        return

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
