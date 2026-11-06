@echo off
REM 抓取全部渠道并重建站点（Windows 双击即可运行）
cd /d "%~dp0.."
echo [%date% %time%] 开始抓取...
python -m baoyan run --max-details 20
echo.
echo [%date% %time%] 完成。正在本地预览： http://127.0.0.1:8000/
python -m baoyan serve --port 8000
pause