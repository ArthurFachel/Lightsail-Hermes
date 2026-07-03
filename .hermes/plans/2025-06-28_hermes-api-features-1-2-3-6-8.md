# Hermes API — Implementation Plan

> **For Hermes:** Implement this plan task-by-task. Document every feature.

**Goal:** Adicionar persistencia SQLite, queue assincrona, webhook Telegram, autenticacao + rate limit, e logging estruturado JSON na API Flask existente.

**Architecture:** API Flask mantem a estrutura atual. SQLite armazena sessoes e historico. Redis + RQ gerencia fila de processamento do hermes CLI. Webhook do Telegram recebe updates do BotFather e reutiliza a mesma logica de sessao. API key simples com rate limit por IP/token. Logging JSON para ingestao em CloudWatch.

**Tech Stack:** Flask, SQLite, Redis, RQ (Redis Queue), python-telegram-bot (webhook), Flask-Limiter, python-json-logger.

**Base:** `/home/fachel/Desktop/Geo/api.py` (Flask, sessoes em RAM, subprocess.run sincrono).

---

## Task 1: Setup — Estrutura de diretorios e dependencias

**Objective:** Preparar o projeto para receber as novas features.

**Files:**
- Create: `/home/fachel/Desktop/Geo/requirements.txt`
- Create: `/home/fachel/Desktop/Geo/docs/README.md`
- Create: `/home/fachel/Desktop/Geo/.env.example`

**Step 1: requirements.txt**

```
flask>=2.0
redislite>=0.1
rq>=1.10
python-telegram-bot>=20.0
flask-limiter>=3.0
python-json-logger>=2.0
Werkzeug>=2.0
```

**Step 2: .env.example**

```
FLASK_PORT=5000
FLASK_DEBUG=false
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_WEBHOOK_URL=https://your-domain.com/telegram
API_KEY=change-me-please
REDIS_URL=redis://localhost:6379/0
DATABASE_PATH=./hermes_api.db
```

**Step 3: Criar diretorios**

```bash
mkdir -p /home/fachel/Desktop/Geo/docs
```

**Verification:** `cat /home/fachel/Desktop/Geo/requirements.txt` deve listar pacotes.

---

## Task 2: Feature 1 — Persistencia SQLite

**Objective:** Substituir `_sessions` em RAM por tabelas SQLite. Sessoes sobrevivem restart.

**Files:**
- Create: `/home/fachel/Desktop/Geo/database.py`
- Modify: `/home/fachel/Desktop/Geo/api.py`

**Step 1: Criar database.py**

```python
import sqlite3
import json
from contextlib import contextmanager

DATABASE_PATH = "./hermes_api.db"

@contextmanager
def get_db():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT CHECK(role IN ('user', 'assistant')) NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at)
        """)

def create_session(session_id: str):
    with get_db() as conn:
        conn.execute("INSERT OR IGNORE INTO sessions (session_id) VALUES (?)", (session_id,))

def get_session_history(session_id: str):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY created_at",
            (session_id,)
        ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in rows]

def append_message(session_id: str, role: str, content: str):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
            (session_id, role, content)
        )
        conn.execute(
            "UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE session_id = ?",
            (session_id,)
        )

def list_sessions():
    with get_db() as conn:
        rows = conn.execute("""
            SELECT s.session_id, COUNT(m.id) as msg_count, MAX(m.created_at) as last_msg
            FROM sessions s
            LEFT JOIN messages m ON s.session_id = m.session_id
            GROUP BY s.session_id
        """).fetchall()
        return [dict(r) for r in rows]

def delete_session(session_id: str):
    with get_db() as conn:
        conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))

def session_exists(session_id: str) -> bool:
    with get_db() as conn:
        row = conn.execute("SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        return row is not None
```

**Step 2: Modificar api.py**

Remover `_sessions = {}` e `_lock`. Substituir todas as operacoes de sessao por chamadas a `database.py`.

```python
# NO TOPO DO ARQUIVO
from database import init_db, create_session, get_session_history, append_message, list_sessions, delete_session, session_exists

# NO FINAL DO if __name__ == '__main__':
init_db()
```

- `chat()`: usar `session_exists`, `create_session`, `get_session_history`, `append_message`
- `list_sessions()`: usar `list_sessions()`
- `get_session()`: usar `get_session_history()`
- `delete_session()`: usar `delete_session()`
- Remover `_lock` (SQLite ja e thread-safe por default; mas Flask e single-threaded em dev)

**Step 3: Testar**

```bash
cd /home/fachel/Desktop/Geo
python -c "from database import init_db, create_session, get_session_history; init_db(); create_session('test-123'); append_message('test-123', 'user', 'oi'); print(get_session_history('test-123'))"
```

Expected: `[{'role': 'user', 'content': 'oi'}]`

---

## Task 3: Feature 2 — Queue Assincrona (RQ + Redis)

**Objective:** Nao bloquear a thread do Flask enquanto hermes processa.

**Files:**
- Create: `/home/fachel/Desktop/Geo/worker.py`
- Modify: `/home/fachel/Desktop/Geo/api.py`

**Step 1: worker.py**

```python
import subprocess
from database import append_message
from api import _clean_response, _build_scoped_query

def process_chat(session_id: str, query: str, historico_json: str):
    """Executa hermes CLI em background."""
    query_to_send = _build_scoped_query(historico_json, query)
    cmd = ["hermes", "chat", "-q", query_to_send, "-Q"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        resposta = f"Erro no hermes: {result.stderr.strip() or '(sem erro)'})"
    else:
        resposta = _clean_response(result.stdout)
    append_message(session_id, "assistant", resposta)
    return {"session_id": session_id, "response": resposta}
```

**Step 2: Modificar api.py**

```python
from redis import Redis
from rq import Queue

redis_conn = Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
task_queue = Queue(connection=redis_conn)

# Novo endpoint para checar status do job
@app.route('/jobs/<job_id>', methods=['GET'])
def get_job_status(job_id):
    job = task_queue.fetch_job(job_id)
    if job is None:
        return jsonify({"error": "Job nao encontrado"}), 404
    return jsonify({
        "job_id": job_id,
        "status": job.get_status(),
        "result": job.result,
        "created_at": str(job.created_at) if job.created_at else None
    })
```

Alterar `chat()`:
- Enfileirar job em vez de rodar subprocess direto
- Retornar `job_id` imediatamente com status 202 Accepted

```python
@app.route('/chat', methods=['POST'])
def chat():
    ...
    # historico_json = json.dumps(...)
    job = task_queue.enqueue(process_chat, session_id, query.strip(), historico_json)
    return jsonify({
        "session_id": session_id,
        "job_id": job.get_id(),
        "status": "queued",
        "check_url": f"/jobs/{job.get_id()}"
    }), 202
```

**Step 3: Testar**

Terminal 1: `rq worker` (ou `python -m rq worker`)
Terminal 2:
```bash
curl -X POST http://localhost:5000/chat -H "Content-Type: application/json" -d '{"query":"hello"}'
# Deve retornar job_id e status "queued"
curl http://localhost:5000/jobs/<job_id>
# Deve eventualmente mostrar "finished" e resultado
```

**Step 4: Documentar**

```bash
echo "## Feature 2: Queue Assincrona" >> docs/README.md
```

---

## Task 4: Feature 3 — Webhook Telegram

**Objective:** Endpoint `/telegram` que recebe updates do Telegram e responde via bot.

**Files:**
- Create: `/home/fachel/Desktop/Geo/telegram_bot.py`
- Modify: `/home/fachel/Desktop/Geo/api.py`

**Step 1: telegram_bot.py**

```python
import os
from telegram import Update
from telegram.ext import Application

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

app_telegram = Application.builder().token(TELEGRAM_TOKEN).build()

async def handle_telegram_update(update_data: dict):
    update = Update.de_json(update_data, app_telegram.bot)
    if update.message and update.message.text:
        chat_id = update.message.chat_id
        text = update.message.text
        # Reutiliza logica da API
        # Aqui simplificamos chamando a funcao interna
        return {"chat_id": chat_id, "text": text}
    return None
```

**Step 2: Modificar api.py**

```python
@app.route('/telegram', methods=['POST'])
def telegram_webhook():
    if not request.is_json:
        return jsonify({"error": "Esperado JSON"}), 400
    update_data = request.get_json()
    result = handle_telegram_update(update_data)
    if result:
        # Enfileirar job de resposta
        # Ou usar InlineKeyboard para sessao
        return jsonify({"status": "ok"}), 200
    return jsonify({"status": "ignored"}), 200
```

**Step 3: Comando de setup do webhook**

```bash
curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook" -d "url=https://seu-dominio.com/telegram"
```

---

## Task 5: Feature 6 — Autenticacao + Rate Limit

**Objective:** Proteger endpoints com API key e limitar requisicoes.

**Files:**
- Modify: `/home/fachel/Desktop/Geo/api.py`

**Step 1: Adicionar imports e middleware**

```python
from functools import wraps
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

API_KEY = os.environ.get("API_KEY", "dev-key-change-me")

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["100 per hour"],
    storage_uri=os.environ.get("REDIS_URL", "memory://")
)

def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get("X-API-Key") or request.args.get("api_key")
        if key != API_KEY:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated
```

**Step 2: Aplicar aos endpoints**

```python
@app.route('/chat', methods=['POST'])
@require_api_key
@limiter.limit("10 per minute")
def chat():
    ...
```

Endpoints protegidos: `/chat`, `/sessions`, `/sessions/<id>`, `/jobs/<id>`
`/health` e `/telegram` podem ficar publicos (ou `/telegram` valida assinatura do Telegram).

**Step 3: Testar**

```bash
curl -X POST http://localhost:5000/chat -H "Content-Type: application/json" -d '{"query":"oi"}'
# Expected: 401 Unauthorized

curl -X POST http://localhost:5000/chat -H "X-API-Key: dev-key-change-me" -H "Content-Type: application/json" -d '{"query":"oi"}'
# Expected: 202 Accepted
```

---

## Task 6: Feature 8 — Logging Estruturado JSON

**Objective:** Logs em formato JSON para CloudWatch ou outro agregador.

**Files:**
- Create: `/home/fachel/Desktop/Geo/logging_config.py`
- Modify: `/home/fachel/Desktop/Geo/api.py`

**Step 1: logging_config.py**

```python
import logging
import sys
from pythonjsonlogger import jsonlogger

def setup_logging():
    logHandler = logging.StreamHandler(sys.stdout)
    formatter = jsonlogger.JsonFormatter(
        '%(timestamp)s %(level)s %(name)s %(message)s %(pathname)s %(lineno)d',
        rename_fields={"level": "severity", "timestamp": "@timestamp"}
    )
    logHandler.setFormatter(formatter)
    root_logger = logging.getLogger()
    root_logger.addHandler(logHandler)
    root_logger.setLevel(logging.INFO)

    # Flask logger
    flask_logger = logging.getLogger('werkzeug')
    flask_logger.handlers = []
    flask_logger.addHandler(logHandler)
```

**Step 2: Modificar api.py**

```python
from logging_config import setup_logging
import logging

setup_logging()
logger = logging.getLogger(__name__)

# Exemplos de uso
logger.info("API startup", extra={"port": port})
logger.info("Chat request", extra={"session_id": session_id, "query_len": len(query)})
logger.error("Hermes subprocess failed", extra={"returncode": result.returncode})
```

**Verification:**

```bash
curl ...
# No terminal deve aparecer JSON {"@timestamp": ..., "severity": "INFO", ...}
```

---

## Task 7: Documentacao Completa

**Objective:** Documentar todas as features em `/home/fachel/Desktop/Geo/docs/`.

**Files:**
- Create: `/home/fachel/Desktop/Geo/docs/README.md`
- Create: `/home/fachel/Desktop/Geo/docs/INSTALL.md`
- Create: `/home/fachel/Desktop/Geo/docs/API.md`

### README.md

```markdown
# Hermes API

Servidor Flask para expor o CLI Hermes via HTTP e Telegram.

## Features

1. **Persistencia SQLite** — sessoes sobrevivem restart
2. **Queue assincrona** — RQ + Redis, sem bloquear requests
3. **Webhook Telegram** — responde via @BotFather
4. **Autenticacao + Rate Limit** — API key + limites por IP
5. **Logging JSON** — formato estruturado para CloudWatch

## Quick Start

```bash
pip install -r requirements.txt
python api.py
```
```

### API.md

Documentacao de todos os endpoints com exemplos de curl.

### INSTALL.md

- Como configurar o Lightsail
- Como subir Redis
- Como registrar o webhook no Telegram
- Variaveis de ambiente

**Step 1: Escrever docs**

**Step 2: Commit git**

```bash
git init  # se nao existir
git add .
git commit -m "feat: implementa features 1,2,3,6,8 com documentacao"
```

---

## Validation Checklist

- [ ] SQLite: sessoes persistem apos restart da API
- [ ] RQ: POST /chat retorna job_id e nao bloqueia
- [ ] Telegram: mensagem no bot gera resposta
- [ ] Auth: sem X-API-Key retorna 401
- [ ] Rate limit: 11 reqs/min retorna 429
- [ ] Logs: saida e JSON valido com campo @timestamp
- [ ] Docs: README, INSTALL, API estao completos

---

## Risks & Mitigacoes

| Risco | Mitigacao |
|-------|-----------|
| Redis nao disponivel no Lightsail | Usar redislite (SQLite como backend Redis) ou subir Redis via docker |
| hermes CLI nao esta no PATH | Documentar instalacao em INSTALL.md |
| Webhook precisa de HTTPS | Usar Caddy ou Let's Encrypt no Lightsail |
| SQLite concorrencia | SQLite suporta WAL mode; para alta carga, migrar para PostgreSQL |
