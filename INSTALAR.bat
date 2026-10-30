@echo off
title Sigma - Instalacion
cd /d "%~dp0"
echo.
echo  ==========================================
echo    SIGMA - Instalacion (solo la primera vez)
echo  ==========================================
echo.

set "PY="
python -c "import sys; assert sys.version_info >= (3, 10)" >nul 2>&1 && set "PY=python"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"

if not defined PY (
  echo [1/3] Python no esta instalado. Instalando Python 3.12 con winget...
  winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
  if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
) else (
  echo [1/3] Python ya esta instalado.
)

if not defined PY (
  echo.
  echo  No se pudo instalar Python automaticamente.
  echo  Instalalo desde https://www.python.org/downloads/ marcando "Add python.exe to PATH"
  echo  y vuelve a ejecutar este archivo.
  pause
  exit /b 1
)

echo [2/3] Creando entorno de Python en la carpeta .venv ...
"%PY%" -m venv .venv
if errorlevel 1 (
  echo  Error creando el entorno.
  pause
  exit /b 1
)

echo [3/3] Instalando dependencias, puede tardar 1-2 minutos...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
if errorlevel 1 (
  echo  Error instalando dependencias. Revisa tu conexion a internet y vuelve a intentar.
  pause
  exit /b 1
)

echo.
echo  Instalacion completa. Abriendo Sigma...
echo.
call "%~dp0INICIAR.bat"
