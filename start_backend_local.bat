@echo off
setlocal
cd /d "%~dp0"

if not exist .venv (
  uv sync --dev --python 3.11
  if errorlevel 1 exit /b 1
)

echo Backend: http://127.0.0.1:8001
uv run uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8001
