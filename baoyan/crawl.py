"""抓取编排：源 → 列表页 → 候选通知 → 详情页 → 结构化记录。"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import urllib.parse

from . import db, extract, http

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG = os.path.join(HERE, "config", "sources.json")

FOOTER_MARKERS = ("上一篇", "下一篇", "友情链接", "版权所有", "Copyright", "相关附件", "分享到", "返回列表")

# 同一篇文章常被 CMS 挂到多个栏目下（复旦 c24257a792585 / c24753a792585：文章号相同、栏目号不同），
# 所以去重的关键是「文章 ID」，而不是完整 URL。
CONTENT_KEY_PATTERNS = (
    r"/c\d+a(\d+)/",            # 复旦式：c24257a792585
    r"/info/\d+/(\d+)\.",       # 通用：/info/1034/10235.htm
    r"/art/\d{4}/\d+/(\d+)\.",
    r"content_(\d+)\.",
    r"t\d{4}_(\d+)\.",
    r"/(\d{6,})\.(?:s?html?)$",
    r"[?&](?:id|info_id|articleid)=(\d+)",
)


def content_key(url: str) -> str:
    """取文章唯一标识（含主机），用于跨栏目去重；取不到则退回规范化 URL。"""
    parts = urllib.parse.urlsplit(url)
    host = parts.netloc.lower()
    hay = parts.path + ("?" + parts.query if parts.query else "")
    for pat in CONTENT_KEY_PATTERNS:
        m = re.search(pat, hay, re.I)
        if m:
            return f"{host}#{m.group(1)}"
    return f"{host}#url:{normalize_url(url)}"


def load_config(path: str | None = None) -> dict:
    with open(path or DEFAULT_CONFIG, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    cfg.setdefault("defaults", {})
    return cfg


# ---------------------------------------------------------------- 工具

def normalize_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query = [(k, v) for k, v in query if not k.lower().startswith("utm_")]
    path = parts.path
    if re.search(r"/index\.(htm|html?)$", path, re.I):
        path = re.sub(r"index\.(htm|html?)$", "", path, flags=re.I)
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urllib.parse.urlencode(query), ""))


def keep_link(text: str, kws: list[str], excludes: list[str]) -> bool:
    if not text or len(text) < 4:
        return False
    if any(x in text for x in excludes):
        return False
    return any(k in text for k in kws)


def looks_like_article(url: str, text: str, block_text: str, patterns: list[str]) -> bool:
    if any(re.search(p, url, re.I) for p in patterns):
        return True
    if extract.date_tokens(block_text):
        return True
    if re.search(r"\.(htm|html?|shtml)$", urllib.parse.urlsplit(url).path, re.I) and len(text) >= 10:
        return True
    return False


def article_text(full_text: str, title: str) -> str:
    """裁掉导航/页脚，只保留正文区域，减少无关日期干扰。"""
    text = full_text
    key = re.sub(r"\s+", "", title)[:14]
    if len(key) >= 6:
        flat = re.sub(r"\s+", "", text)
        idx = flat.find(key)
        if idx > 0:
            # 用字符比例映射回原文位置
            ratio = idx / max(1, len(flat))
            text = text[int(len(text) * ratio * 0.9) :]
    for marker in FOOTER_MARKERS:
        pos = text.find(marker)
        if pos > 200:
            text = text[:pos]
    return text


def parse_date_field(value: str | None) -> _dt.date | None:
    if not value:
        return None
    toks = extract.date_tokens(value)
    if not toks:
        return None
    return toks[0].resolved(_dt.date.today().year, _dt.date.today().month)


# ---------------------------------------------------------------- 单源抓取

class SourceStats:
    def __init__(self, source_id: str):
        self.source_id = source_id
        self.pages_fetched = 0
        self.candidates = 0
        self.new = 0
        self.updated = 0
        self.details = 0
        self.errors: list[str] = []
        self.ok = True
        self.http_status = None


def _collect_candidates(source: dict, pages: list[tuple[str, str]], cfg: dict) -> tuple[list[dict], list[str]]:
    """从已抓取的列表页中筛出候选通知，并发现更多列表页。"""
    kws = cfg["defaults"].get("keywords", [])
    excludes = cfg["defaults"].get("exclude", [])
    patterns = cfg["defaults"].get("list_patterns", [])

    candidates: dict[str, dict] = {}
    extra_pages: list[str] = []
    seq = 0
    keyword_links = 0

    for page_url, html in pages:
        for link in extract.extract_links(html, page_url):
            text = link["text"]
            if not keep_link(text, kws, excludes):
                continue
            keyword_links += 1
            url = normalize_url(link["url"])
            block = link["block_text"]
            if not looks_like_article(url, text, block, patterns):
                # 短标题 + 不像文章 → 可能是栏目导航，作为列表页再抓一层
                if len(text) <= 8 and len(extra_pages) < 4:
                    extra_pages.append(link["url"])
                continue
            if url in candidates:
                continue
            candidates[url] = {
                "url": url,
                "title": extract.clean_title(text),
                "block_text": block,
                "seq": seq,
                "source_id": source["id"],
                "org": source["org"],
                "unit": source["unit"],
                "tier": source.get("tier", ""),
                "level": source.get("level", ""),
            }
            seq += 1
    # 列表页顺序 ≈ 站点排列顺序（多数 CMS 为最新在前），据此决定详情抓取优先级：
    # 带日期的条目优先，其次按列表顺序，保证预算花在最新通知上。
    ordered = sorted(
        candidates.values(),
        key=lambda c: (0 if extract.date_tokens(c["block_text"]) else 1, c["seq"]),
    )
    return ordered, extra_pages, keyword_links


def entry_candidates(source: dict, today: _dt.date) -> list[str]:
    """入口解析：支持 year_url 模板（如 LAMDA 的 recruit-{year}，逐年换路径）。

    顺序为 明年 → 今年 → 去年 → 配置里的固定入口，取第一个能打开的；
    另外对每个入口追加 www / 非 www 变体——国内院系站两种写法常常只有一个能解析。
    """
    base_urls: list[str] = []
    tmpl = source.get("year_url")
    if tmpl:
        for year in (today.year + 1, today.year, today.year - 1):
            base_urls.append(tmpl.format(year=year))
    base_urls.append(source["entry"])

    urls: list[str] = []
    for u in base_urls:
        urls.append(u)
        parts = urllib.parse.urlsplit(u)
        host = parts.netloc
        if host.startswith("www."):
            urls.append(urllib.parse.urlunsplit((parts.scheme, host[4:], parts.path, parts.query, "")))
        else:
            urls.append(urllib.parse.urlunsplit((parts.scheme, "www." + host, parts.path, parts.query, "")))

    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _enrich(record: dict, text: str, cand: dict, pub: _dt.date | None) -> None:
    """从正文抽取发布时间、报名区间、活动时间、招生年份。"""
    record["detail_fetched"] = 1
    pub2, _ = extract.guess_published(text, pub)
    if pub2:
        record["published_at"] = pub2.isoformat()
    base = pub2 or pub or _dt.date.today()
    reg = extract.find_registration(text, base)
    ev = extract.find_event(text, base)
    record["reg_start"] = reg["start"].isoformat() if reg.get("start") else None
    record["deadline"] = reg["deadline"].isoformat() if reg.get("deadline") else None
    record["deadline_raw"] = reg.get("raw") or ""
    record["event_start"] = ev["start"].isoformat() if ev.get("start") else None
    record["event_end"] = ev["end"].isoformat() if ev.get("end") else None
    record["event_raw"] = ev.get("raw") or ""
    record["cycle"] = extract.guess_cycle(cand["title"] + "\n" + text[:200], pub2 or pub)
    if not record["deadline"]:
        # 标题里有时直接写截止日（如「…推免报名6月30日截止」）
        reg2 = extract.find_registration(cand["title"], base)
        if reg2.get("deadline"):
            record["deadline"] = reg2["deadline"].isoformat()
            record["deadline_raw"] = reg2.get("raw") or ""
    if record["deadline"]:
        explicit = bool(re.search(r"20\d{2}\s*[年\-/\.]", record["deadline_raw"] or ""))
        record["confidence"] = "high" if explicit else "medium"


def crawl_source(conn, source: dict, cfg: dict, *, max_details: int = 30, refresh_details: bool = False) -> SourceStats:
    stats = SourceStats(source["id"])
    if source.get("system"):
        return stats  # 报名系统入口只展示，不抓

    res = None
    last_err = None
    for url in entry_candidates(source, _dt.date.today()):
        last_err = http.fetch(url)
        if last_err.ok:
            res = last_err
            break
        stats.errors.append(f"入口 {url}: {last_err.error}")
    stats.http_status = res.status if res else (last_err.status if last_err else None)
    if res is None:
        stats.ok = False
        err = last_err.error if last_err else "no entry"
        db.set_source_status(conn, source["id"], http_status=stats.http_status, ok=False, error=err)
        return stats

    pages: list[tuple[str, str]] = [(res.final_url, res.text)]
    stats.pages_fetched += 1

    if source.get("discover"):
        discovered = extract.discover_list_pages(res.text, res.final_url, limit=3)
        for url in discovered:
            if len(pages) >= 4:
                break
            sub = http.fetch(url)
            if sub.ok:
                pages.append((sub.final_url, sub.text))
                stats.pages_fetched += 1
            else:
                stats.errors.append(f"发现页失败 {url}: {sub.error}")

    candidates, extra_pages, keyword_links = _collect_candidates(source, pages, cfg)

    for url in extra_pages:
        if len(pages) >= 7:
            break
        sub = http.fetch(url)
        if sub.ok:
            stats.pages_fetched += 1
            more, _, _n = _collect_candidates(source, [(sub.final_url, sub.text)], cfg)
            for c in more:
                if c["url"] not in {x["url"] for x in candidates}:
                    candidates.append(c)

    stats.candidates = len(candidates)

    # 「页面即通知」：LAMDA / 上海AI Lab 这类入口页本身就是招生通知，而非列表页
    if not candidates:
        html0 = pages[0][1]
        text0 = extract.html_to_text(html0)
        title0 = extract.best_self_title(html0, text0)
        if title0 and extract.is_self_notice(text0, title0, keyword_links):
            candidates.append({
                "url": normalize_url(pages[0][0]),
                "title": title0,
                "block_text": "",
                "source_id": source["id"],
                "org": source["org"],
                "unit": source["unit"],
                "tier": source.get("tier", ""),
                "level": source.get("level", ""),
                "preloaded_text": text0,
                "note": "页面即通知",
            })
            stats.candidates = 1

    budget = max_details

    for cand in candidates:
        row = conn.execute(
            "SELECT id, detail_fetched, deadline FROM notices WHERE url=?", (cand["url"],)
        ).fetchone()
        need_detail = refresh_details or row is None or not row["detail_fetched"]

        record = dict(cand)
        record["content_key"] = content_key(cand["url"])
        record["detail_fetched"] = 0
        record["published_at"] = None
        record["reg_start"] = None
        record["deadline"] = None
        record["deadline_raw"] = None
        record["event_start"] = None
        record["event_end"] = None
        record["event_raw"] = None
        record["cycle"] = ""
        record["confidence"] = "low"
        record["category"] = extract.classify(cand["title"])

        # 列表页块文本里往往直接带发布日期
        pub = parse_date_field(cand["block_text"])
        if pub:
            record["published_at"] = pub.isoformat()

        if need_detail and budget > 0:
            if cand.get("preloaded_text"):
                _enrich(record, article_text(cand["preloaded_text"], cand["title"]), cand, pub)
            else:
                detail = http.fetch(cand["url"])
                budget -= 1
                stats.details += 1
                if detail.ok:
                    _enrich(record, article_text(extract.html_to_text(detail.text), cand["title"]), cand, pub)
                else:
                    stats.errors.append(f"详情失败 {cand['url']}: {detail.error}")
        elif row is not None:
            # 之前抓过的：保留已解析结果，仅更新 last_seen
            existing = conn.execute("SELECT * FROM notices WHERE url=?", (cand["url"],)).fetchone()
            for f in ("published_at", "reg_start", "deadline", "deadline_raw", "event_start",
                      "event_end", "event_raw", "cycle", "confidence", "category"):
                record[f] = existing[f]

        # 兜底：详情页没给出截止日时，列表页条目里常直接写着「报名截止：X月X日」
        if not record["deadline"] and cand.get("block_text"):
            fb = extract.find_registration(cand["block_text"], parse_date_field(record["published_at"]))
            if fb.get("deadline"):
                record["deadline"] = fb["deadline"].isoformat()
                record["deadline_raw"] = fb.get("raw") or ""
                record["reg_start"] = fb["start"].isoformat() if fb.get("start") else record["reg_start"]
                record["confidence"] = "medium"

        if not record["cycle"]:
            record["cycle"] = extract.guess_cycle(cand["title"], parse_date_field(record["published_at"]))

        status, _nid = db.upsert_notice(conn, record)
        if status == "new":
            stats.new += 1
        elif status == "updated":
            stats.updated += 1

    db.set_source_status(conn, source["id"], http_status=res.status, ok=True, error=None)
    return stats


# ---------------------------------------------------------------- 全量抓取

def crawl_all(conn, cfg: dict, *, only: list[str] | None = None, max_details: int = 30,
              refresh_details: bool = False, dry_run: bool = False) -> dict:
    sources = cfg["sources"]
    if only:
        wanted = set(only)
        sources = [s for s in sources if s["id"] in wanted or s["org"] in wanted]

    run_id = db.start_run(conn)
    db.sync_sources(conn, cfg["sources"])

    summary = {"run_id": run_id, "sources": 0, "ok": 0, "failed": 0, "new": 0, "updated": 0, "details": 0, "errors": []}

    for src in sources:
        if src.get("system"):
            continue
        summary["sources"] += 1
        try:
            st = crawl_source(conn, src, cfg, max_details=max_details, refresh_details=refresh_details)
        except Exception as exc:  # noqa: BLE001  单源异常不应中断整轮
            db.set_source_status(conn, src["id"], ok=False, error=f"{type(exc).__name__}: {exc}")
            summary["failed"] += 1
            summary["errors"].append(f"{src['org']} {src['unit']}: {type(exc).__name__}: {exc}")
            continue
        summary["new"] += st.new
        summary["updated"] += st.updated
        summary["details"] += st.details
        if st.ok:
            summary["ok"] += 1
        else:
            summary["failed"] += 1
            summary["errors"].extend(f"{src['org']} {src['unit']}: {e}" for e in st.errors[:2])

    db.refresh_counts(conn)
    if not dry_run:
        db.finish_run(conn, run_id, summary["new"], summary["updated"], summary["failed"],
                      json.dumps({"details": summary["details"]}, ensure_ascii=False))
    return summary