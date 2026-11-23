@echo off
setlocal
cd /d "%~dp0"
if not exist "backend\.venv\Scripts\python.exe" (
  echo Follow the Windows setup in README.md to install backend dependencies.
  exit /b 1
)
if not exist "frontend\dist\index.html" (
  pushd frontend
  call npm.cmd ci
  if errorlevel 1 exit /b 1
  call npm.cmd run build
  if errorlevel 1 exit /b 1
  popd
)
cd backend
echo AI Video SEO Analysis System: http://127.0.0.1:8000
".venv\Scripts\python.exe" -m uvicorn app:app --host 127.0.0.1 --port 8000
