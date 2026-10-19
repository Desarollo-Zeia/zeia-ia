@echo off
rem Levanta la webapp leyendo PRODUCCION en tiempo real por tunel SSH
rem (energia -> 127.0.0.1:55432, ambiental -> 127.0.0.1:5435).
rem No modifica .env: solo sobrescribe estas variables para este proceso.
rem La app abre los tuneles sola en la primera consulta (src/tunnel.py).
rem Uso: scripts\webapp_en_vivo.cmd   ->  http://localhost:8000/monitor
cd /d "%~dp0.."
set ENERGIA_DB_PORT=55432
set USE_SSH_TUNNEL_ENERGIA=true
set AMBIENTAL_DB_PORT=5435
set USE_SSH_TUNNEL_AMBIENTAL=true
set PYTHONUNBUFFERED=1
set PATH=C:\Windows\System32\OpenSSH;%PATH%
if not exist logs mkdir logs
venv\Scripts\python.exe webapp.py >> logs\webapp.out.log 2>> logs\webapp.err.log
