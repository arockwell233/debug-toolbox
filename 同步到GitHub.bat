@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [1/3] 检查本地改动...
git add -A
set "count=0"
for /f %%i in ('git status --porcelain ^| find /c /v ""') do set "count=%%i"
if "%count%"=="0" (
  echo 没有需要同步的改动。
  pause
  exit /b
)
echo [2/3] 提交改动...
git commit -m "更新 %date% %time%"
echo [3/3] 推送到 GitHub...
git push origin main
echo.
echo 已同步到 GitHub，完成。
pause
