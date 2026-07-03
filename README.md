# Geo — Hermes Agent API com Subagent Worker

API Flask que expõe o **Hermes Agent CLI** via HTTP e Telegram, com fila assíncrona RQ, persistência em JSON e arquitetura **orchestrator/subagent**.

## Arquitetura

```
┌──────────────┐     POST /chat      ┌──────────────────┐     RQ Queue     ┌──────────────────┐
│   Cliente    │ ──────────────────▶  │   api.py         │ ──────────────▶  │   worker.py      │
│ (curl/app)   │ ◀────────────────── │   (Orchestrator) │                  │   (Subagent)     │
│              │    GET /jobs/<id>   │   Flask + Redis   │                  │   hermes CLI     │
└──────────────┘                     └──────────────────┘                  └──────────────────┘
                                              │                                       │
                                              │  JSON (sessoes)                          │ Executa hermes
                                              │  Telegram webhook                     │ chat -q "query"
                                              └───────────────────────────────────────┘
```

**Orchestrator (api.py):** recebe as queries via HTTP ou Telegram, valida API key, gerencia sessões em JSON, enfileira jobs no Redis RQ.

**Subagent (worker.py):** consome a fila, executa `hermes chat -q "query"` em background, grava resposta no banco. O agent principal (api.py) nunca bloqueia — ele orquestra e o worker processa.

---

## 1. Subir máquina no Lightsail AWS

### 1.1 Criar instância

1. Acesse o console AWS Lightsail: https://lightsail.aws.amazon.com
2. Clique **Create instance**
3. Escolha:
   - **Platform:** Linux/Unix
   - **Blueprint:** OS Only → **Ubuntu 22.04 LTS**
   - **Instance plan:** $3.50/mo (512MB RAM, 1 vCPU) — suficiente para testes
   - **Instance name:** `hermes-api` (ou o nome que preferir)
4. Clique **Create instance**

### 1.2 Liberar networking IPv4

1. No console Lightsail, vá em **Networking** da sua instância
2. Clique **Add rule** e adicione:

| Application | Protocol | Port range | Restrict to |
|-------------|----------|------------|-------------|
| HTTP        | TCP      | 80         | 0.0.0.0/0  |
| HTTPS       | TCP      | 443        | 0.0.0.0/0  |
| Custom      | TCP      | 5000       | 0.0.0.0/0  |
| Custom      | TCP      | 22         | SEU_IP/32  |

> **⚠️ Segurança:** a porta 5000 aberta para `0.0.0.0/0` é aceitável para testes, mas em produção coloque um proxy reverso (Caddy/Nginx) com HTTPS e mantenha a porta 22 restrita ao seu IP.

### 1.3 Obter IP público

No console Lightsail, o IP público aparece no painel da instância. Anote-o — você vai usar para SSH e para acessar a API.

---

## 2. Entrar na máquina e instalar tudo

### 2.1 SSH na instância

```bash
# Baixe a chave privada no console Lightsail (Account → SSH keys → Download default)
chmod 400 ~/Downloads/LightsailDefaultKey-us-east-1.pem
ssh -i ~/Downloads/LightsailDefaultKey-us-east-1.pem ubuntu@<IP-PUBLICO>
```

### 2.2 Instalar dependências do sistema

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install python3-pip python3-venv git redis-server -y
```

### 2.3 Clonar o repositório

```bash
cd /opt
sudo git clone https://github.com/fachel/Geo /opt/hermes-api
sudo chown -R ubuntu:ubuntu /opt/hermes-api
cd /opt/hermes-api
```

### 2.4 Instalar dependências Python

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2.5 Instalar Hermes Agent

```bash
pip install hermes-agent
```

### 2.6 Instalar AWS CLI

```bash
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install
rm -rf aws awscliv2.zip
aws --version
```

### 2.7 Configurar variáveis de ambiente

```bash
cp .env.example .env
nano .env
```

Preencha:

```
PORT=5000
DEBUG=false
API_KEY=uma-chave-segura-aqui
TELEGRAM_TOKEN=seu-token-do-botfather
```

### 2.8 Rodar a API

```bash
cd /opt/hermes-api
source venv/bin/activate

# Terminal 1: worker RQ
rq worker

# Terminal 2: API Flask
python api.py
```

---

## 3. Configurar Hermes Agent (Telegram)

### 3.1 Criar o bot no BotFather

1. No Telegram, procure por **@BotFather**
2. Envie `/newbot` e siga as instruções
3. Anote o **token** gerado (ex: `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`)

### 3.2 Configurar o Hermes local

```bash
# No servidor Lightsail
hermes config set telegram.token SEU_TOKEN_AQUI
hermes config set telegram.enabled true
```

### 3.3 Configurar webhook do Telegram

No seu terminal local (ou qualquer máquina com curl):

```bash
curl -X POST "https://api.telegram.org/bot<SEU_TOKEN>/setWebhook" \
  -d "url=http://<IP-PUBLICO>:5000/telegram"
```

Verifique se funcionou:

```bash
curl "https://api.telegram.org/bot<SEU_TOKEN>/getWebhookInfo"
```

Agora o bot responde no Telegram. Cada chat_id vira uma sessão (`tg-<chat_id>`) persistente no JSON.

---

## 4. Arquitetura: Orchestrator + Subagent

### Como funciona

```
                    ┌─────────────────────────────────────┐
                    │         api.py (Orchestrator)        │
                    │                                     │
  POST /chat ──────▶│  1. Valida API key                  │
                    │  2. Cria/recupera sessão JSON        │
                    │  3. Salva query do usuário           │
                    │  4. Enfileira job no Redis RQ        │
                    │  5. Retorna job_id + session_id       │
                    └──────────┬──────────────────────────┘
                               │ RQ Queue
                               ▼
                    ┌──────────────────────────┐
                    │  worker.py (Subagent)     │
                    │                           │
                    │  1. Pega job da fila      │
                    │  2. Monta prompt com       │
                    │     historico da sessao   │
                    │  3. Executa:              │
                    │     hermes chat -q "..."  │
                    │  4. Salva resposta no     │
                    │     JSON (sessions.json)  │
                    └──────────────────────────┘
```

### Orchestrator (api.py)

- Recebe requisições HTTP (POST /chat) e webhooks Telegram
- Valida API key, rate limit, gerencia sessões em JSON
- **Nunca executa o Hermes diretamente** — enfileira jobs no Redis RQ
- Retorna `job_id` + `session_id` imediatamente (202 Accepted)
- Cliente faz polling em `GET /jobs/<job_id>` até status `finished`

### Subagent (worker.py)

- Consome a fila RQ em background
- Monta o prompt com histórico da sessão
- Executa `hermes chat -q "query"` via subprocess
- Grava resposta no JSON (sessions.json)
- Pode escalar horizontalmente: múltiplos workers consomem a mesma fila

### Fluxo completo

```
Cliente ──POST /chat──▶ api.py ──enfileira──▶ RQ Queue ──▶ worker.py
   │                                                         │
   │◀── 202 {job_id, session_id} ──── api.py ◀──────────────┘
   │                                                         
   └── GET /jobs/<job_id> ──▶ api.py ──▶ {status: "finished", result: {...}}
```

---

## 5. Configurar o Hermes Agent como Orchestrator + Subagent

O Hermes Agent tem suporte nativo a **delegate_task** — o agent principal (orchestrator) pode delegar tarefas para subagents que rodam em contextos isolados.

### 5.1 Criar o soul.md do Orchestrator

O `soul.md` define a personalidade e regras do agent. Crie ou edite:

```bash
nano ~/soul.md
```

Conteúdo sugerido para o **orchestrator**:

```markdown
# Orchestrator — Lightsail Hermes API

Você é o orchestrator da API Hermes no Lightsail. Suas funções:

1. Receber queries dos usuários via API (POST /chat) ou Telegram
2. Delegar tarefas complexas para subagents via delegate_task
3. Gerenciar sessões e histórico no JSON (sessions.json)
4. Responder com resultados consolidados

Regras:
- Nunca execute tarefas longas diretamente — delegue para subagents
- Subagents rodam em contextos isolados com terminal próprio
- Sempre retorne respostas em português
- Mantenha o histórico da sessão para contexto
```

### 5.2 Criar o soul.md do Subagent

O subagent (worker.py) executa `hermes chat` com um prompt montado. Para configurar a personalidade dele no Hermes:

```bash
# Cria um profile separado para o subagent
hermes config set profile subagent
hermes config set profile.subagent.soul_path ~/soul-subagent.md
```

Crie o `~/soul-subagent.md`:

```markdown
# Subagent — Worker de Processamento

Você é um subagent especializado em responder queries de usuários.

Regras:
- Responda de forma direta e objetiva
- Use português brasileiro
- Mantenha respostas concisas (máx 3 parágrafos)
- Se não souber a resposta, diga claramente
- Use ferramentas externas se necessario
- Seu output será salvo no JSON (sessions.json) e entregue ao usuário via polling
```

### 5.3 Configurar o Hermes para usar delegate_task

No `soul.md` do orchestrator, adicione a instrução de delegação:

```markdown
- Para queries que exigem raciocínio profundo, use:
  delegate_task(goal="Responda: {query}", context="Histórico: {historico}")
- Nunca processe queries longas diretamente — sempre delegue
```

### 5.4 Testar a delegação

```bash
# Pelo terminal do servidor
hermes chat -q "Explique o que é uma LLM" -Q

# Pela API
curl -X POST http://localhost:5000/chat \
  -H "X-API-Key: sua-chave" \
  -H "Content-Type: application/json" \
  -d '{"query": "Explique o que é uma LLM"}'
```

---

## 6. API Endpoints

| Método | Rota | Autenticação | Descrição |
|--------|------|-------------|-----------|
| POST | `/chat` | API Key | Envia query, retorna job_id |
| GET | `/jobs/<id>` | API Key | Polling de resultado |
| GET | `/sessions` | API Key | Lista sessões ativas |
| GET | `/sessions/<id>` | API Key | Histórico da sessão |
| DELETE | `/sessions/<id>` | API Key | Remove sessão |
| POST | `/telegram` | — | Webhook do Telegram Bot |
| GET | `/health` | — | Health check |

### Exemplo de uso

```bash
# Enviar query
curl -X POST http://<IP>:5000/chat \
  -H "X-API-Key: sua-chave" \
  -H "Content-Type: application/json" \
  -d '{"query": "O que é LLM?"}'

# Resposta: {"session_id": "...", "job_id": "...", "status": "queued", "check_url": "/jobs/..."}

# Polling do resultado
curl -H "X-API-Key: sua-chave" http://<IP>:5000/jobs/<job_id>
```

---

## 7. Systemd (produção)

```bash
sudo tee /etc/systemd/system/hermes-api.service > /dev/null <<'EOF'
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
EOF

sudo systemctl daemon-reload
sudo systemctl enable hermes-api
sudo systemctl start hermes-api
```

### Worker RQ como serviço separado

```bash
sudo tee /etc/systemd/system/hermes-worker.service > /dev/null <<'EOF'
[Unit]
Description=Hermes RQ Worker
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/opt/hermes-api
ExecStart=/usr/bin/python3 -m rq worker
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable hermes-worker
sudo systemctl start hermes-worker
```

---

## 8. HTTPS com Caddy (produção)

```bash
sudo apt install caddy -y
```

Crie `/etc/caddy/Caddyfile`:

```
seu-dominio.com {
    reverse_proxy localhost:5000
}
```

```bash
sudo systemctl restart caddy
```

---

## 9. Subagent: como funciona

O `worker.py` é um **subagent** que:

1. **Escuta a fila RQ** — cada job é uma query de usuário
2. **Monta o prompt** com o histórico da sessão (últimos N turnos)
3. **Executa `hermes chat -q "query"`** via subprocess
4. **Salva a resposta** no JSON (sessions.json)
5. **Retorna o resultado** para o job (disponível via GET /jobs/<id>)

O **orchestrator (api.py)** nunca bloqueia esperando o Hermes responder. Ele enfileira e devolve um `job_id` imediatamente. O cliente faz polling.

### Escalando workers

```bash
# Rodar múltiplos workers em paralelo
rq worker --num-workers 4
```

Cada worker é um subagent independente — todos consomem da mesma fila RQ. O Redis (ou redislite) gerencia a distribuição.

---

## Variáveis de Ambiente

| Variável | Obrigatória | Padrão | Descrição |
|----------|-------------|--------|-----------|
| `PORT` | Não | 5000 | Porta do Flask |
| `DEBUG` | Não | false | Modo debug |
| `API_KEY` | **Sim** | dev-key-change-me | Chave para header X-API-Key |
| `REDIS_URL` | Não | redislite local | URL do Redis para fila RQ |
| `TELEGRAM_TOKEN` | **Sim** | — | Token do BotFather |
| `MAX_TURNS` | Não | 50 | Máximo de turnos por sessão |

---

## Licença

MIT
