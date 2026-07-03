# API Documentation

## Authentication

Todos os endpoints exceto `/health` e `/telegram` exigem o header:
```
X-API-Key: <sua_api_key>
```

## Endpoints

### POST /chat

Envia uma mensagem para o Hermes. Retorna job_id para polling.

**Request:**
```json
{
  "query": "O que e LLM?",
  "session_id": "optional-existing-uuid",
  "new_session": false
}
```

**Response (202 Accepted):**
```json
{
  "session_id": "uuid-here",
  "job_id": "rq-job-id",
  "status": "queued",
  "check_url": "/jobs/rq-job-id"
}
```

### GET /jobs/<job_id>

Consulta status de um job enfileirado.

**Response:**
```json
{
  "job_id": "rq-job-id",
  "status": "finished",
  "result": {"response": "..."},
  "created_at": "..."
}
```

### GET /sessions

Lista todas as sessoes persistentes (SQLite).

### GET /sessions/<session_id>

Retorna historico completo de uma sessao.

### DELETE /sessions/<session_id>

Remove uma sessao do banco.

### POST /telegram

Webhook para receber updates do Telegram Bot.

**Setup:**
```bash
# Configure o webhook no BotFather
curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook" \
  -d "url=https://seu-dominio.com/telegram"
```

### GET /health

Health check da API.

**Response:**
```json
{
  "status": "ok",
  "hermes_cli": "ok",
  "sessoes_ativas": 5,
  "redis": "ok"
}
```

## Rate Limits

- `/chat`: 10 requests/minute
- Default: 100 requests/hour
