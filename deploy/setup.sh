#!/usr/bin/env bash
# Setup inicial del agente ZEIA en una EC2 Ubuntu (misma VPC que la DB).
# Uso (tras clonar el repo en ~/zeia-ia):
#   bash deploy/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Paquetes del sistema"
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip nginx certbot python3-certbot-nginx

echo "==> Entorno Python"
python3 -m venv venv
venv/bin/pip install -r requirements.txt

echo "==> .env del servidor (una sola vez)"
if [ ! -f .env ]; then
  cp deploy/.env.server.example .env
  echo ">> ATENCIÓN: edita .env con tus credenciales antes de seguir:"
  echo "   nano .env"
fi

echo "==> Servicio systemd"
sudo cp deploy/zeia-agent.service /etc/systemd/system/zeia-agent.service
sudo systemctl daemon-reload
sudo systemctl enable zeia-agent
sudo systemctl restart zeia-agent

echo "==> nginx"
sudo cp deploy/zeia-agent.nginx.conf /etc/nginx/sites-available/zeia-agent
sudo ln -sf /etc/nginx/sites-available/zeia-agent /etc/nginx/sites-enabled/zeia-agent
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx

echo ""
echo "==> Listo. Próximos pasos:"
echo "    1) Edita .env (nano .env) y reinicia: sudo systemctl restart zeia-agent"
echo "    2) DNS: apunta agent.energy.zeia.com.pe a la IP pública de esta instancia"
echo "    3) HTTPS: sudo certbot --nginx -d agent.energy.zeia.com.pe"
echo "    4) Verifica: curl http://127.0.0.1:8000/api/models"
