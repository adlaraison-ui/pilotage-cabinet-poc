from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from src.config import load_settings

@contextmanager
def get_conn():
    settings = load_settings()
    conn = sqlite3.connect(settings.db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        yield conn
        conn.commit()
    finally:
        conn.close()
