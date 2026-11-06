"""SQLite 存储层：源状态、通知、变更日志、运行记录。"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id            TEXT PRIMARY KEY,
    org           TEXT NOT NULL,
    unit          TEXT NOT NULL,
    tier          TEXT,
    level         TEXT,
    entry         TEXT,
    is_system     INTEGER DEFAULT 0,
    discover      INTEGER DEFAULT 0,
    expected      TEXT,
    history       TEXT,
    last_checked  TEXT,
    last_changed  TEXT,
    http_status   INTEGER,
    ok            INTEGER,
    error         TEXT,
    notice_count  INTEGER DEFAULT 0,
    new_count     INTEGER DEFAULT 0,
    follow        INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS notices (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id      TEXT NOT NULL,
    org            TEXT NOT NULL,
    unit           TEXT,
    tier           TEXT,
    level          TEXT,
    url            TEXT UNIQUE NOT NULL,
    title          TEXT NOT NULL,
    category       TEXT,
    cycle          TEXT,
    published_at   TEXT,
    reg_start      TEXT,
    deadline       TEXT,
    deadline_raw   TEXT,
    event_start    TEXT,
    event_end      TEXT,
    event_raw      TEXT,
    first_seen     TEXT NOT NULL,
    last_seen      TEXT NOT NULL,
    detail_fetched INTEGER DEFAULT 0,
    confidence     TEXT,
    note           TEXT,
    hidden         INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_notices_deadline ON notices(deadline);
CREATE INDEX IF NOT EXISTS idx_notices_source ON notices(source_id);

CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    type      TEXT NOT NULL,      -- new_notice | updated_notice | source_error | source_recovered
    notice_id INTEGER,
    source_id TEXT,
    title     TEXT,
    detail    TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started     TEXT, finished TEXT,
    new_count   INTEGER DEFAULT 0,
    upd_count   INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    note        TEXT
);
"""

DIFF_FIELDS = ("title", "category", "cycle", "published_at", "reg_start", "deadline", "event_start", "event_end")
# 只有这几个字段变化才值得写变更日志——它们代表「官方改了关键日期」；
# 标题/类型的差异更可能来自我们自己的解析改进，静默更新即可，否则日志会被噪声淹没。
ALERT_FIELDS = ("deadline", "reg_start", "event_start", "event_end", "published_at")


def now_iso() -> str:
    return _dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def connect(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """为既有数据库补列（CREATE TABLE IF NOT EXISTS 不会改已存在的表）。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(notices)")}
    for name, ddl in (
        ("content_key", "ALTER TABLE notices ADD COLUMN content_key TEXT"),
        ("alt_urls", "ALTER TABLE notices ADD COLUMN alt_urls TEXT"),
    ):
        if name not in cols:
            conn.execute(ddl)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notices_ck ON notices(content_key)")
    conn.commit()


def sync_sources(conn: sqlite3.Connection, sources: list[dict]) -> None:
    for s in sources:
        conn.execute(
            """INSERT INTO sources (id, org, unit, tier, level, entry, is_system, discover, expected, history)
               VALUES (:id, :org, :unit, :tier, :level, :entry, :is_system, :discover, :expected, :history)
               ON CONFLICT(id) DO UPDATE SET
                 org=excluded.org, unit=excluded.unit, tier=excluded.tier, level=excluded.level,
                 entry=excluded.entry, is_system=excluded.is_system, discover=excluded.discover,
                 expected=excluded.expected, history=excluded.history""",
            {
                "id": s["id"],
                "org": s["org"],
                "unit": s["unit"],
                "tier": s.get("tier", ""),
                "level": s.get("level", ""),
                "entry": s.get("entry", ""),
                "is_system": 1 if s.get("system") else 0,
                "discover": 1 if s.get("discover") else 0,
                "expected": json.dumps(s.get("expected", []), ensure_ascii=False),
                "history": s.get("history", ""),
            },
        )
    conn.commit()


def set_source_status(conn, source_id, *, http_status=None, ok=None, error=None) -> None:
    ts = now_iso()
    row = conn.execute("SELECT ok, unit, org FROM sources WHERE id=?", (source_id,)).fetchone()
    prev_ok = row["ok"] if row else None
    conn.execute(
        "UPDATE sources SET last_checked=?, http_status=?, ok=?, error=? WHERE id=?",
        (ts, http_status, None if ok is None else (1 if ok else 0), error, source_id),
    )
    if prev_ok == 1 and ok is False:
        upsert_event(conn, "source_error", None, source_id, row["org"] + " " + row["unit"], error or "抓取失败")
    elif prev_ok == 0 and ok is True:
        upsert_event(conn, "source_recovered", None, source_id, row["org"] + " " + row["unit"], "恢复正常")
    conn.commit()


def upsert_notice(conn: sqlite3.Connection, n: dict) -> tuple[str, int]:
    """返回 (status, notice_id)，status ∈ {new, updated, same}。

    去重分两级：先按 URL，再按 content_key（文章 ID）——后者能识别
    「同一篇文章挂在两个栏目下」的情况，此时记下备用链接而不新增一条。
    """
    ts = now_iso()
    row = conn.execute("SELECT * FROM notices WHERE url=?", (n["url"],)).fetchone()

    if row is None and n.get("content_key"):
        row = conn.execute("SELECT * FROM notices WHERE content_key=?", (n["content_key"],)).fetchone()
        if row is not None:
            alts = {u for u in (row["alt_urls"] or "").split("\u0001") if u and u != n["url"]}
            alts.add(n["url"])
            conn.execute(
                "UPDATE notices SET alt_urls=?, last_seen=? WHERE id=?",
                ("\u0001".join(sorted(alts)), ts, row["id"]),
            )

    if row is None:
        cur = conn.execute(
            """INSERT INTO notices (source_id, org, unit, tier, level, url, title, category, cycle,
                   published_at, reg_start, deadline, deadline_raw, event_start, event_end, event_raw,
                   first_seen, last_seen, detail_fetched, confidence, note, content_key)
               VALUES (:source_id,:org,:unit,:tier,:level,:url,:title,:category,:cycle,
                   :published_at,:reg_start,:deadline,:deadline_raw,:event_start,:event_end,:event_raw,
                   :first_seen,:last_seen,:detail_fetched,:confidence,:note,:content_key)""",
            {**{k: n.get(k) for k in
                ("source_id","org","unit","tier","level","url","title","category","cycle",
                 "published_at","reg_start","deadline","deadline_raw","event_start","event_end",
                 "event_raw","detail_fetched","confidence","note","content_key")},
             "first_seen": ts, "last_seen": ts},
        )
        nid = int(cur.lastrowid)
        upsert_event(conn, "new_notice", nid, n["source_id"], n["title"], n.get("deadline") or "")
        conn.commit()
        return "new", nid

    changes = {}
    for f in DIFF_FIELDS:
        new_v = n.get(f)
        if new_v in (None, "") or new_v is False:
            continue
        old_v = row[f]
        if f == "detail_fetched":
            continue
        if old_v in (None, "") and new_v:
            changes[f] = {"from": old_v, "to": new_v}
        elif old_v != new_v and f in ("deadline", "reg_start", "title", "event_start", "event_end", "published_at"):
            changes[f] = {"from": old_v, "to": new_v}

    conn.execute("UPDATE notices SET last_seen=? WHERE id=?", (ts, row["id"]))
    if changes:
        for f, ch in changes.items():
            conn.execute(f"UPDATE notices SET {f}=? WHERE id=?", (ch["to"], row["id"]))
        if n.get("detail_fetched"):
            conn.execute("UPDATE notices SET detail_fetched=1 WHERE id=?", (row["id"],))
        if n.get("deadline_raw"):
            conn.execute("UPDATE notices SET deadline_raw=? WHERE id=?", (n["deadline_raw"], row["id"]))
        alert = {f: ch for f, ch in changes.items() if f in ALERT_FIELDS}
        if alert:
            upsert_event(
                conn, "updated_notice", row["id"], n["source_id"], n["title"],
                json.dumps(alert, ensure_ascii=False, default=str),
            )
        conn.commit()
        return "updated", row["id"]

    conn.commit()
    return "same", row["id"]


def upsert_event(conn, type_, notice_id, source_id, title, detail="") -> None:
    conn.execute(
        "INSERT INTO events (ts, type, notice_id, source_id, title, detail) VALUES (?,?,?,?,?,?)",
        (now_iso(), type_, notice_id, source_id, title or "", detail or ""),
    )


def refresh_counts(conn) -> None:
    conn.execute(
        """UPDATE sources SET notice_count=(
             SELECT COUNT(*) FROM notices WHERE notices.source_id = sources.id AND hidden=0),
           new_count=0"""
    )
    conn.commit()


def start_run(conn) -> int:
    cur = conn.execute("INSERT INTO runs (started) VALUES (?)", (now_iso(),))
    conn.commit()
    return int(cur.lastrowid)


def finish_run(conn, run_id: int, new_count: int, upd_count: int, error_count: int, note: str = "") -> None:
    conn.execute(
        "UPDATE runs SET finished=?, new_count=?, upd_count=?, error_count=?, note=? WHERE id=?",
        (now_iso(), new_count, upd_count, error_count, note, run_id),
    )
    conn.commit()