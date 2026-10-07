"""抓取各数据源 → data/raw/<日期>/<source>.json。

同一日内多次运行会按 id 增量合并去重；单个源失败只记录告警，不影响整体流水线；
全部启用源都失败时以非零码退出（触发 workflow 的 failure 告警）。
"""

import email.utils
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

sys.path.insert(0, os.path.dirname(__file__))
from common import (  # noqa: E402
    RAW_DIR,
    atomic_write_json,
    get_json,
    http_get,
    load_config,
    md5_key,
    now_tz,
    strip_html,
    today_key,
)

MAX_ITEMS_PER_DAY = 300


# ---------------------------------------------------------------- 数据源


def fetch_hackernews(scfg):
    ids = get_json("https://hacker-news.firebaseio.com/v0/topstories.json")[
        : scfg.get("top_stories", 25)
    ]

    def one(i):
        try:
            it = get_json(f"https://hacker-news.firebaseio.com/v0/item/{i}.json", timeout=15)
            if not it:
                return None
            t = datetime.fromtimestamp(it["time"], tz=timezone.utc)
            return {
                "id": f"hn-{it['id']}",
                "title": it.get("title", ""),
                "url": it.get("url") or f"https://news.ycombinator.com/item?id={it['id']}",
                "hn_url": f"https://news.ycombinator.com/item?id={it['id']}",
                "score": it.get("score", 0),
                "comments": it.get("descendants", 0),
                "author": it.get("by", ""),
                "time_iso": t.isoformat(),
            }
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=8) as ex:
        return [x for x in ex.map(one, ids) if x]


def fetch_github_search(scfg):
    since = (
        datetime.now(timezone.utc) - timedelta(days=scfg.get("days_back", 7))
    ).strftime("%Y-%m-%d")
    q = f"created:>{since}"
    if scfg.get("query_extra"):
        q += " " + scfg["query_extra"]
    headers = {"Accept": "application/vnd.github+json"}
    tok = os.environ.get("GH_TOKEN")
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    url = (
        "https://api.github.com/search/repositories?q="
        + quote(q)
        + f"&sort=stars&order=desc&per_page={scfg.get('per_page', 20)}"
    )
    data = get_json(url, headers=headers)
    return [
        {
            "id": f"gh-{r['id']}",
            "title": r["full_name"],
            "url": r["html_url"],
            "stars": r.get("stargazers_count", 0),
            "language": r.get("language") or "",
            "description": strip_html(r.get("description"), 240),
            "created_at": r.get("created_at"),
        }
        for r in data.get("items", [])
    ]


def _tag(t):
    return t.rsplit("}", 1)[-1].lower()


def _parse_dt(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return email.utils.parsedate_to_datetime(s)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _feed_items(root, feed_name):
    """兼容 RSS 2.0 与 Atom，返回原始条目字段。"""
    els = list(root.iter())
    out = []
    if any(_tag(e.tag) == "entry" for e in els):
        for e in (e for e in els if _tag(e.tag) == "entry"):
            title = link = pub = summ = ""
            for c in e:
                ct = _tag(c.tag)
                if ct == "title":
                    title = (c.text or "").strip()
                elif ct == "link":
                    link = c.get("href") or c.text or link
                elif ct in ("updated", "published", "date") and not pub:
                    pub = c.text or ""
                elif ct in ("summary", "content") and not summ:
                    summ = c.text or ""
            out.append((title, link, pub, summ))
    else:
        for e in (e for e in els if _tag(e.tag) == "item"):
            title = link = pub = summ = ""
            for c in e:
                ct = _tag(c.tag)
                if ct == "title":
                    title = (c.text or "").strip()
                elif ct == "link" and not link:
                    link = (c.text or "").strip()
                elif ct == "guid" and not link:
                    link = (c.text or "").strip()
                elif ct in ("pubdate", "date") and not pub:
                    pub = c.text or ""
                elif ct in ("description", "encoded") and not summ:
                    summ = c.text or ""
            out.append((title, link, pub, summ))
    return out


def fetch_blog_rss(scfg):
    items = []
    for feed in scfg.get("feeds", []):
        try:
            _, body = http_get(
                feed["url"],
                accept="application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
                timeout=30,
            )
            root = ET.fromstring(body)
            for title, link, pub, summ in _feed_items(root, feed["name"]):
                if not title:
                    continue
                dt = _parse_dt(pub)
                items.append(
                    {
                        "id": "rss-" + md5_key(link or title),
                        "title": strip_html(title, 200),
                        "url": link,
                        "feed_name": feed["name"],
                        "published": pub,
                        "published_iso": dt.astimezone(timezone.utc).isoformat() if dt else "",
                        "summary": strip_html(summ, 220),
                    }
                )
        except Exception as e:
            print(f"[warn] RSS {feed['name']}: {e}")
    return items


def fetch_producthunt(scfg):
    """Product Hunt 官方 Atom feed；tagline 在 content 首个 <p> 里。保持 feed 顺序（站方策展序）。"""
    items = []
    for feed in scfg.get("feeds", [{"url": "https://www.producthunt.com/feed"}]):
        _, body = http_get(
            feed["url"],
            accept="application/atom+xml, application/xml, text/xml, */*",
            timeout=30,
        )
        root = ET.fromstring(body)
        for e in (el for el in root.iter() if _tag(el.tag) == "entry"):
            title = link = pub = content = ""
            for c in e:
                ct = _tag(c.tag)
                if ct == "title":
                    title = (c.text or "").strip()
                elif ct == "link" and c.get("href"):
                    link = c.get("href")
                elif ct == "published":
                    pub = c.text or ""
                elif ct == "content":
                    content = c.text or ""
            m = re.search(r"<p[^>]*>(.*?)</p>", content, re.S)
            tagline = strip_html(m.group(1), 200) if m else strip_html(content, 200)
            dt = _parse_dt(pub)
            items.append(
                {
                    "id": "ph-" + md5_key(link or title),
                    "title": title,
                    "url": link,
                    "tagline": tagline,
                    "published": pub,
                    "published_iso": dt.astimezone(timezone.utc).isoformat() if dt else "",
                    "summary": tagline,
                }
            )
    return items


def fetch_aihot(scfg):
    params = {
        "mode": scfg.get("mode", "selected"),
        "window": scfg.get("window", "24h"),
        "limit": scfg.get("limit", 30),
    }
    data = get_json(scfg["endpoint"] + "?" + urlencode(params))
    arr = data.get("items") if isinstance(data, dict) else data
    items = []
    for it in arr or []:
        links = it.get("links") or {}
        url = links.get("aihot") or links.get("original") or ""
        items.append(
            {
                "id": "aihot-" + (it.get("id") or md5_key(url or it.get("title", ""))),
                "title": it.get("title", ""),
                "url": url,
                "summary": strip_html(it.get("summary"), 240),
                "reason": strip_html(it.get("reason"), 200) or None,
                "source_name": (it.get("source") or {}).get("name"),
                "published_at": it.get("publishedAt") or it.get("discoveredAt"),
                "score": it.get("score", 0),
            }
        )
    return items


FETCHERS = {
    "hackernews": fetch_hackernews,
    "github_search": fetch_github_search,
    "blog_rss": fetch_blog_rss,
    "producthunt": fetch_producthunt,
    "aihot": fetch_aihot,
}


# ---------------------------------------------------------------- 合并落盘


def merge_and_save(day, key, new_items, error=None):
    path = RAW_DIR / day / f"{key}.json"
    old = {}
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            old = {}
    by_id = {it["id"]: it for it in old.get("items", [])}
    for it in new_items:
        by_id[it["id"]] = it  # 新数据覆盖同 id 旧数据（分数/排名会更新）
    merged = list(by_id.values())
    # 保持新抓到的在前
    merged.sort(key=lambda x: x["id"], reverse=False)
    record = {
        "source": key,
        "updated_at": now_tz().isoformat(),
        "count": len(merged),
        "items": merged[:MAX_ITEMS_PER_DAY],
    }
    errors = old.get("errors", [])
    if error:
        errors = (errors + [{"at": record["updated_at"], "error": error}])[-20:]
    if errors:
        record["errors"] = errors
    atomic_write_json(path, record)
    return record


def main():
    cfg = load_config()
    day = today_key(cfg)
    enabled = {k: v for k, v in cfg["sources"].items() if v.get("enabled")}
    ok_count = 0
    for key, scfg in enabled.items():
        fetcher = FETCHERS.get(key)
        if not fetcher:
            print(f"[warn] {key}: 未注册的抓取器，跳过")
            continue
        try:
            items = fetcher(scfg)
            merge_and_save(day, key, items)
            ok_count += 1
            print(f"[ok] {key}: {len(items)} 条")
        except Exception as e:
            merge_and_save(day, key, [], error=str(e))
            print(f"[warn] {key}: 抓取失败 - {e}")
    if enabled and ok_count == 0:
        print("[fatal] 所有启用源均失败")
        sys.exit(1)
    print(f"完成：{ok_count}/{len(enabled)} 个源成功，日期 {day}")


if __name__ == "__main__":
    main()
