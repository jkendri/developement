@echo off
cd /d "%~dp0"
set "PY=python"
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PY=%LocalAppData%\Programs\Python\Python312\python.exe"

echo Installing build tools...
"%PY%" -m pip install -r requirements.txt pyinstaller -q
if errorlevel 1 (
  echo pip install failed.
  pause
  exit /b 1
)

echo Building Shackboard.exe ...
"%PY%" -m PyInstaller --noconfirm ftdx1200.spec
if errorlevel 1 (
  echo Build failed.
  pause
  exit /b 1
)

echo.
echo Done: dist\Shackboard.exe
explorer dist
