#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""Hermes Agent API"""

import json
import os
import re
import subprocess
import uuid
from functools import wraps

from flask import Flask, request, jsonify
from redis import Redis
from rq import Queue
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from database import init_db, create_session, get_session_history, append_message, list_sessions, delete_session, session_exists
from logging_config import setup_logging

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

try:
    app.json.ensure_ascii = False
except AttributeError:
    pass

logger = setup_logging()
_MAX_TURNS = 50
API_KEY = getattr(getattr(os, 'environ'), 'get')('API_KEY', 'dev-key-change-me')

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["100 per hour"],
    storage_uri="memory://"
)

def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get("X-API-Key") or request.args.get("api_key")
        if key != API_KEY:
            logger.warning("Requisicao sem API key valida", extra={"remote_addr": request.remote_addr})
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

REDIS_URL = getattr(getattr(os, 'environ'), 'get')('REDIS_URL', '')
if REDIS_URL:
    redis_conn = Redis.from_url(REDIS_URL)
else:
    import redislite
    redis_conn = Redis(redislite.Redis("/tmp/redis.db"))

task_queue = Queue(connection=redis_conn)

def chat():
    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({"error": "JSON body ausente ou invalido"}), 400

    query = data.get("query")
    if not query or not isinstance(query, str) or not query.strip():
        return jsonify({"error": "Campo query obrigatorio"}), 400

    session_id = data.get("session_id")
    create_new = data.get("new_session", False)

    if create_new:
        session_id = str(uuid.uuid4())
        create_session(session_id)
    elif session_id:
        if not session_exists(session_id):
            return jsonify({"error": "session_id invalida ou expirada"}), 404
    else:
        session_id = str(uuid.uuid4())
        create_session(session_id)

    history = get_session_history(session_id)
    if len(history) >= _MAX_TURNS * 2:
        return jsonify({
            "error": "Sessao atingiu o limite maximo de turnos.",
            "session_id": session_id,
            "turnos": len(history) // 2
        }), 429

    historico_json = json.dumps(history, ensure_ascii=False)
    append_message(session_id, "user", query.strip())

    from worker import process_chat
    job = task_queue.enqueue(
        process_chat,
        session_id,
        query.strip(),
        historico_json
    )

    logger.info("Chat enfileirado", extra={
        "session_id": session_id,
        "job_id": job.get_id(),
        "query_len": len(query)
    })

    payload = {
        "session_id": session_id,
        "job_id": job.get_id(),
        "status": "queued",
        "check_url": f"/jobs/{job.get_id()}"
    }
    return jsonify(payload), 202

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

def list_all_sessions():
    info = list_sessions()
    return jsonify({"total": len(info), "sessions": info})

def get_session(session_id):
    history = get_session_history(session_id)
    if not history and not session_exists(session_id):
        return jsonify({"error": "session_id nao encontrada"}), 404
    return jsonify({
        "session_id": session_id,
        "turnos": len(history) // 2,
        "history": history
    })

def delete_session_endpoint(session_id):
    if not session_exists(session_id):
        return jsonify({"error": "session_id nao encontrada"}), 404
    delete_session(session_id)
    logger.info("Sessao deletada", extra={"session_id": session_id})
    return jsonify({"status": "deleted", "session_id": session_id})

TELEGRAM_TOKEN = getattr(getattr(os, 'environ'), 'get')('TELEGRAM_TOKEN', '')

def telegram_webhook():
    if not TELEGRAM_TOKEN:
        logger.warning("Webhook chamado sem TELEGRAM_TOKEN configurado")
        return jsonify({"error": "Bot nao configurado"}), 503

    if not request.is_json:
        return jsonify({"error": "Esperado JSON"}), 400

    update_data = request.get_json()
    message = update_data.get("message") or {}
    text = message.get("text", "")
    chat_id = message.get("chat", {}).get("id")

    if not text or not chat_id:
        return jsonify({"status": "ignored"}), 200

    sid = f"tg-{chat_id}"
    if not session_exists(sid):
        create_session(sid)

    history = get_session_history(sid)
    historico_json = json.dumps(history, ensure_ascii=False)
    append_message(sid, "user", text)

    from worker import process_chat
    job = task_queue.enqueue(process_chat, sid, text, historico_json)

    logger.info("Telegram msg enfileirada", extra={"chat_id": chat_id, "job_id": job.get_id()})
    return jsonify({"status": "ok", "job_id": job.get_id()}), 200

def health():
    hermes_ok = True
    try:
        subprocess.run(["hermes", "--version"], capture_output=True, timeout=5)
    except Exception:
        hermes_ok = False

    redis_ok = True
    try:
        redis_conn.ping()
    except Exception:
        redis_ok = False

    return jsonify({
        "status": "ok" if hermes_ok else "degraded",
        "hermes_cli": "ok" if hermes_ok else "not_found",
        "sessoes_ativas": len(list_sessions()),
        "redis": "ok" if redis_ok else "down"
    })

app.route("/chat", methods=["POST"])(require_api_key(limiter.limit("10 per minute")(chat)))
app.route("/jobs/<job_id>", methods=["GET"])(require_api_key(get_job_status))
app.route("/sessions", methods=["GET"])(require_api_key(list_all_sessions))
app.route("/sessions/<session_id>", methods=["GET"])(require_api_key(get_session))
app.route("/sessions/<session_id>", methods=["DELETE"])(require_api_key(delete_session_endpoint))
app.route("/telegram", methods=["POST"])(telegram_webhook)
app.route("/health", methods=["GET"])(health)

port = int(getattr(getattr(os, 'environ'), 'get')('PORT', '5000'))
debug = getattr(getattr(os, 'environ'), 'get')('DEBUG', '').lower() in ('1', 'true', 'yes')
if __name__ == "__main__":
    init_db()
    logger.info("API startup", extra={"port": port, "debug": debug})
    print(f"Hermes API rodando em http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=debug)
