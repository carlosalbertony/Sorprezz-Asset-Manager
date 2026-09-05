@echo off
setlocal
cd /d "%~dp0"

echo ================================================
echo  Sorprezz Asset Manager - Compilador Windows
echo ================================================

echo [1/5] Creando entorno de compilacion...
py -3.12 -m venv .venv || goto :error
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip

 echo [2/5] Instalando dependencias...
pip install -r requirements-build.txt || goto :error

 echo [3/5] Compilando aplicacion...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
pyinstaller --noconfirm --clean --windowed ^
  --name SorprezzAssetManager ^
  --icon assets\sorprezz.ico ^
  --add-data "web;web" ^
  --collect-all webview ^
  desktop.py || goto :error

 echo [4/5] Buscando Inno Setup...
set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
  echo No se encontro Inno Setup 6.
  echo Instala Inno Setup 6 y vuelve a ejecutar este archivo.
  goto :error
)

 echo [5/5] Creando instalador...
"%ISCC%" installer\SorprezzAssetManager.iss || goto :error

echo.
echo LISTO.
echo El instalador esta en la carpeta: release
pause
exit /b 0

:error
echo.
echo Ocurrio un error durante la compilacion.
pause
exit /b 1
