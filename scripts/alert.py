"""流水线失败时立即开告警 issue（由 pipeline.yml 的 failure() 步骤调用）。"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from common import now_tz, upsert_alert_issue  # noqa: E402


def main():
    token = os.environ.get("GH_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print("[fatal] 需要 GH_TOKEN 与 GITHUB_REPOSITORY 环境变量")
        sys.exit(1)
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    run_url = f"https://github.com/{repo}/actions/runs/{run_id}" if run_id else ""
    body = (
        f"### 🚨 流水线失败 · {now_tz().strftime('%Y-%m-%d %H:%M')}\n\n"
        f"抓取/处理/提交阶段失败，请查看运行日志排查。\n\n"
        + (f"[查看运行]({run_url})\n" if run_url else "")
        + "\n> 每日自检 (heartbeat) 将持续跟踪，恢复后自动关闭。"
    )
    num = upsert_alert_issue(token, repo, body)
    print(f"[alert] 已发出告警 issue #{num}")


if __name__ == "__main__":
    main()
