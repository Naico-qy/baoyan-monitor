#!/usr/bin/env bash
# 抓取全部渠道并重建站点。建议配合系统计划任务（cron / 任务计划程序 / GitHub Actions）定时执行。
set -euo pipefail
cd "$(dirname "$0")/.."

echo "[$(date '+%F %T')] 开始抓取…"
python -m baoyan run --max-details "${MAX_DETAILS:-20}"
echo "[$(date '+%F %T')] 完成。用 python -m baoyan serve 预览。"