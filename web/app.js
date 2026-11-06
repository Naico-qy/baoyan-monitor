/* 保研通知雷达 —— 前端逻辑（原生 JS，无外部依赖） */
"use strict";

const STATUS_TEXT = {
  urgent: "即将截止",
  open: "进行中",
  upcoming: "未开始",
  closed: "已截止",
  unknown: "待定",
  unknown_new: "新发布",
};
const CATEGORIES = ["夏令营", "冬令营", "预推免", "推免/九推", "联培/直博", "暑期学校", "实验室招生", "招生简章", "其他"];
const MONTH_LABEL = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二"];

const state = {
  data: null,
  cat: "",
  tier: "",
  level: "",
  status: "live",
  sort: "deadline",
  q: "",
  starOnly: false,
  unknownExpanded: false,
  marks: loadMarks(), // {url: 'star'|'applied'|'ignore'}
};

/* ------------------------------------------------------------------ 本地标记 */
function loadMarks() {
  try { return JSON.parse(localStorage.getItem("baoyan.marks") || "{}"); } catch (e) { return {}; }
}
function saveMarks() {
  localStorage.setItem("baoyan.marks", JSON.stringify(state.marks));
}
function markOf(url) { return state.marks[url] || ""; }
function setMark(url, kind) {
  if (state.marks[url] === kind) delete state.marks[url];
  else state.marks[url] = kind;
  saveMarks();
  render();
}

/* ------------------------------------------------------------------ 工具 */
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtDate = (s) => (s ? s.slice(0, 10) : "—");
const fmtTime = (s) => (s ? s.replace("T", " ").slice(0, 16) : "—");

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), 1900);
}

function daysLabel(n) {
  if (n === null || n === undefined) return "待定";
  if (n < 0) return "已截止";
  if (n === 0) return "今天截止";
  return n + " 天";
}

/* ------------------------------------------------------------------ 过滤与排序 */
function visibleNotices() {
  const q = state.q.trim().toLowerCase();
  let list = state.data.notices.filter((n) => {
    if (markOf(n.url) === "ignore") return false;
    if (state.starOnly && !["star", "applied"].includes(markOf(n.url))) return false;
    if (state.cat && n.category !== state.cat) return false;
    if (state.tier && n.tier !== state.tier) return false;
    if (state.level && n.level !== state.level) return false;
    if (state.status === "live" && !["urgent", "open", "upcoming", "unknown", "unknown_new"].includes(n.status)) return false;
    if (state.status && state.status !== "live" && n.status !== state.status) return false;
    if (q) {
      const hay = `${n.org} ${n.unit || ""} ${n.title} ${n.category} ${n.cycle || ""}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  const cmpHier = { T1: 0, T2: 1, T3: 2, T4: 3 };
  list.sort((a, b) => {
    if (state.sort === "published") return (b.published_at || "").localeCompare(a.published_at || "");
    if (state.sort === "first_seen") return (b.first_seen || "").localeCompare(a.first_seen || "");
    if (state.sort === "tier") {
      const d = (cmpHier[a.tier] ?? 9) - (cmpHier[b.tier] ?? 9);
      if (d) return d;
      return (a.deadline || "9999").localeCompare(b.deadline || "9999");
    }
    const an = a.deadline === null, bn = b.deadline === null;
    if (an !== bn) return an ? 1 : -1;
    const d = (a.deadline || "").localeCompare(b.deadline || "");
    if (d) return d;
    return (b.published_at || "").localeCompare(a.published_at || "");
  });
  return list;
}

/* ------------------------------------------------------------------ 渲染：统计 */
function renderStaleBanner() {
  const el = $("#stale-banner");
  if (!el) return;
  const gen = state.data.generated_at;
  if (!gen) { el.innerHTML = ""; return; }
  const days = Math.floor((Date.now() - new Date(gen).getTime()) / 86400000);
  if (days < 3) { el.innerHTML = ""; return; }
  el.innerHTML = `
    <div class="stale">
      ⚠ 数据已 <b>${days} 天</b> 未更新（最后生成于 ${esc(fmtTime(gen))}）。
      如果你用的是 GitHub Pages，可能是定时抓取失败——请到仓库的 Actions 页面查看运行记录。
    </div>`;
}

function renderStats() {
  const s = state.data.stats;
  const cards = [
    { n: s.open, k: "未截止通知", cls: "blue" },
    { n: s.urgent, k: "7 天内截止", cls: s.urgent ? "warn" : "" },
    { n: s.upcoming, k: "尚未开始报名", cls: "" },
    { n: s.unknown, k: "待定 / 无明确截止", cls: "" },
    { n: s.notices, k: "库内通知总数", cls: "" },
    { n: `${s.sources_ok}/${s.sources_total}`, k: "渠道抓取正常", cls: s.sources_failed ? "warn" : "ok" },
  ];
  $("#stats").innerHTML = cards
    .map((c) => `<div class="stat ${c.cls}"><div class="n">${esc(c.n)}</div><div class="k">${esc(c.k)}</div></div>`)
    .join("");

  const lr = s.last_run || {};
  $("#stamp").innerHTML = `数据生成于 <b>${esc(fmtTime(state.data.generated_at))}</b>`;
  $("#tab-count-ddl").textContent = s.notices;
  $("#tab-count-health").textContent = s.sources_total;
  $("#tab-count-log").textContent = (state.data.events || []).length;

  const icsBtn = document.querySelector('a[href="baoyan.ics"]');
  if (icsBtn) {
    const noneOpen = s.open === 0;
    icsBtn.textContent = noneOpen ? "📅 导出日历（当前无未截止项）" : "📅 导出日历 (.ics)";
    icsBtn.title = noneOpen
      ? "当前库内没有未截止的报名截止日，导出的 .ics 为空；等新一轮夏令营/预推免通知发布后即可用。"
      : "导出未截止通知的截止日，可导入手机日历（截止前 3 天提醒）";
  }
}

/* ------------------------------------------------------------------ 渲染：类型筛选 */
function renderChips() {
  const counts = {};
  state.data.notices.forEach((n) => { counts[n.category] = (counts[n.category] || 0) + 1; });
  const used = CATEGORIES.filter((c) => counts[c]);
  const html = [`<button class="chip ${state.cat === "" ? "on" : ""}" data-cat="">全部<span class="c">${state.data.notices.length}</span></button>`]
    .concat(used.map((c) => `<button class="chip ${state.cat === c ? "on" : ""}" data-cat="${esc(c)}">${esc(c)}<span class="c">${counts[c]}</span></button>`));
  $("#cat-chips").innerHTML = html.join("");
  $("#cat-chips").querySelectorAll(".chip").forEach((el) => {
    el.onclick = () => { state.cat = el.dataset.cat; render(); };
  });
}

/* ------------------------------------------------------------------ 渲染：DDL 列表 */
function renderDDL() {
  const list = visibleNotices();
  const box = $("#ddl-list");

  const marked = Object.values(state.marks).filter((v) => v === "applied").length;
  const starred = Object.values(state.marks).filter((v) => v === "star").length;
  $("#hint").innerHTML =
    `当前显示 <b>${list.length}</b> 条 ｜ 收藏 ${starred} · 已投递 ${marked} ｜ ` +
    `<a href="#" id="dump-marks">导出我的标记</a> · <a href="#" id="load-marks">导入</a>` +
    ` ｜ 颜色：<span style="color:#d92d20">≤7天</span> / <span style="color:#14804a">进行中</span> / <span style="color:#1f5fd8">未开始</span>`;

  if (!list.length) {
    box.innerHTML = `<div class="empty">没有符合条件的通知。<br>试试把上方「状态」切到「全部」，或运行 <code>python -m baoyan run</code> 抓取最新数据。</div>`;
    return;
  }

  // 按截止月份分组（无截止日期的归到「待定」）
  const groups = new Map();
  list.forEach((n) => {
    const key = n.deadline ? n.deadline.slice(0, 7) : "unknown";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(n);
  });

  let html = "";
  for (const [key, items] of groups) {
    const isUnknown = key === "unknown";
    // 「待定」组通常很长（多为无明确截止日的流程类通知），默认只展开最新的一批
    const shown = isUnknown && !state.unknownExpanded ? items.slice(0, 40) : items;
    const urgent = items.filter((n) => n.status === "urgent").length;
    const label = isUnknown ? "截止日期待定 / 以官网为准"
      : `${key.slice(0, 4)} 年 ${MONTH_LABEL[parseInt(key.slice(5, 7), 10) - 1]} 月 截止`;
    const note = isUnknown
      ? `${shown.length} / ${items.length} 条`
      : urgent ? `其中 ${urgent} 条 7 天内截止` : `${items.length} 条`;
    html += `<div class="month-head"><span>${label}</span><span class="m-note">${note}</span></div>`;
    html += shown.map(cardHTML).join("");
    if (isUnknown && items.length > shown.length) {
      html += `<button class="btn more" id="more-unknown">展开剩余 ${items.length - shown.length} 条待定通知</button>`;
    } else if (isUnknown && state.unknownExpanded && items.length > 40) {
      html += `<button class="btn more" id="less-unknown">收起待定通知</button>`;
    }
  }
  box.innerHTML = html;

  box.querySelectorAll("[data-mark]").forEach((btn) => {
    btn.onclick = () => setMark(btn.dataset.url, btn.dataset.mark);
  });
  const moreBtn = $("#more-unknown");
  if (moreBtn) moreBtn.onclick = () => { state.unknownExpanded = true; renderDDL(); };
  const lessBtn = $("#less-unknown");
  if (lessBtn) lessBtn.onclick = () => { state.unknownExpanded = false; renderDDL(); };
  const dm = $("#dump-marks"); if (dm) dm.onclick = dumpMarks;
  const lm = $("#load-marks"); if (lm) lm.onclick = importMarks;
}

function cardHTML(n) {
  const m = markOf(n.url);
  const cls = `s-${n.status}`;
  const tags = [
    `<span class="tag cat">${esc(n.category)}</span>`,
    n.tier ? `<span class="tag ${n.tier.toLowerCase()}">${esc(n.tier)}</span>` : "",
    n.level ? `<span class="tag">${esc(n.level)}</span>` : "",
    n.cycle ? `<span class="tag">${esc(n.cycle)} 级</span>` : "",
    n.is_new ? `<span class="tag new">新发现</span>` : "",
    n.manual ? `<span class="tag">人工录入</span>` : "",
    m === "star" ? `<span class="tag star">已收藏</span>` : "",
    m === "applied" ? `<span class="tag applied">已投递</span>` : "",
  ].filter(Boolean).join("");

  const meta = [
    `<span>${esc(n.org)}${n.unit && n.unit !== n.org ? " · " + esc(n.unit) : ""}</span>`,
    n.published_at ? `<span>发布 ${esc(fmtDate(n.published_at))}</span>` : "",
    n.reg_start ? `<span>报名起 ${esc(fmtDate(n.reg_start))}</span>` : "",
    n.event_start ? `<span>活动 ${esc(fmtDate(n.event_start))}${n.event_end && n.event_end !== n.event_start ? " ~ " + esc(fmtDate(n.event_end)) : ""}</span>` : "",
    n.confidence ? `<span class="muted">可信度 ${{ high: "高", medium: "中", low: "低", manual: "人工" }[n.confidence] || "低"}</span>` : "",
  ].filter(Boolean).join("");

  const rawBits = [];
  if (n.deadline_raw) rawBits.push(`<b>截止原文：</b>${esc(n.deadline_raw)}`);
  if (n.event_raw) rawBits.push(`<b>活动原文：</b>${esc(n.event_raw)}`);
  if (n.note) rawBits.push(`<b>备注：</b>${esc(n.note)}`);
  if (n.alt_urls) {
    const alts = String(n.alt_urls).split("").filter(Boolean);
    if (alts.length) {
      rawBits.push(`<b>同源链接：</b>` + alts.map((u, i) =>
        `<a href="${esc(u)}" target="_blank" rel="noopener">备用${i + 1}</a>`).join(" · "));
    }
  }

  return `
  <article class="card ${cls}">
    <div class="dd">
      <div class="days">${esc(daysLabel(n.days_left))}</div>
      <div class="date">${esc(fmtDate(n.deadline))}</div>
    </div>
    <div class="body">
      <div class="title"><a href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.title)}</a></div>
      <div class="tags">${tags}</div>
      <div class="meta">${meta}</div>
      ${rawBits.length ? `<div class="raw">${rawBits.join(" ｜ ")}</div>` : ""}
    </div>
    <div class="acts">
      <button data-mark="star" data-url="${esc(n.url)}" class="${m === "star" ? "on-star" : ""}">${m === "star" ? "★ 已收藏" : "☆ 收藏"}</button>
      <button data-mark="applied" data-url="${esc(n.url)}" class="${m === "applied" ? "on-applied" : ""}">${m === "applied" ? "✓ 已投递" : "标记已投递"}</button>
      <button data-mark="ignore" data-url="${esc(n.url)}" class="${m === "ignore" ? "on-ignore" : ""}">忽略</button>
    </div>
  </article>`;
}

/* ------------------------------------------------------------------ 渲染：预期节奏 */
function renderExpect() {
  const curMonth = parseInt(state.data.today.slice(5, 7), 10);
  const byMonth = new Map();
  state.data.sources.filter((s) => !s.is_system).forEach((s) => {
    let months = [];
    try { months = JSON.parse(s.expected || "[]"); } catch (e) { months = []; }
    months.forEach((m) => {
      if (!byMonth.has(m)) byMonth.set(m, []);
      byMonth.get(m).push(s);
    });
  });
  const order = [...byMonth.keys()].sort((a, b) => ((a - curMonth + 12) % 12) - ((b - curMonth + 12) % 12));
  const html = order.map((m) => {
    const items = byMonth.get(m);
    const rel = (m - curMonth + 12) % 12;
    const badge = rel === 0 ? "本月" : rel === 1 ? "下月" : rel === 2 ? "两个月后" : "";
    return `<div class="mcard ${rel === 0 ? "now" : ""}">
      <h3><span>${MONTH_LABEL[m - 1]} 月</span>${badge ? `<span class="badge">${badge}</span>` : ""}</h3>
      <ul>${items.map((s) => `<li>
        <div>${esc(s.org)} ${esc(s.unit)}</div>
        <span class="u">${esc(s.history || "")}</span>
        ${s.entry ? `<a href="${esc(s.entry)}" target="_blank" rel="noopener">打开渠道 →</a>` : ""}
      </li>`).join("")}</ul>
    </div>`;
  }).join("");
  $("#expect").innerHTML = html || `<div class="empty">暂无预期月份数据</div>`;
}

/* ------------------------------------------------------------------ 渲染：渠道健康 */
function renderHealth() {
  const rows = state.data.sources.map((s) => {
    const dot = s.is_system ? "sys" : s.ok === 1 ? "" : "bad";
    const st = s.is_system ? "报名系统（仅入口）" : s.ok === 1 ? "正常" : s.ok === 0 ? `失败：${esc(s.error || "")}` : "未检查";
    let months = [];
    try { months = JSON.parse(s.expected || "[]"); } catch (e) { months = []; }
    return `<tr>
      <td><span class="dot ${dot}"></span>${esc(s.org)}<br><span class="muted">${esc(s.unit)}</span></td>
      <td class="nowrap">${esc(s.tier || "")} · ${esc(s.level || "")}</td>
      <td class="nowrap">${esc(st)}</td>
      <td class="nowrap mono">${esc(fmtDate(s.last_checked))}</td>
      <td class="nowrap mono">${s.notice_count || 0}</td>
      <td class="nowrap">${months.map((m) => MONTH_LABEL[m - 1] + "月").join(" ") || "—"}</td>
      <td class="muted">${esc(s.history || "")}</td>
      <td>${s.entry ? `<a href="${esc(s.entry)}" target="_blank" rel="noopener">入口</a>` : ""}</td>
    </tr>`;
  }).join("");
  $("#health").innerHTML = `<table>
    <thead><tr><th>渠道</th><th>层次</th><th>抓取状态</th><th>最近检查</th><th>通知数</th><th>往年发布</th><th>往年时点</th><th>链接</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}

/* ------------------------------------------------------------------ 渲染：日志 */
function renderLog() {
  const kinds = { new_notice: "新通知", updated_notice: "截止日变更", source_error: "抓取异常", source_recovered: "恢复正常" };
  const evs = state.data.events || [];
  $("#log").innerHTML = evs.length ? evs.map((e) => {
    let detail = "";
    if (e.type === "updated_notice") {
      try {
        const ch = JSON.parse(e.detail || "{}");
        detail = Object.entries(ch).map(([k, v]) => `${k}: ${v.from || "空"} → ${v.to}`).join("；");
      } catch (err) { detail = e.detail || ""; }
    } else if (e.detail) {
      detail = e.detail;
    }
    return `<div class="ev">
      <span class="kind k-${esc(e.type)}">${esc(kinds[e.type] || e.type)}</span>
      <span class="t">${esc(fmtTime(e.ts))}</span>
      <span>${esc(e.title || "")}${e.source_id ? ` <span class="muted">[${esc(e.source_id)}]</span>` : ""}${detail ? `<br><span class="muted">${esc(detail)}</span>` : ""}</span>
    </div>`;
  }).join("") : `<div class="empty">暂无变更记录</div>`;
}

/* ------------------------------------------------------------------ 标记导入导出 */
function dumpMarks() {
  const blob = new Blob([JSON.stringify(state.marks, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "baoyan-marks.json";
  a.click();
  toast("已导出收藏与投递标记");
}
function importMarks() {
  const inp = document.createElement("input");
  inp.type = "file";
  inp.accept = ".json";
  inp.onchange = () => {
    const f = inp.files[0];
    if (!f) return;
    const fr = new FileReader();
    fr.onload = () => {
      try {
        state.marks = Object.assign({}, state.marks, JSON.parse(fr.result));
        saveMarks(); render(); toast("标记已导入");
      } catch (e) { toast("导入失败：不是合法的 JSON"); }
    };
    fr.readAsText(f);
  };
  inp.click();
}

/* ------------------------------------------------------------------ 事件绑定 */
function bind() {
  $("#q").oninput = (e) => { state.q = e.target.value; renderDDL(); };
  $("#f-tier").onchange = (e) => { state.tier = e.target.value; renderDDL(); };
  $("#f-level").onchange = (e) => { state.level = e.target.value; renderDDL(); };
  $("#f-status").onchange = (e) => { state.status = e.target.value; renderDDL(); };
  $("#f-sort").onchange = (e) => { state.sort = e.target.value; renderDDL(); };
  $("#f-star").onchange = (e) => { state.starOnly = e.target.checked; renderDDL(); };
  $("#reset").onclick = () => {
    Object.assign(state, { cat: "", tier: "", level: "", status: "live", sort: "deadline", q: "", starOnly: false });
    $("#q").value = ""; $("#f-tier").value = ""; $("#f-level").value = ""; $("#f-status").value = "live";
    $("#f-sort").value = "deadline"; $("#f-star").checked = false;
    render();
  };
  $("#copy-cmd").onclick = () => {
    const cmd = "python -m baoyan run";
    navigator.clipboard.writeText(cmd).then(() => toast("已复制：" + cmd + "（在项目目录执行即可更新本站数据）"));
  };
  document.querySelectorAll(".tab").forEach((t) => {
    t.onclick = () => {
      document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === t));
      ["ddl", "expect", "health", "log"].forEach((k) => {
        $("#panel-" + k).classList.toggle("hidden", k !== t.dataset.tab);
      });
    };
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && document.activeElement.tagName !== "INPUT") { e.preventDefault(); $("#q").focus(); }
  });
}

function render() {
  renderStaleBanner();
  renderStats();
  renderChips();
  renderDDL();
  renderExpect();
  renderHealth();
  renderLog();
}

async function boot() {
  bind();
  try {
    const res = await fetch("data.json", { cache: "no-store" });
    state.data = await res.json();
  } catch (e) {
    document.querySelector("main").innerHTML =
      `<div class="empty">读取 data.json 失败：${esc(e.message)}<br><br>请先运行 <code>python -m baoyan run</code> 生成数据，并用 <code>python -m baoyan serve</code> 打开本站（直接双击 html 文件时浏览器会拦截本地 fetch）。</div>`;
    return;
  }
  render();
}

boot();