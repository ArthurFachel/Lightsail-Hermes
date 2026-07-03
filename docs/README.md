# Hermes API

Servidor Flask para expor o CLI Hermes via HTTP e Telegram.

## Features

1. **Persistencia SQLite** - sessoes sobrevivem restart
2. **Queue assincrona** - RQ + redislite, sem bloquear requests
3. **Webhook Telegram** - responde via BotFather
4. **Autenticacao + Rate Limit** - API key + limites por IP
5. **Logging JSON** - formato estruturado para CloudWatch

## Quick Start

```bash
pip install -r requirements.txt
python api.py
```

## Variaveis de ambiente

- `PORT` - porta do Flask (default 5000)
- `DEBUG` - 1/true/yes para modo debug
- `API_KEY` - chave obrigatoria no header X-API-Key
- `REDIS_URL` - URL do Redis para fila (default: redislite em /tmp/redis.db)
- `TELEGRAM_TOKEN` - token do bot Telegram

## Arquitetura

- `api.py` - Flask app, endpoints, middleware
- `database.py` - SQLite persistence layer
- `worker.py` - RQ worker job (roda hermes CLI)
- `logging_config.py` - JSON logging
- `requirements.txt` - dependencias
- `.env.example` - template de env vars
