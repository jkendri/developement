@echo off
cd /d "%~dp0"
set "PY=python"
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PY=%LocalAppData%\Programs\Python\Python312\python.exe"
"%PY%" -m ftdx1200 %*
if errorlevel 1 (
  echo.
  echo If Python packages are missing, run:
  echo   "%PY%" -m pip install -r requirements.txt
  pause
)
