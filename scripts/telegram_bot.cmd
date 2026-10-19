@echo off
rem Bot de Telegram del equipo (/ambiental, /energia, /agua) leyendo PRODUCCION
rem en vivo por tunel SSH (energia -> 127.0.0.1:55432, ambiental -> 127.0.0.1:5435),
rem igual que scripts\webapp_en_vivo.cmd. No modifica .env: solo sobrescribe
rem estas variables para este proceso. Una sola instancia por PC.
rem Uso: scripts\telegram_bot.cmd   (log en logs\telegram_bot.out.log)
cd /d "%~dp0.."
set ENERGIA_DB_PORT=55432
set USE_SSH_TUNNEL_ENERGIA=true
set AMBIENTAL_DB_PORT=5435
set USE_SSH_TUNNEL_AMBIENTAL=true
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8
set PATH=C:\Windows\System32\OpenSSH;%PATH%
if not exist logs mkdir logs
venv\Scripts\python.exe scripts\telegram_bot.py >> logs\telegram_bot.out.log 2>> logs\telegram_bot.err.log
