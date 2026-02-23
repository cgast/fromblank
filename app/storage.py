import json
import sqlite3
import os
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Optional


DATABASE_PATH = os.getenv("DATABASE_PATH", "./data/pages.db")


def _ensure_db_dir():
    db_dir = os.path.dirname(os.path.abspath(DATABASE_PATH))
    os.makedirs(db_dir, exist_ok=True)


def _get_connection() -> sqlite3.Connection:
    _ensure_db_dir()
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def get_db():
    conn = _get_connection()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pages (
                path TEXT PRIMARY KEY,
                html_content TEXT NOT NULL,
                prompt_history TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)


def get_page(path: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT path, html_content, prompt_history, created_at, updated_at FROM pages WHERE path = ?",
            (path,),
        ).fetchone()
        if row is None:
            return None
        return {
            "path": row["path"],
            "html_content": row["html_content"],
            "prompt_history": json.loads(row["prompt_history"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


def save_page(path: str, html_content: str, prompt: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    existing = get_page(path)

    if existing:
        prompt_history = existing["prompt_history"]
        prompt_history.append(prompt)
        with get_db() as conn:
            conn.execute(
                "UPDATE pages SET html_content = ?, prompt_history = ?, updated_at = ? WHERE path = ?",
                (html_content, json.dumps(prompt_history), now, path),
            )
        return {
            "path": path,
            "html_content": html_content,
            "prompt_history": prompt_history,
            "created_at": existing["created_at"],
            "updated_at": now,
        }
    else:
        prompt_history = [prompt]
        with get_db() as conn:
            conn.execute(
                "INSERT INTO pages (path, html_content, prompt_history, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (path, html_content, json.dumps(prompt_history), now, now),
            )
        return {
            "path": path,
            "html_content": html_content,
            "prompt_history": prompt_history,
            "created_at": now,
            "updated_at": now,
        }
