"""命令行入口。

用法示例：
    python -m baoyan run                       # 抓取全部渠道并重建站点
    python -m baoyan run --only pku-sai nju-lamda
    python -m baoyan run --max-details 5       # 快速巡检（每源最多抓 5 篇详情）
    python -m baoyan export                    # 只用现有数据库重建站点
    python -m baoyan list --open               # 终端查看未截止通知
    python -m baoyan serve --port 8000         # 本地起服务（打开跟踪站）
    python -m baoyan check                     # 自检：解析器 + 数据源连通性
    python -m baoyan prune --apply             # 清理误入库的站名/栏目页条目
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import http.server
import json
import os
import socketserver
import sys

from . import crawl, db, export

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(HERE, "data", "baoyan.db")
WEB_DIR = os.path.join(HERE, "web")

STATUS_LABEL = {
    "urgent": "即将截止",
    "open": "进行中",
    "upcoming": "未开始",
    "closed": "已截止",
    "unknown": "待定",
    "unknown_new": "新发布",
}


def cmd_run(args) -> int:
    cfg = crawl.load_config(args.config)
    conn = db.connect(args.db)
    db.sync_sources(conn, cfg["sources"])
    print(f"[run] 渠道 {len(cfg['sources'])} 个，开始抓取（详情上限 {args.max_details}/源）…")
    summary = crawl.crawl_all(
        conn,
        cfg,
        only=args.only or None,
        max_details=args.max_details,
        refresh_details=args.refresh_details,
    )
    print(
        f"[run] 完成：成功 {summary['ok']}/{summary['sources']} 源，"
        f"新通知 {summary['new']}，更新 {summary['updated']}，抓详情 {summary['details']} 篇"
    )
    for err in summary["errors"][:10]:
        print(f"  ! {err}")
    payload = export.write_site(conn)
    print(
        f"[run] 站点已更新：通知 {payload['stats']['notices']} 条，"
        f"未截止 {payload['stats']['open']} 条（即将截止 {payload['stats']['urgent']} 条）"
    )
    print(f"[run] 打开: {os.path.join(WEB_DIR, 'index.html')}")
    conn.close()
    return 0


def cmd_export(args) -> int:
    conn = db.connect(args.db)
    payload = export.write_site(conn, include_closed_days=args.closed_days)
    print(json.dumps(payload["stats"], ensure_ascii=False, indent=2))
    conn.close()
    return 0


def cmd_list(args) -> int:
    conn = db.connect(args.db)
    rows = conn.execute("SELECT * FROM notices WHERE hidden=0 ORDER BY deadline IS NULL, deadline").fetchall()
    today = _dt.date.today()
    shown = 0
    for r in rows:
        if args.open:
            if not r["deadline"] or _dt.date.fromisoformat(r["deadline"]) < today:
                continue
        days = ""
        if r["deadline"]:
            d = (_dt.date.fromisoformat(r["deadline"]) - today).days
            days = f"{d:>4}天" if d >= 0 else "已截止"
        else:
            days = "  待定"
        cat = (r["category"] or "")[:5]
        print(f"{days} | {r['deadline'] or '---- -- --'} | {r['tier'] or '--'} | {cat:<6} | {r['org']}{r['unit']} | {r['title'][:44]}")
        shown += 1
        if args.limit and shown >= args.limit:
            break
    print(f"\n共 {shown} 条")
    conn.close()
    return 0


def cmd_serve(args) -> int:
    if not os.path.exists(os.path.join(WEB_DIR, "data.json")):
        print("[serve] 尚未生成站点数据，请先运行 python -m baoyan run", file=sys.stderr)
        return 1

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=WEB_DIR, **kw)

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def log_message(self, fmt, *a):  # 静音
            pass

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer((args.host, args.port), Handler) as httpd:
        print(f"[serve] http://{args.host}:{args.port}/  (Ctrl+C 退出)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[serve] 已停止")
    return 0


def cmd_check(args) -> int:
    """自检：解析器单测 + 源连通性概览。"""
    from . import extract

    samples = [
        ("关于举办2026年优秀大学生夏令营的通知\n报名时间：2026年6月20日至6月30日。", "夏令营", "2026-06-30"),
        ("2027年接收推荐免试研究生（含直博生）工作通知\n申请截止时间：2026年9月15日。", "推免/九推", "2026-09-15"),
        ("2026年冬令营报名通知\n报名日期：2026年12月1日-12月20日", "冬令营", "2026-12-20"),
        ("人工智能研究院2026年暑期学校招生\n材料提交截止：6月18日", "暑期学校", "2026-06-18"),
        # 回归用例：跨句日期不能当截止日（只取提示词同分句内的日期）
        ("LAMDA招收2027年秋季入学研究生说明\n申请材料提交截止时间：2026年7月31日；面谈通知时间：2026年8月5日。",
         "实验室招生", "2026-07-31"),
        # 回归用例：『6月1日上午8:00起至6月15日』区间需完整合并
        ("关于举办2026年优秀大学生夏令营的通知\n报名时间：2026年6月1日上午8:00起至2026年6月15日下午17:00系统自动关闭。",
         "夏令营", "2026-06-15"),
    ]
    ok = True
    for text, want_cat, want_deadline in samples:
        cat = extract.classify(text.split("\n")[0])
        pub = None
        reg = extract.find_registration(text, pub)
        got = reg["deadline"].isoformat() if reg.get("deadline") else None
        mark = "OK " if (cat == want_cat and got == want_deadline) else "FAIL"
        if mark == "FAIL":
            ok = False
        print(f"[check] {mark} 类型={cat}(期望 {want_cat}) 截止={got}(期望 {want_deadline})")

    # 标题清洗回归用例：列表页 <a> 常把标题与正文摘要整块包住，序号/日期/装饰语也需剥掉
    title_cases = [
        ("08-25 2026 清华大学交叉信息研究院2027年博士研究生招生综合考核及录取办法 一、资格审查",
         "清华大学交叉信息研究院2027年博士研究生招生综合考核及录取办法"),
        ("哈尔滨工业大学关于2027年接收优秀应届本科毕业生免试攻读研究生报名的通知 哈",
         "哈尔滨工业大学关于2027年接收优秀应届本科毕业生免试攻读研究生报名的通知"),
        ("26 2025.06 欢迎报考 | 上海交通大学设计学院2026年研究生“优才夏令营”方案",
         "上海交通大学设计学院2026年研究生“优才夏令营”方案"),
        ("05.22/2024 上海交通大学中美物流研究院全日制物流工程与管理推免夏令营招生宣传手册（2025",
         "上海交通大学中美物流研究院全日制物流工程与管理推免夏令营招生宣传手册"),
        ("12、第六届稀土科技夏令营（中国科学院赣江创新研究院）",
         "第六届稀土科技夏令营（中国科学院赣江创新研究院）"),
        ("2026 年夏令营报名通知", "2026 年夏令营报名通知"),
        ("北京大学软件与微电子学院2026年全国优秀大学生夏令营通知 2026-06-18",
         "北京大学软件与微电子学院2026年全国优秀大学生夏令营通知"),
    ]
    for raw, want in title_cases:
        got = extract.clean_title(raw)
        mark = "OK " if got == want else "FAIL"
        if mark == "FAIL":
            ok = False
        print(f"[check] {mark} 标题清洗 → {got!r}")

    # 「页面即通知」闸门：站名/列表页必须挡住，独立招生页要放过
    gate_cases = [
        ("夏令营信息 2026年夏令营 报名 2026年6月1日", "夏令营信息-清华研究生招生网", 0, False),
        ("招生 2026年招生 报名 2026年6月1日", "清华大学深圳国际研究生院", 0, False),
        ("申请材料提交截止时间：2026年7月31日。", "某某大学2026年夏令营招生通知", 5, False),
        ("LAMDA招收2027年秋季入学研究生说明 申请材料提交截止时间：2026年7月31日。",
         "LAMDA招收2027年秋季入学研究生说明", 0, True),
    ]
    for text, title, links, want in gate_cases:
        got = extract.is_self_notice(text, title, links)
        mark = "OK " if got == want else "FAIL"
        if mark == "FAIL":
            ok = False
        print(f"[check] {mark} 页面即通知闸门({title[:16]}…) → {got}(期望 {want})")

    if args.net:
        cfg = crawl.load_config(args.config)
        print(f"\n[check] 连通性巡检（{len(cfg['sources'])} 源，详情不抓）…")
        conn = db.connect(args.db)
        db.sync_sources(conn, cfg["sources"])
        for src in cfg["sources"]:
            if src.get("system"):
                continue
            from . import http
            res = http.fetch(src["entry"])
            db.set_source_status(conn, src["id"], http_status=res.status, ok=res.ok, error=res.error)
            flag = "OK  " if res.ok else "FAIL"
            print(f"[check] {flag} {src['org']} {src['unit']} -> {res.status or res.error}")
        conn.close()
    return 0 if ok else 2


def cmd_prune(args) -> int:
    """清理误入库的条目 + 跨栏目重复条目（配置的排除词会随使用演进，旧记录需要能删掉）。"""
    from . import extract

    conn = db.connect(args.db)
    cfg = crawl.load_config(args.config)
    excludes = cfg["defaults"].get("exclude", [])

    # 1) 回填 content_key（既有库没有这一列），供跨栏目去重使用
    backfill = 0
    for r in conn.execute("SELECT id, url FROM notices WHERE content_key IS NULL OR content_key=''"):
        conn.execute("UPDATE notices SET content_key=? WHERE id=?", (crawl.content_key(r["url"]), r["id"]))
        backfill += 1
    if backfill:
        conn.commit()
        print(f"[prune] 回填文章标识 {backfill} 条")

    # 2) 跨栏目重复：同一篇文章被挂到多个栏目下（文章 ID 相同 / 同单位同标题）
    dup_groups: dict[str, list] = {}
    for r in conn.execute("SELECT * FROM notices ORDER BY id"):
        key = r["content_key"] or crawl.content_key(r["url"])
        dup_groups.setdefault(key, []).append(r)
    title_groups: dict[tuple, list] = {}
    for r in conn.execute("SELECT * FROM notices ORDER BY id"):
        title_groups.setdefault((r["org"], r["unit"] or "", r["title"]), []).append(r)

    def _score(r) -> tuple:
        # 保留「有截止日 > 抓过详情 > 抓到的信息更全 > 更早发现」的那条
        return (
            1 if r["deadline"] else 0,
            r["detail_fetched"] or 0,
            sum(1 for f in ("published_at", "reg_start", "event_start", "deadline_raw") if r[f]),
            1 if r["confidence"] in ("high", "medium") else 0,
            -(r["id"] or 0),
        )

    doomed: dict[int, str] = {}
    for groups in (list(dup_groups.values()), list(title_groups.values())):
        for rows in groups:
            if len(rows) < 2:
                continue
            keep = max(rows, key=_score)
            alts = {u for u in (keep["alt_urls"] or "").split("\u0001") if u}
            for r in rows:
                if r["id"] == keep["id"]:
                    continue
                if r["id"] in doomed:
                    continue
                doomed[r["id"]] = f"重复于 #{keep['id']}"
                alts.add(r["url"])
            if alts:
                conn.execute(
                    "UPDATE notices SET alt_urls=? WHERE id=?",
                    ("\u0001".join(sorted(alts)), keep["id"]),
                )

    if doomed:
        print(f"[prune] 跨栏目/同标题重复 {len(doomed)} 条（保留信息最全的一条，其余链接记为同源）")

    # 3) 站名/栏目页、命中排除词
    victims = []
    for r in conn.execute("SELECT id, title, url, org, unit, deadline, note FROM notices"):
        t = r["title"] or ""
        why = None
        if r["id"] in doomed:
            why = doomed[r["id"]]
        elif extract.SITE_NAME_HINT.search(t):
            why = "站名/栏目页"
        elif any(x in t for x in excludes):
            why = "命中排除词"
        elif len(t) < 8 and extract.classify(t) == "其他":
            why = "导航标签"
        elif re.search(r"(学院|大学|学部|研究院|实验室|研究所)$", t) and not extract.NOTICE_TITLE_HINT.search(t):
            why = "疑似机构名"
        if why:
            victims.append((r, why))

    if not victims:
        print("[prune] 无需清理")
        conn.commit()
        conn.close()
        return 0

    print(f"[prune] 命中 {len(victims)} 条：")
    for r, why in victims[: args.limit or 30]:
        print(f"  - [{why}] {r['org']}{r['unit'] or ''} | {r['title'][:50]}")

    if not args.apply:
        print("\n[prune] 以上为预览。加 --apply 实际删除。")
        conn.close()
        return 0

    for r, _why in victims:
        conn.execute("DELETE FROM notices WHERE id=?", (r["id"],))
    conn.commit()
    db.refresh_counts(conn)
    print(f"\n[prune] 已删除 {len(victims)} 条")
    conn.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="baoyan", description="保研官方渠道监控")
    p.add_argument("--db", default=DEFAULT_DB, help="SQLite 数据库路径")
    p.add_argument("--config", default=None, help="渠道配置路径")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="抓取 + 重建站点")
    r.add_argument("--only", nargs="*", help="只抓指定渠道 id 或院校名")
    r.add_argument("--max-details", type=int, default=30, help="每个源最多抓多少篇详情")
    r.add_argument("--refresh-details", action="store_true", help="强制重抓所有详情（慢）")
    r.set_defaults(func=cmd_run)

    e = sub.add_parser("export", help="仅重建站点")
    e.add_argument("--closed-days", type=int, default=900, help="保留多少天内的已截止通知")
    e.set_defaults(func=cmd_export)

    l = sub.add_parser("list", help="终端列出通知")
    l.add_argument("--open", action="store_true", help="只看未截止")
    l.add_argument("--limit", type=int, default=0)
    l.set_defaults(func=cmd_list)

    s = sub.add_parser("serve", help="本地预览站点")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(func=cmd_serve)

    pr = sub.add_parser("prune", help="清理误入库的站名/栏目页等条目")
    pr.add_argument("--apply", action="store_true", help="实际删除（默认仅预览）")
    pr.add_argument("--limit", type=int, default=30, help="预览条数")
    pr.set_defaults(func=cmd_prune)

    c = sub.add_parser("check", help="自检")
    c.add_argument("--net", action="store_true", help="附带源连通性巡检")
    c.set_defaults(func=cmd_check)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())