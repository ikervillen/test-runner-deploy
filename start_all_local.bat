@echo off
setlocal
cd /d "%~dp0"

start "Calidad produccion - Backend" cmd /k call "%~dp0start_backend_local.bat"
start "Calidad produccion - Frontend" cmd /k call "%~dp0start_frontend_local.bat"

echo.
echo Servicios iniciados:
echo   Frontend: http://127.0.0.1:5174
echo   API docs: http://127.0.0.1:8001/api/docs
