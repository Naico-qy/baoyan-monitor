# 保研通知雷达 · Baoyan Monitor

盯住清北 / 华五 / 中九 / 中科院院所 / 新型研究机构的**官方**院系网站，自动抓取
**夏令营、冬令营、预推免、九推、联培直博** 等通知，抽取**报名截止日**，生成一个
[ccfddl.com](https://ccfddl.com/) 风格的截止日跟踪站点。

- 站点首页：[`web/index.html`](web/index.html)（打开即用，含 DDL 列表 / 预期节奏 / 渠道健康 / 变更日志）
- 日历订阅：[`web/baoyan.ics`](web/baoyan.ics)（可导入手机日历，截止前 3 天自动提醒）

> 数据全部来自院校官网公开页面；本工具只做**聚合与日期抽取**，投递前请以官方原文为准。
> 抽取结果都附带「原文片段」，方便人工核对。

---

## 快速开始

```bash
cd baoyan-monitor

python -m baoyan check                 # 1. 自检解析器（离线，秒级）
python -m baoyan run                   # 2. 抓取全部渠道并生成站点
python -m baoyan serve                 # 3. 打开 http://127.0.0.1:8000/
```

**无需安装任何第三方依赖**，Python 3.8+ 标准库即可（`urllib` / `sqlite3` / `html.parser`）。
这一点是刻意的：高校内网、实验室服务器上 `pip install` 经常不通，而标准库到处都有。

Windows 用户可直接双击 `scripts\update.bat`（抓取 + 起服务）。

---

## 它做了什么

```
config/sources.json         渠道清单（72 个入口：研招网 / 学院公告 / 实验室招生页 / 报名系统）
                            其中 55 个为可抓取渠道，17 个是报名系统入口（仅展示，不抓取）
        │
        ├─ 列表页发现 ── 从首页自动找「通知公告 / 招生信息」栏目，应对院系逐年改路径
        ├─ 年份探测 ─── 对 LAMDA 这类 recruit-2026 → recruit-2027 的路径，自动试明年/今年/去年
        ├─ 关键词筛 ─── 只留夏令营/冬令营/预推免/推免/开放日/暑期学校/联培直博…，滤掉招标/招聘/入营名单
        ├─ 详情页解析 ─ 抽取发布时间、报名区间、活动时间，并裁掉导航与页脚减少干扰
        │
        ├─ SQLite 归档 ─ 新通知、截止日变更（官方改了日期会单独记一条日志）
        │
        └─ 静态站点 ──── web/data.json（前端数据） + web/baoyan.ics（日历）
```

### 抽取逻辑的几个关键决定

| 场景 | 处理方式 |
|---|---|
| `报名时间：6月1日上午8:00起至6月15日` | 区间合并容忍插入语，取 **6/15** 为截止 |
| `截止时间：7月31日；面谈通知时间：8月5日` | **只取提示词所在分句**内的日期，避免把面谈日当截止日 |
| `…6月20日` 缺年份 | 按发布年推断；无发布日则按今天推断 |
| 页面本身就是通知（LAMDA / 上海AI Lab） | 「页面即通知」通道：直接解析入口页 |
| 可能是首页站名（`北京大学软件与微电子学院`） | 标题长度 + 通知类关键词 + 报名提示词带日期，三道闸门才认 |
| 列表页标题带序号/日期（`15 2026.09 …办法`） | 自动清洗前后缀 |
| 官网改过截止日期 | 写入 `updated_notice` 变更日志，前端可追溯 |
| 同一文章挂两个栏目（复旦 `c24257a792585` / `c24753a792585`） | 按**文章 ID** 去重，只留信息最全的一条，其余记为「同源链接」 |
| 标题≠正文（列表页 `<a>` 包住标题+摘要） | 按结尾词/分句起点截断正文尾巴，剥掉序号与日期前缀 |

每条通知带 `confidence`：`high`（截止日含明确年份）/ `medium`（年份靠推断）/ `low`（未找到截止日）。
**前端会同时展示原文片段**——正则必然有误差，可核对比"看起来准"更重要。

---

## 站点功能

- **截止日历**：按截止月份分组，红/绿/蓝三色倒计时（≤7 天 / 进行中 / 未开始），支持按
  类型、层次（T1–T4）、粒度（校/院/室）、状态、关键词筛选；`/` 键快速搜索。
- **预期节奏**：按各渠道**往年发布月份**排的蹲守月历，当前月份高亮——解决"什么时候该蹲哪个渠道"。
- **渠道健康**：每个渠道的抓取状态、最近检查时间、入库通知数、往年时点、入口直达。
- **变更日志**：新通知、截止日被官方修改、抓取异常都留痕。
- **我的标记**：收藏 / 已投递 / 忽略，存在浏览器 localStorage（重抓数据不丢），可导出 JSON 备份。
- **日历导出**：`web/baoyan.ics`，未截止通知生成为全天事件，含截止前 3 天的提醒。

---

## 定时自动更新

### 方式一：GitHub Actions（推荐，机器不用常开）

仓库里已带 [`.github/workflows/monitor.yml`](.github/workflows/monitor.yml)：
每天北京时间 09:10 与 21:10 自动抓取，把数据提交回仓库，并发布到 GitHub Pages。

1. 把 `baoyan-monitor/` 推到 GitHub 仓库；
2. Settings → Pages → Source 选 **GitHub Actions**；
3. 完成后访问 `https://<用户名>.github.io/<仓库名>/`。

（注意：抓取访问的是国内高校站点，GitHub 海外 runner 多数可达但有少数会失败；
失败渠道会记在「渠道健康」里，不影响其他渠道。）

### 方式二：本地 / 服务器 cron

```bash
# 每天早上 9:10 抓一轮
10 9 * * * cd /path/to/baoyan-monitor && MAX_DETAILS=25 ./scripts/update.sh >> logs/cron.log 2>&1
```

Windows 用「任务计划程序」定时运行 `scripts\update.bat` 即可。

### 方式三：ZCode 会话内定时（已配置）

本项目已在当前工作区注册了一个每天 09:00 的本地定时任务，到点自动执行抓取并更新站点数据；
不需要它时可在自动任务列表里停用或删除。

---

## 常用命令

```bash
python -m baoyan run --only pku-sai nju-lamda   # 只抓指定渠道（id 或院校名）
python -m baoyan run --max-details 5            # 快速巡检：每源最多抓 5 篇详情
python -m baoyan run --refresh-details          # 强制重抓所有详情（慢，但能发现官方改期）
python -m baoyan export                         # 用现有数据库重建站点（不再联网）
python -m baoyan prune --apply                  # 去重 + 清理误入库条目（建议每轮抓取后跑）
python -m baoyan list --open                    # 终端查看未截止通知
python -m baoyan check                          # 解析器自检（含标题清洗与日期抽取的回归用例）
python -m baoyan check --net                    # 附带检查所有渠道连通性
```

首次抓取建议跑两遍：第一轮受到「每源详情上限」限制，第二轮会把剩下没解析过的通知补全
（已解析过的不会重复抓，省时间也不给院系站点添压力）。

改动过解析逻辑（`extract.py`）后，用 `--refresh-details` 重跑一遍，才会用新逻辑重新解析
已入库的旧记录。本项目当前状态：**55/55 渠道抓取正常，入库 181 条通知**，其中 44 条带明确截止日。

---

## 维护渠道清单

编辑 `config/sources.json`：

```jsonc
{
  "id": "pku-sai",              // 唯一标识
  "org": "北京大学", "unit": "智能学院",
  "tier": "T1", "level": "院",  // T1/T2/T3/T4；校/院/室
  "entry": "https://www.sai.pku.edu.cn/rcpy/yjszs.htm",
  "discover": true,             // 从入口页自动发现「通知公告/招生」列表页
  "year_url": "https://x/{year}/recruit.html",   // 可选：路径含年份的源，自动探测明年/今年/去年
  "system": false,              // true = 只是报名系统入口，仅展示不抓取
  "expected": [5, 6],           // 往年发布月份，用于「预期节奏」
  "history": "2026通知5/28发布，7/2-4开营"        // 往年时点备注
}
```

抓不到但很重要的通知（只在公众号/系统内发布），写进 `config/manual_notices.json`，
会以「人工录入」标签合并进站点。

### 新增渠道的排错顺序

1. `python -m baoyan check --net` 看入口是否可达（SSL 证书链不全的站点会自动降级重试）；
2. `python -m baoyan run --only <id> --max-details 3` 看候选与详情解析；
3. 若无候选：该页可能是首页或纯导航页 → 打开 `discover: true`，或把入口直接换成公告列表页 URL。

---

## 目录结构

```
baoyan-monitor/
├── baoyan/            抓取器
│   ├── http.py        HTTP 层：编码识别(UTF-8/GBK)、gzip、重试、同域限速、证书降级
│   ├── extract.py     解析层：链接/正文抽取、日期区间、截止日、类型判定、导航发现
│   ├── crawl.py       编排层：入口解析、页面发现、候选筛选、详情解析
│   ├── db.py          SQLite：源状态 / 通知 / 变更日志 / 运行记录
│   ├── export.py      导出 data.json + baoyan.ics
│   └── cli.py         命令行
├── config/
│   ├── sources.json           渠道清单（72 个入口）
│   └── manual_notices.json    人工补充通知
├── data/baoyan.db     归档数据库（累积，越跑越全）
├── web/               站点（index.html / style.css / app.js / data.json / baoyan.ics）
└── scripts/           update.sh · update.bat
```

## 已知边界

- **只覆盖有网页公告的渠道**：纯公众号/微信群发布的信息抓不到，需人工录入。
- **正文里的日期是正则抽的**：会用 `confidence` 与原文片段标注，请以原文为准。
- **少数站点有反爬或需登录**：会显示在「渠道健康」的失败项里，可手动换入口 URL。
- **首轮入库以"当年 + 近一年"为主**：历史通知由 `data/baoyan.db` 逐轮累积，
  建议在 4-6 月（夏令营）与 8-9 月（预推免）保持每日运行。