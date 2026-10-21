@echo off
rem Avisos automaticos (backtest / ciclo de prueba / estado) leyendo PRODUCCION
rem en vivo por tunel SSH, igual que scripts\telegram_bot.cmd. No modifica .env:
rem solo sobrescribe estas variables para este proceso. No envia mensajes.
rem Uso: scripts\avisos.cmd --backtest --desde 2026-09-23 --hasta 2026-10-20
rem      scripts\avisos.cmd --ciclo
cd /d "%~dp0.."
set ENERGIA_DB_PORT=55432
set USE_SSH_TUNNEL_ENERGIA=true
set AMBIENTAL_DB_PORT=5435
set USE_SSH_TUNNEL_AMBIENTAL=true
set PYTHONIOENCODING=utf-8
set PATH=C:\Windows\System32\OpenSSH;%PATH%
venv\Scripts\python.exe scripts\avisos.py %*
