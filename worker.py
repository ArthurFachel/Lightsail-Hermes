"""
RQ Worker job: executa o CLI hermes em background.
A fila (Queue) usa Redis local (redislite) ou um Redis real via REDIS_URL.
"""

import json
import os
import re
import subprocess

# database.py precisa estar no path para funcionar como job RQ
import sys
sys.path.insert(0, os.path.dirname(__file__))

from database import append_message

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_MAX_TURNS = int(os.environ.get("MAX_TURNS", "50"))


def _clean_stdout(raw: str) -> str:
    text = _ANSI_RE.sub("", raw).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        text = text[1:-1].strip()
    return text


def _build_query(historico_anterior: list, query: str) -> str:
    """Reconstroi prompt com historico embutido (mesma logica da API)."""
    if not historico_anterior:
        return query
    historico_json = json.dumps(historico_anterior, ensure_ascii=False, indent=2)
    instruction = (
        "INSTRUCAO DE SISTEMA: a pergunta a seguir faz parte de uma sessao ja em "
        "andamento. Responda usando as informacoes contidas no "
        "historico desta sessao (fornecido em JSON abaixo). "
    )
    return f"{instruction}\n\nHistorico da sessao (JSON):\n{historico_json}\n\nPergunta atual: {query}"


def process_chat(session_id: str, query: str, historico_json: str):
    """Job principal: roda hermes CLI e grava resposta no SQLite."""
    historico_anterior = json.loads(historico_json) if historico_json else []
    query_to_send = _build_query(historico_anterior, query)

    cmd = ["hermes", "chat", "-q", query_to_send, "-Q"]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except FileNotFoundError:
        resposta = "Erro: comando 'hermes' nao encontrado no PATH."
        append_message(session_id, "assistant", resposta)
        return {"session_id": session_id, "response": resposta, "error": "hermes_not_found"}
    except subprocess.TimeoutExpired:
        resposta = "Erro: tempo limite excedido (120s)."
        append_message(session_id, "assistant", resposta)
        return {"session_id": session_id, "response": resposta, "error": "timeout"}

    if result.returncode != 0:
        stderr = result.stderr.strip() or "(sem saida de erro)"
        resposta = f"Erro no hermes (rc={result.returncode}): {stderr}"
    else:
        resposta = _clean_stdout(result.stdout)

    append_message(session_id, "assistant", resposta)
    return {"session_id": session_id, "response": resposta}
