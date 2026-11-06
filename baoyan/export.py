"""导出层：生成 web/data.json（前端数据）与 web/baoyan.ics（日历提醒）。"""

from __future__ import annotations

import datetime as _dt
import json
import os

from . import crawl, db

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(HERE, "web")

SOON_DAYS = 7


def _status(n: dict, today: _dt.date) -> tuple[str, int | None]:
    deadline = _parse(n.get("deadline"))
    start = _parse(n.get("reg_start"))
    if deadline is None:
        pub = _parse(n.get("published_at"))
        if pub and (today - pub).days <= 60:
            return "unknown_new", None
        return "unknown", None
    days = (deadline - today).days
    if days < 0:
        return "closed", days
    if days <= SOON_DAYS:
        return "urgent", days
    if start and start > today:
        return "upcoming", days
    return "open", days


def _parse(value: str | None) -> _dt.date | None:
    if not value:
        return None
    try:
        return _dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def build_payload(conn, *, include_closed_days: int = 900) -> dict:
    today = _dt.date.today()
    sources = [dict(r) for r in conn.execute("SELECT * FROM sources ORDER BY tier, org, unit")]

    notices = []
    for row in conn.execute("SELECT * FROM notices WHERE hidden=0"):
        n = dict(row)
        status, days = _status(n, today)
        deadline = _parse(n.get("deadline"))
        if deadline and (today - deadline).days > include_closed_days:
            continue
        n["status"] = status
        n["days_left"] = days
        n["is_new"] = (today - _parse(n["first_seen"][:10])).days <= 3 if n.get("first_seen") else False
        n["source_name"] = f"{n['org']} {n['unit']}" if n.get("unit") else n["org"]
        notices.append(n)

    notices.sort(key=lambda n: (
        n["deadline"] is None,
        n["deadline"] or "9999",
        -(int((n["published_at"] or "0000").replace("-", "") or 0) if n.get("published_at") else 0),
    ))

    events = [dict(r) for r in conn.execute(
        "SELECT * FROM events ORDER BY ts DESC LIMIT 120"
    )]

    tier_counts: dict[str, int] = {}
    for n in notices:
        tier_counts[n.get("tier") or "?"] = tier_counts.get(n.get("tier") or "?", 0) + 1

    last_run = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()

    open_notices = [n for n in notices if n["status"] in ("urgent", "open", "upcoming")]
    urgent = [n for n in notices if n["status"] == "urgent"]

    return {
        "generated_at": db.now_iso(),
        "today": today.isoformat(),
        "stats": {
            "sources_total": len([s for s in sources if not s["is_system"]]),
            "sources_ok": len([s for s in sources if s["ok"] == 1]),
            "sources_failed": len([s for s in sources if s["ok"] == 0]),
            "notices": len(notices),
            "open": len(open_notices),
            "urgent": len(urgent),
            "upcoming": len([n for n in notices if n["status"] == "upcoming"]),
            "closed": len([n for n in notices if n["status"] == "closed"]),
            "unknown": len([n for n in notices if n["status"].startswith("unknown")]),
            "by_category": _count_by(notices, "category"),
            "by_tier": tier_counts,
            "last_run": {
                "started": last_run["started"] if last_run else None,
                "finished": last_run["finished"] if last_run else None,
                "new": last_run["new_count"] if last_run else 0,
            },
        },
        "notices": notices,
        "sources": sources,
        "events": events,
    }


def _count_by(items: list[dict], key: str) -> dict:
    out: dict[str, int] = {}
    for it in items:
        k = it.get(key) or "其他"
        out[k] = out.get(k, 0) + 1
    return out


def load_manual_notices(path: str | None = None) -> list[dict]:
    """人工维护的补充通知（抓不到的渠道或线下消息），格式见 config/manual_notices.json。"""
    path = path or os.path.join(HERE, "config", "manual_notices.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return []
    return data.get("notices", []) if isinstance(data, dict) else []


def merge_manual(payload: dict, manual: list[dict]) -> dict:
    existing = {n["url"] for n in payload["notices"]}
    today = _dt.date.today()
    for m in manual:
        if m.get("url") in existing:
            continue
        n = {
            "id": None,
            "source_id": m.get("source_id", "manual"),
            "org": m.get("org", ""),
            "unit": m.get("unit", ""),
            "tier": m.get("tier", "T3"),
            "level": m.get("level", "院"),
            "url": m.get("url", ""),
            "title": m.get("title", ""),
            "category": m.get("category", "其他"),
            "cycle": m.get("cycle", ""),
            "published_at": m.get("published_at"),
            "reg_start": m.get("reg_start"),
            "deadline": m.get("deadline"),
            "deadline_raw": m.get("deadline_raw", "人工录入"),
            "event_start": m.get("event_start"),
            "event_end": m.get("event_end"),
            "event_raw": "",
            "first_seen": m.get("published_at") or db.now_iso(),
            "last_seen": db.now_iso(),
            "detail_fetched": 0,
            "confidence": "manual",
            "note": m.get("note", ""),
            "manual": True,
            "source_name": f"{m.get('org','')} {m.get('unit','')}".strip(),
        }
        n["status"], n["days_left"] = _status(n, today)
        n["is_new"] = False
        payload["notices"].append(n)
    payload["notices"].sort(key=lambda x: (x["deadline"] is None, x["deadline"] or "9999"))
    payload["stats"]["notices"] = len(payload["notices"])
    payload["stats"]["open"] = len([n for n in payload["notices"] if n["status"] in ("urgent", "open", "upcoming")])
    payload["stats"]["urgent"] = len([n for n in payload["notices"] if n["status"] == "urgent"])
    return payload


def write_site(conn, *, include_closed_days: int = 900) -> dict:
    payload = build_payload(conn, include_closed_days=include_closed_days)
    payload = merge_manual(payload, load_manual_notices())
    os.makedirs(WEB_DIR, exist_ok=True)
    out = os.path.join(WEB_DIR, "data.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    write_ics(payload, os.path.join(WEB_DIR, "baoyan.ics"))
    return payload


# ---------------------------------------------------------------- ICS

def _ics_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def write_ics(payload: dict, path: str) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Baoyan Monitor//Deadline Tracker//CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:保研通知截止日",
        "X-WR-TIMEZONE:Asia/Shanghai",
    ]
    stamp = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    count = 0
    for n in payload["notices"]:
        if not n.get("deadline") or n["status"] == "closed":
            continue
        d = n["deadline"].replace("-", "")
        uid = f"baoyan-{count}-{d}-{abs(hash(n['url'])) % 10**8}@local"
        desc = [f"来源: {n.get('source_name','')}", f"类型: {n.get('category','')}"]
        if n.get("deadline_raw"):
            desc.append(f"原文: {n['deadline_raw']}")
        if n.get("url"):
            desc.append(f"链接: {n['url']}")
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{stamp}Z",
            f"DTSTART;VALUE=DATE:{d}",
            f"SUMMARY:{_ics_escape('[保研截止] ' + n['title'])}",
            f"DESCRIPTION:{_ics_escape(chr(10).join(desc))}",
            f"URL:{n.get('url','')}",
            "BEGIN:VALARM",
            "TRIGGER:-P3D",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{_ics_escape(n['title'])}",
            "END:VALARM",
            "END:VEVENT",
        ]
        count += 1
    lines.append("END:VCALENDAR")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write("\n".join(lines))
    return path