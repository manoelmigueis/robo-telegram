#!/usr/bin/env bash
# Instala o robô numa VM Ubuntu (Oracle Cloud Always Free).
# Uso, dentro da VM, na pasta do robô:  bash servidor/instalar.sh
set -e
PASTA="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PASTA"

sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip curl

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r servidor/requirements.txt
.venv/bin/python -m playwright install --with-deps chromium

# Serviço: liga sozinho quando a VM liga e reinicia se cair
sudo tee /etc/systemd/system/robo-telegram.service >/dev/null <<EOF
[Unit]
Description=Robo do Telegram (painel + motor)
After=network-online.target

[Service]
WorkingDirectory=$PASTA
ExecStart=$PASTA/.venv/bin/python -u -X utf8 painel.py
Restart=always
RestartSec=10
User=$USER
Environment=TZ=America/Manaus

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now robo-telegram

# Endereço https grátis para o painel (Cloudflare Tunnel rápido)
curl -fsSL -o /tmp/cloudflared.deb \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$(dpkg --print-architecture).deb
sudo dpkg -i /tmp/cloudflared.deb

echo
echo "Robô ligado. Para ver o log:  journalctl -u robo-telegram -f"
echo "Para abrir o painel de fora:  cloudflared tunnel --url http://localhost:8800"
