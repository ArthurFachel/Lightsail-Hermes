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
