@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Pokalatro - servidor de pruebas
echo ==============================================
echo   Pokalatro - conexion al servidor de pruebas
echo ==============================================
echo.
set "SERVIDOR=32.201.29.142"
set "PUERTO=9000"
echo Comprobando que el servidor responda en %SERVIDOR%:%PUERTO% ...
powershell -NoProfile -Command "if ((Test-NetConnection %SERVIDOR% -Port %PUERTO% -WarningAction SilentlyContinue).TcpTestSucceeded) { Write-Host 'OK: el servidor responde.' -ForegroundColor Green; exit 0 } else { Write-Host 'El servidor NO responde todavia. El bot igual va a reintentar solo.' -ForegroundColor Yellow; exit 0 }"
echo.
set /p "TOKEN=Pega el token del equipo y presiona Enter: "
if "%TOKEN%"=="" (
  echo No escribiste ningun token.
  pause
  exit /b 1
)
set "POKALATRO_TXT=%~dp0partida.log"
set "POKALATRO_LOG=%~dp0decisiones.jsonl"
echo.
echo Bot conectando... (todo queda tambien en partida.log)
echo Para detenerlo: Ctrl+C o cerrar esta ventana.
echo.
py bot.py -addr %SERVIDOR%:%PUERTO% -token %TOKEN% -persistente -verboso
echo.
echo El bot termino.
pause
