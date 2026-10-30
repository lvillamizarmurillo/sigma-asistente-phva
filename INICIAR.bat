@echo off
title Sigma - Servidor (no cierres esta ventana)
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Primero ejecuta INSTALAR.bat
  pause
  exit /b 1
)
echo.
echo  Sigma esta corriendo en http://localhost:8000
echo  Deja esta ventana abierta. Para apagar Sigma, cierrala o presiona Ctrl+C.
echo.
start "" cmd /c "timeout /t 4 >nul & start http://localhost:8000"
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
