# Install Guide

## Lightsail Setup

1. **Criar instancia:** Ubuntu Server 22.04 LTS
2. **Abrir porta 5000** no firewall da instancia
3. **SSH na instancia** e rodar:

```bash
sudo apt update
sudo apt install python3-pip redis-server -y
cd /hermes-api
pip install -r requirements.txt
```

4. **Configurar env vars**:
```bash
cp .env.example .env
nano .env
# edite API_KEY, TELEGRAM_TOKEN, etc.
```

5. **Rodar worker RQ** (em outra sessao screen/tmux):
```bash
cd /opt/hermes-api
rq worker
```

6. **Rodar API**:
```bash
cd /opt/hermes-api
python api.py
```

## Systemd Service (opcional)

Crie `/etc/systemd/system/hermes-api.service`:
```ini
[Unit]
Description=Hermes API
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/opt/hermes-api
Environment=PATH=/usr/bin
ExecStart=/usr/bin/python3 /opt/hermes-api/api.py
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable hermes-api
sudo systemctl start hermes-api
```

## HTTPS / Caddy

Caddy auto-configura Let's Encrypt:
```bash
sudo apt install caddy
```

Crie `/etc/caddy/Caddyfile`:
```
your-domain.com {
    reverse_proxy localhost:5000
}
```

```bash
sudo systemctl restart caddy
```

## Redis

Production: use ElastiCache ou Redis local.
Development: redislite ja esta incluso e fallback automatico.
