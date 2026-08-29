@echo off
rem Debug Toolbox launcher
cd /d "%~dp0"
where pyw >nul 2>nul
if %errorlevel%==0 (
  start "" pyw "%~dp0调试工具箱.py"
) else (
  where pythonw >nul 2>nul
  if %errorlevel%==0 (
    start "" pythonw "%~dp0调试工具箱.py"
  ) else (
    start "" python "%~dp0调试工具箱.py"
  )
)
