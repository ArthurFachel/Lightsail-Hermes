"""Persistencia de sessoes em JSON — substitui SQLite."""
import json
import os
import threading

DB_PATH = "./sessions.json"
_lock = threading.Lock()


def _load():
    if not os.path.exists(DB_PATH):
        return {}
    with open(DB_PATH, "r") as f:
        return json.load(f)


def _save(data: dict):
    with open(DB_PATH, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def init_db():
    """Cria o arquivo JSON se nao existir."""
    if not os.path.exists(DB_PATH):
        _save({})


def create_session(session_id: str):
    with _lock:
        data = _load()
        if session_id not in data:
            data[session_id] = {"messages": []}
            _save(data)


def get_session_history(session_id: str):
    with _lock:
        data = _load()
        return data.get(session_id, {}).get("messages", [])


def append_message(session_id: str, role: str, content: str):
    with _lock:
        data = _load()
        if session_id not in data:
            data[session_id] = {"messages": []}
        data[session_id]["messages"].append({"role": role, "content": content})
        _save(data)


def list_sessions():
    with _lock:
        data = _load()
        return [
            {
                "session_id": sid,
                "msg_count": len(s["messages"]),
                "last_msg": s["messages"][-1]["content"][:80] if s["messages"] else None,
            }
            for sid, s in data.items()
        ]


def delete_session(session_id: str):
    with _lock:
        data = _load()
        data.pop(session_id, None)
        _save(data)


def session_exists(session_id: str) -> bool:
    with _lock:
        data = _load()
        return session_id in data
