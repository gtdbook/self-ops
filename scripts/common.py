"""共用工具：HTTP 抓取、原子写入、时间处理、GitHub API 与 issue 告警。仅依赖标准库。"""

import gzip
import hashlib
import html as html_mod
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
DIGEST_DIR = DATA_DIR / "digest"
SITE_DIR = REPO_ROOT / "_site"
SITE_URL = "https://gtdbook.github.io/self-ops/"

UA = "Mozilla/5.0 (X11; Linux x86_64) self-ops/1.0 (+https://github.com/gtdbook/self-ops)"

ALERT_LABEL = "self-ops-alert"
PANEL_TITLE = "📊 self-ops 运行面板"


def load_config():
    with open(REPO_ROOT / "config.json", encoding="utf-8") as f:
        return json.load(f)


def now_tz(tz_name="Asia/Shanghai"):
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(tz_name))


def today_key(cfg=None):
    """数据归档使用的日期键（默认北京时间）。"""
    tz = (cfg or load_config()).get("timezone", "Asia/Shanghai")
    return now_tz(tz).strftime("%Y-%m-%d")


def parse_day(day):
    return datetime.strptime(day, "%Y-%m-%d").date()


# ---------------------------------------------------------------- HTTP


def http_get(url, *, headers=None, timeout=30, retries=2, accept="application/json"):
    """带重试的 GET，返回 (status, bytes)。自动处理 gzip。"""
    hdrs = {"User-Agent": UA, "Accept": accept}
    if headers:
        hdrs.update(headers)
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return resp.status, data
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}: {e.read()[:200]!r}"
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(3 * (attempt + 1))
                continue
            raise RuntimeError(f"GET {url} 失败: {last_err}")
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            if attempt < retries:
                time.sleep(3 * (attempt + 1))
                continue
            raise RuntimeError(f"GET {url} 失败: {last_err}")


def get_json(url, **kw):
    _, body = http_get(url, **kw)
    return json.loads(body.decode("utf-8", "replace"))


def strip_html(s, limit=280):
    s = html_mod.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def md5_key(s):
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:16]


def atomic_write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def atomic_write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------- GitHub API


def gh_api(path, *, token, method="GET", payload=None):
    url = path if path.startswith("http") else f"https://api.github.com{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": UA,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            body = resp.read().decode("utf-8", "replace")
            return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise RuntimeError(f"GitHub API {method} {url} -> HTTP {e.code}: {e.read()[:300]!r}")


# ---------------------------------------------------------------- issue 告警


def _ensure_alert_label(token, repo):
    if gh_api(f"/repos/{repo}/labels/{ALERT_LABEL}", token=token) is None:
        gh_api(
            f"/repos/{repo}/labels",
            token=token,
            method="POST",
            payload={"name": ALERT_LABEL, "color": "d1242f", "description": "self-ops 自动告警"},
        )


def _find_open_issue(token, repo, *, label=None, title_prefix=None):
    q = f"/repos/{repo}/issues?state=open&per_page=100"
    if label:
        q += f"&labels={urllib.parse.quote(label)}"
    for issue in gh_api(q, token=token) or []:
        if issue.get("pull_request"):
            continue
        if title_prefix is None or issue["title"].startswith(title_prefix):
            return issue
    return None


def upsert_alert_issue(token, repo, body):
    """有未关闭的告警 issue 就追加评论，否则新建。返回 issue 编号。"""
    _ensure_alert_label(token, repo)
    issue = _find_open_issue(token, repo, label=ALERT_LABEL)
    if issue:
        gh_api(issue["comments_url"], token=token, method="POST", payload={"body": body})
        return issue["number"]
    created = gh_api(
        f"/repos/{repo}/issues",
        token=token,
        method="POST",
        payload={
            "title": f"🚨 self-ops 异常告警",
            "body": body,
            "labels": [ALERT_LABEL],
        },
    )
    return created["number"]


def resolve_alert_issue(token, repo, note):
    issue = _find_open_issue(token, repo, label=ALERT_LABEL)
    if not issue:
        return
    gh_api(issue["comments_url"], token=token, method="POST", payload={"body": note})
    gh_api(f"/repos/{repo}/issues/{issue['number']}", token=token, method="PATCH",
          payload={"state": "closed"})


def append_panel_comment(token, repo, body):
    """运行面板 issue：不存在则创建，始终追加一条状态评论。"""
    issue = _find_open_issue(token, repo, title_prefix=PANEL_TITLE)
    if not issue:
        issue = gh_api(
            f"/repos/{repo}/issues",
            token=token,
            method="POST",
            payload={"title": PANEL_TITLE, "body": "self-ops 每日自检报告沉淀在此。"},
        )
    gh_api(issue["comments_url"], token=token, method="POST", payload={"body": body})


# ---------------------------------------------------------------- 展示格式


_STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "with", "new", "how", "why", "what",
    "your", "you", "its", "is", "are", "to", "of", "in", "on", "from", "at",
    "by", "as", "this", "that", "it", "be", "not", "but", "can", "will", "now",
    "announces", "announced", "launches", "launched", "unveils", "unveiled",
    "vs", "using", "into", "about", "after", "before", "over", "more", "most",
    "best", "top", "all", "get", "gets", "set", "sets", "day", "days", "week",
    "year", "first", "one", "two", "her", "his", "their", "they", "them",
    "was", "were", "has", "have", "had", "than", "then", "when", "who", "what",
}


def tokenize_title(s):
    """标题分词（小写、去停用词、去纯数字），用于共振聚类与口味学习。"""
    words = re.findall(r"[a-z0-9]{3,}", (s or "").lower())
    return [w for w in words if w not in _STOPWORDS and not w.isdigit()]


def format_meta(source_key, item):
    """各数据源条目的元信息行。"""
    if source_key == "hackernews":
        return f"⭐ {item.get('score', 0)} · 💬 {item.get('comments', 0)}"
    if source_key == "github_search":
        lang = item.get("language") or ""
        return f"⭐ {item.get('stars', 0)}" + (f" · {lang}" if lang else "")
    if source_key == "blog_rss":
        pub = (item.get("published_iso") or "")[:10]
        return item.get("feed_name", "") + (f" · {pub}" if pub else "")
    if source_key == "producthunt":
        pub = (item.get("published_iso") or "")[:10]
        return "Product Hunt" + (f" · {pub}" if pub else "")
    if source_key == "aihot":
        src = item.get("source_name") or "AIHOT"
        pub = (item.get("published_at") or "")[:10]
        return f"{src}" + (f" · {pub}" if pub else "")
    return ""
