"""HTML 解析与信息抽取。

设计原则：抽取结果一律保留「原文片段」（raw snippet），前端展示时可供人工核对——
正则必然有误差，透明比"看起来准确"更重要。
"""

from __future__ import annotations

import datetime as _dt
import re
import urllib.parse
from html.parser import HTMLParser

# ---------------------------------------------------------------- 文本化

_BLOCK_TAGS = {
    "p", "div", "li", "tr", "td", "th", "br", "h1", "h2", "h3", "h4",
    "h5", "h6", "table", "ul", "ol", "section", "article", "header",
    "footer", "nav", "dl", "dt", "dd", "blockquote", "pre", "hr",
}
_SKIP_TAGS = {"script", "style", "noscript", "svg", "iframe"}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS and self._skip_depth == 0:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BLOCK_TAGS and self._skip_depth == 0:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001  容错：坏 HTML 也要能出文本
        pass
    text = "".join(parser.parts)
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*(\n\s*)+", "\n", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


# ---------------------------------------------------------------- 链接抽取

class _LinkExtractor(HTMLParser):
    """抽取 <a> 链接，并记录其所在『块』（li/tr/div/p）的整块文本。

    列表页通常在链接同一行/同一块里放发布日期，块文本可用于兜底提取。
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[dict] = []
        self._skip_depth = 0
        self._a_stack: list[dict] = []
        self._block_stack: list[dict] = []
        self._a_text: list[list[str]] = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        attr = {k.lower(): (v or "") for k, v in attrs}
        if tag == "a":
            self._a_stack.append({"href": attr.get("href", ""), "title": attr.get("title", "")})
            self._a_text.append([])
        elif tag in {"li", "tr", "div", "p", "td", "dd", "h3", "h4"}:
            self._block_stack.append({"tag": tag, "buf": [], "anchors": []})

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag == "a" and self._a_stack:
            info = self._a_stack.pop()
            text = _clean_text("".join(self._a_text.pop()))
            anchor = {
                "href": info["href"],
                "title_attr": info["title"],
                "text": text,
                "block_text": "",
            }
            idx = len(self.links)
            self.links.append(anchor)
            for blk in self._block_stack:
                blk["anchors"].append(idx)
        for i in range(len(self._block_stack) - 1, -1, -1):
            if self._block_stack[i]["tag"] == tag:
                blk = self._block_stack.pop(i)
                block_text = _clean_text("".join(blk["buf"]))
                for idx in blk["anchors"]:
                    if not self.links[idx]["block_text"]:
                        self.links[idx]["block_text"] = block_text
                break

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._a_stack:
            for buf in self._a_text:
                buf.append(data)
        for blk in self._block_stack:
            blk["buf"].append(data)


def _clean_text(s: str) -> str:
    s = s.replace("\u3000", " ").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def extract_links(html: str, base_url: str) -> list[dict]:
    parser = _LinkExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001
        pass
    out: list[dict] = []
    seen: set[str] = set()
    for link in parser.links:
        href = (link["href"] or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "void(")):
            continue
        abs_url = urllib.parse.urljoin(base_url, href)
        if not abs_url.lower().startswith(("http://", "https://")):
            continue
        abs_url = abs_url.split("#")[0]
        if abs_url in seen:
            continue
        seen.add(abs_url)
        text = link["text"] or _clean_text(link["title_attr"])
        if not text:
            continue
        out.append({"url": abs_url, "text": text, "block_text": link["block_text"]})
    return out


# ---------------------------------------------------------------- 分类

CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("冬令营", ["冬令营", "冬季学校", "冬季学期", "冬校"]),
    ("夏令营", ["夏令营", "优秀大学生", "优秀学生暑期", "大学生夏令营"]),
    ("暑期学校", ["暑期学校", "暑期研修", "研修班", "暑期班", "暑期夏令营", "夏令营"]),
    ("研学营", ["研学营", "研学活动", "开放日", "暑期研学"]),
    ("预推免", ["预推免", "推免预报名", "预报名", "预推免生"]),
    ("推免/九推", ["推免", "推荐免试", "免试攻读", "九推", "接收推荐"]),
    ("联培/直博", ["联培", "联合培养", "直博", "博士生招生"]),
    ("实验室招生", ["招收", "独立招生", "研究生说明", "招录", "课题组招生"]),
]

CATEGORY_ORDER = ["冬令营", "夏令营", "暑期学校", "研学营", "预推免", "推免/九推", "联培/直博", "实验室招生", "招生简章", "其他"]


def classify(text: str) -> str:
    """按关键词出现的『最早位置』判定类型：标题里越靠前的词越可能是主题。"""
    head = text[:24]
    for cat, kws in CATEGORY_RULES:
        if any(k in head for k in kws):
            return cat
    for cat, kws in CATEGORY_RULES:
        for k in kws:
            if k in text:
                return cat
    if any(k in text for k in ["招生简章", "招生章程", "招生通知", "招生说明"]):
        return "招生简章"
    return "其他"


# ---------------------------------------------------------------- 日期

_FULL_DATE = re.compile(r"(20\d{2})\s*[年\-/\.]\s*(\d{1,2})\s*[月\-/\.]\s*(\d{1,2})\s*[日号]?")
_PART_DATE = re.compile(r"(?<![\d年月/])(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")
_SLASH_DATE = re.compile(r"(?<![\d/\-.])(\d{1,2})/(\d{1,2})(?![\d/\-])")
_RANGE_CONNECTOR = re.compile(r"^\s*(?:至|到|—|–|~|～|-|—|继|一直至)\s*$")
_RANGE_CONNECTOR_WIDE = re.compile(r"(?:至|到|~|～|—|–)")


def _is_range_gap(gap: str) -> bool:
    """判断两个日期之间是否只是连接词（可容忍『上午8:00起至』这类插入语）。"""
    if _RANGE_CONNECTOR.match(gap):
        return True
    # 宽口径：18 字以内且含连接词，且中间没有句读切断（避免把两个不相干的日期并成一个区间）
    return len(gap) <= 18 and bool(_RANGE_CONNECTOR_WIDE.search(gap)) and not re.search(r"[。；;！!？?\n]", gap)


class DateTok:
    __slots__ = ("start", "end", "y", "m", "d", "explicit_year")

    def __init__(self, start, end, y, m, d, explicit_year):
        self.start, self.end = start, end
        self.y, self.m, self.d = y, m, d
        self.explicit_year = explicit_year

    def resolved(self, base_year: int, base_month: int) -> _dt.date | None:
        year = self.y
        if year is None:
            year = base_year
            if self.m < base_month - 6:
                year += 1
        try:
            return _dt.date(year, self.m, self.d)
        except ValueError:
            return None

    def __repr__(self):
        return f"<{self.y or '?'}-{self.m}-{self.d}>"


def date_tokens(text: str) -> list[DateTok]:
    toks: list[DateTok] = []
    for m in _FULL_DATE.finditer(text):
        try:
            toks.append(DateTok(m.start(), m.end(), int(m.group(1)), int(m.group(2)), int(m.group(3)), True))
        except ValueError:
            continue
    covered = [(t.start, t.end) for t in toks]

    def _inside(a, b):
        return any(a >= s and b <= e for s, e in covered)

    for m in _PART_DATE.finditer(text):
        if _inside(m.start(), m.end()):
            continue
        try:
            toks.append(DateTok(m.start(), m.end(), None, int(m.group(1)), int(m.group(2)), False))
        except ValueError:
            continue
    toks.sort(key=lambda t: t.start)
    return toks


def date_groups(text: str, base_year: int, base_month: int) -> list[dict]:
    """把相邻日期合并成区间：『2026年6月20日至6月30日』→ start/deadline。"""
    toks = date_tokens(text)
    groups: list[dict] = []
    i = 0
    while i < len(toks):
        cur = [toks[i]]
        j = i
        while j + 1 < len(toks):
            gap = text[toks[j].end : toks[j + 1].start]
            if _is_range_gap(gap):
                cur.append(toks[j + 1])
                j += 1
                continue
            break
        dates = [t.resolved(base_year, base_month) for t in cur]
        dates = [d for d in dates if d]
        if dates:
            groups.append(
                {
                    "start": min(dates),
                    "end": max(dates),
                    "raw": text[cur[0].start : cur[-1].end].strip(),
                    "single": len(dates) == 1,
                }
            )
        i = j + 1
    return groups


def guess_published(text: str, fallback: _dt.date | None = None) -> tuple[_dt.date | None, str]:
    for m in re.finditer(r"(?:发布时间|发布日期|更新时间|发布人|时间|日期)\s*[:：]?\s*(20\d{2}[-/年]\d{1,2}[-/月]\d{1,2})", text[:3000]):
        toks = date_tokens(m.group(1))
        if toks:
            d = toks[0].resolved(int(m.group(1)[:4]), 1)
            if d:
                return d, m.group(0)[:40]
    head = text[:1000]
    for tok in date_tokens(head):
        # 排除『截止时间：X』这类日期——那是截稿日，不是发布日
        if re.search(r"(截止|截至)\s*[:：]?\s*$", head[max(0, tok.start - 6) : tok.start]):
            continue
        d = tok.resolved(_dt.date.today().year, _dt.date.today().month)
        if d:
            return d, tok.__repr__()
    return fallback, ""


REG_CUES = [
    "报名截止", "申请截止", "截止日期", "截止时间", "报名时间", "申请时间", "报名日期",
    "网上报名", "提交材料", "材料提交", "申请截止日期", "报名截止日期", "受理时间", "截止",
]
START_HINTS = ("开始", "起", "自", "从", "开放")
EVENT_CUES = ["开营", "入营", "举办时间", "活动时间", "夏令营时间", "报到", "举办日期", "活动日期", "研修时间"]


def _search_window(text: str, cue: str, span: int = 100) -> list[tuple[int, int]]:
    windows = []
    for m in re.finditer(re.escape(cue), text):
        a = max(0, m.start() - 12)
        b = min(len(text), m.end() + span)
        windows.append((a, b))
    return windows


def _looks_like_start(text: str, date_start: int, date_end: int) -> bool:
    """『6月1日开始报名』类表述：起点提示词必须紧贴日期，否则不判为开始日。

    紧贴是关键——正文里『自…起』『从…开放』离日期太远时多为无关描述。
    """
    before = text[max(0, date_start - 4) : date_start]
    after = text[date_end : date_end + 3]
    return bool(re.search(r"(开始|起|自|从|开放)", before + after))


SENTENCE_BREAK = re.compile(r"[。；;！!？?\n]")


def _first_clause(text: str, cue_end: int, max_len: int = 120) -> str:
    """截取提示词之后的第一个分句。

    截止日就在提示词同一分句内；跨句的日期属于另一件事（如『截止时间：7月31日；面谈通知时间：8月5日』），
    取整段窗口的最后一个日期会把面谈/活动日误当截止日。
    """
    tail = text[cue_end : cue_end + max_len]
    m = SENTENCE_BREAK.search(tail)
    return tail[: m.start() + 1] if m else tail


def find_registration(text: str, published: _dt.date | None) -> dict:
    """返回 {'start','deadline','raw'}；找不到则为空值。

    合理性判据：已知发布时间时以其为锚点（截稿日不会早于发布日太久，也不会晚于一年半）；
    发布时间未知时以今天为锚点、放宽下界——历史通知同样要收录进库供参考。
    """
    base = published or _dt.date.today()
    today = _dt.date.today()
    lo = base - _dt.timedelta(days=30) if published else today - _dt.timedelta(days=730)
    hi = base + _dt.timedelta(days=430) if published else today + _dt.timedelta(days=430)
    best: dict = {"start": None, "deadline": None, "raw": ""}

    for cue in REG_CUES:
        for m in re.finditer(re.escape(cue), text):
            window = _first_clause(text, m.end())
            groups = date_groups(window, base.year, base.month)
            if not groups:
                continue
            g = groups[0]
            cand_start, cand_end = g["start"], g["end"]
            if cand_end < lo or cand_end > hi:
                continue
            if g["single"] and _looks_like_start(window, *(0, len(window))) and not re.search(
                r"(截止|截至|前|之前|以内)", text[max(0, m.start() - 4) : m.end() + 4]
            ):
                if best["start"] is None and best["deadline"] is None:
                    best = {"start": cand_end, "deadline": None, "raw": g["raw"]}
                continue
            return {"start": cand_start, "deadline": cand_end, "raw": g["raw"]}
    return best


def find_event(text: str, published: _dt.date | None) -> dict:
    base = published or _dt.date.today()
    for cue in EVENT_CUES:
        for a, b in _search_window(text, cue, span=80):
            groups = date_groups(text[a:b], base.year, base.month)
            if groups:
                g = groups[-1]
                return {"start": g["start"], "end": g["end"], "raw": g["raw"]}
    return {"start": None, "end": None, "raw": ""}


def guess_cycle(text: str, published: _dt.date | None) -> str:
    """推断招生年份（如 2027 = 2027 年入学/2027 届推免）。"""
    head = text[:60]
    for pat in (r"(20\d{2})\s*年.{0,6}?(?:夏令营|冬令营|预推免|推免|推荐免试|招生|开放日)",
                r"(20\d{2})\s*届", r"(20\d{2})\s*级"):
        m = re.search(pat, head)
        if m:
            return m.group(1)
    m = re.search(r"(20\d{2})\s*年", text)
    if m:
        return m.group(1)
    if published:
        return str(published.year + (1 if published.month >= 6 else 0))
    return ""


# ---------------------------------------------------------------- 导航发现

LIST_HINT = re.compile(
    r"(通知公告|通知与公告|招生信息|研究生招生|硕士招生|招生工作|招生|新闻动态|新闻中心|动态|公告|announcements?|news|notice)",
    re.I,
)
LISTISH_PATH = re.compile(r"(list|tzgg|notice|news|gggs|index|announce|zsgz|zsxx|article)", re.I)


def discover_list_pages(html: str, base_url: str, limit: int = 4) -> list[str]:
    """从入口页（通常是首页或某通知页）发现『通知公告/招生』列表页，应对逐年改路径。"""
    host = urllib.parse.urlsplit(base_url).netloc
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for link in extract_links(html, base_url):
        url = link["url"]
        if urllib.parse.urlsplit(url).netloc != host:
            continue
        text = link["text"]
        if len(text) > 20 or not text:
            continue
        score = 0
        if re.search(r"(通知公告|通知与公告|招生信息|研究生招生|硕士招生|招生工作|公告)", text, re.I):
            score += 3
        elif LIST_HINT.search(text):
            score += 1
        if LISTISH_PATH.search(url):
            score += 1
        if url.rstrip("/") == base_url.rstrip("/"):
            score -= 5
        if score >= 3 and url not in seen:
            seen.add(url)
            scored.append((score, url))
    scored.sort(key=lambda x: -x[0])
    return [u for _, u in scored[:limit]]

def html_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.S | re.I)
    if not m:
        return ""
    return _clean_text(re.sub(r"<[^>]+>", " ", m.group(1)))


def page_heading(html: str) -> str:
    """页面主标题：优先 h1，其次 h2，再次首个较长文本行。"""
    for tag in ("h1", "h2"):
        m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", html or "", re.S | re.I)
        if m:
            text = _clean_text(re.sub(r"<[^>]+>", " ", m.group(1)))
            if 6 <= len(text) <= 80:
                return text
    return ""


def best_self_title(html: str, text: str) -> str:
    """判断『页面即通知』时用哪个标题最合适。"""
    cands = [page_heading(html), html_title(html)]
    for line in text.splitlines()[:12]:
        line = line.strip()
        if 8 <= len(line) <= 70 and not re.match(r"^(首页|通知公告|研究生招生|招生信息|EN|中文)", line):
            cands.append(line)
            break
    cands = [c for c in cands if c]
    if not cands:
        return ""
    # 标题里带关键词的最合适，其次取最长的
    for c in cands:
        if classify(c) != "其他":
            return c
    return max(cands, key=len)


NOTICE_TITLE_HINT = re.compile(r"(通知|说明|简章|公告|办法|方案|招收|招生|报名|申请|选拔|接收)")
SITE_NAME_HINT = re.compile(r"(招生网|研招网|研究生招生信息网|信息\s*[-—|]|首页|官网|研究生院\s*$)")


def is_self_notice(text: str, title: str, article_links: int = 0) -> bool:
    """页面正文自身是否就是一条招生通知（而非列表页或首页）。

    四道闸门缺一不可，否则首页/栏目页都会被误判成通知：
      1) 标题长度够且不像站名（"夏令营信息-清华研究生招生网"要挡住）；
      2) 标题像通知，或本身能判出类型；
      3) 页面上没有一批同类通知链接（有 → 这是列表页，交给正常链路）；
      4) 正文里有报名/申请类提示词，且提示词附近真有日期。
    """
    if len(title) < 10:
        return False
    if SITE_NAME_HINT.search(title):
        return False
    if article_links >= 3:
        return False
    if classify(title) == "其他" and not NOTICE_TITLE_HINT.search(title):
        return False
    body = text[:6000]
    if not re.search(r"(报名|申请|提交|截止|寄送|投递)", body):
        return False
    return bool(date_tokens(body))


# 序号分支要求标点后不是数字，否则 "05.22/2024" 会被误当作 "05." 序号切掉
_LEAD_INDEX = re.compile(r"^\s*(?:\d{1,3}\s*[、,，.．:：)）](?!\d)|\d{1,3}\s+(?=\S{4,}))\s*")
_LEAD_INDEX_BEFORE_YEAR = re.compile(r"^\s*\d{1,3}(?=\s*20\d{2}\s*[.\-/年])")
_LEAD_INDEX_CNMONTH = re.compile(r"^\s*\d{1,3}\s*[一二三四五六七八九十]{1,3}\s*月\s*")
_LEAD_MDY = re.compile(r"^\s*\d{1,2}\s*[.\-/]\s*\d{1,2}\s*[.\-/]\s*20\d{2}\s*")
_LEAD_DATE = re.compile(r"^\s*20\d{2}\s*[.\-/年]\s*\d{1,2}\s*[.\-/月]\s*\d{1,2}\s*日?\s*")
_LEAD_YM = re.compile(r"^\s*20\d{2}\s*[.\-/年]\s*\d{1,2}\s*月?\s+")
_LEAD_YEAR = re.compile(r"^\s*20\d{2}\s+(?=[^\d\s年届级])")
_LEAD_SHORTDATE = re.compile(r"^\s*\d{1,2}\s*[.\-/]\s*\d{1,2}\s+")
_TRAIL_DATE = re.compile(r"\s*20\d{2}\s*[.\-/年]\s*\d{1,2}\s*[.\-/月]\s*\d{1,2}\s*日?\s*$")
_TRAIL_YM = re.compile(r"\s*20\d{2}\s*[.\-/年]\s*\d{1,2}\s*月?\s*$")
_TRAIL_MORE = re.compile(
    r"\s*(?:详情|更多|查看|点击进入|如果您无法在线浏览|请点击|…+|\.{3,})\s*$"
)
_LEAD_DECOR = re.compile(r"^\s*(?:欢迎报考|欢迎报名|火热报名中|重磅|最新|关注|转发|点击)\s*[|｜丨]\s*")
# 列表页的翻页链接（「上一条：xxx」「下一篇」）会被当成标题，需要剥掉
_LEAD_NAV = re.compile(r"^\s*(?:上一条|下一条|上一篇|下一篇|返回列表|返回首页)\s*[:：]?\s*")
_TITLE_END_WORDS = r"(?:通知|公告|章程|简章|说明|办法|方案|须知|要求|安排|细则|信息|意见|规定|工作)"
_TITLE_END = re.compile(_TITLE_END_WORDS)
_SENT_START = re.compile(
    r"\s+(?=(?:一|二|三|四|五|六|七|八|九|十)[、.]|为(?:了|全面|深入|进一步)|根据|现将|欢迎)"
)
# 标题结尾词之后又跟了一小段文本 → 那多半是正文开头（"…报名通知 哈"）
_TAIL_AFTER_END = re.compile(rf"^(.*{_TITLE_END_WORDS})[\s|｜丨]+\S{{1,24}}$")


def trim_title(text: str) -> str:
    """列表页里 <a> 常把标题和正文摘要整块包住，需要把正文尾巴切掉。

    三级判断，逐级放宽：分句起点（"…录取办法 一、资格审查"）→
    结尾词后的小尾巴（"…报名通知 哈"）→ 超长时按最后一个结尾词截断。
    """
    t = _clean_text(text)
    t = _SENT_START.split(t)[0].strip()
    m = _TAIL_AFTER_END.match(t)
    if m:
        t = m.group(1).strip()
    if len(t) > 52:
        ends = [m.end() for m in _TITLE_END.finditer(t[:70])]
        if ends:
            cand = t[: max(ends)].strip()
            if len(cand) >= 12:
                return cand
        m2 = re.match(r"^(.{16,58}?)[，,。；;、\s]", t)
        return m2.group(1) if m2 else t[:52]
    return t


def _balance_parens(t: str) -> str:
    """去掉因站方截断而悬空的左括号，如 "…宣传手册（2025"。"""
    if t.count("（") > t.count("）"):
        pos = t.rfind("（")
        if pos > len(t) * 0.4:
            t = t[:pos].strip()
    if t.count("(") > t.count(")"):
        pos = t.rfind("(")
        if pos > len(t) * 0.4:
            t = t[:pos].strip()
    return t


def clean_title(title: str) -> str:
    """清掉列表页常见的前后缀：序号（"12、"）、日期（"2026.09.04"）、装饰语、"详情"尾缀。

    每轮都先剥前缀再截正文尾巴——前缀（如 "08-25 2026 "）会撑长字符串，
    若先截断就会因为「看起来不够长」而漏切。
    """
    t = (title or "").strip()
    for _ in range(4):
        before = t
        t = _LEAD_NAV.sub("", t)
        t = _LEAD_DECOR.sub("", t)
        t = _LEAD_MDY.sub("", t)
        t = _LEAD_INDEX.sub("", t)
        t = _LEAD_INDEX_BEFORE_YEAR.sub("", t)
        t = _LEAD_INDEX_CNMONTH.sub("", t)
        t = _LEAD_DATE.sub("", t)
        t = _LEAD_YM.sub("", t)
        t = _LEAD_YEAR.sub("", t)
        t = _LEAD_SHORTDATE.sub("", t)
        t = _TRAIL_DATE.sub("", t)
        t = _TRAIL_YM.sub("", t)
        t = _TRAIL_MORE.sub("", t)
        t = trim_title(t)
        t = _balance_parens(t)
        t = t.strip()
        if t == before:
            break
    return t or (title or "").strip()
