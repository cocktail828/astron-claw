import hashlib
import secrets
import sqlite3
import time
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "tokens.db"

SESSION_TTL = 86400  # 24 hours


class AdminAuth:
    """Admin password auth with SQLite storage and in-memory sessions."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=DELETE")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS admin_config ("
            "  key TEXT PRIMARY KEY,"
            "  value TEXT NOT NULL"
            ")"
        )
        self._conn.commit()
        # session_token -> expire_time
        self._sessions: dict[str, float] = {}

    def is_password_set(self) -> bool:
        row = self._conn.execute(
            "SELECT value FROM admin_config WHERE key = 'password_hash'"
        ).fetchone()
        return row is not None

    def set_password(self, password: str) -> None:
        salt = secrets.token_hex(16)
        pw_hash = hashlib.sha256((salt + password).encode()).hexdigest()
        self._conn.execute(
            "INSERT OR REPLACE INTO admin_config (key, value) VALUES ('password_salt', ?)",
            (salt,),
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO admin_config (key, value) VALUES ('password_hash', ?)",
            (pw_hash,),
        )
        self._conn.commit()

    def verify_password(self, password: str) -> bool:
        salt_row = self._conn.execute(
            "SELECT value FROM admin_config WHERE key = 'password_salt'"
        ).fetchone()
        hash_row = self._conn.execute(
            "SELECT value FROM admin_config WHERE key = 'password_hash'"
        ).fetchone()
        if not salt_row or not hash_row:
            return False
        expected = hashlib.sha256(
            (salt_row[0] + password).encode()
        ).hexdigest()
        return secrets.compare_digest(expected, hash_row[0])

    def create_session(self) -> str:
        token = secrets.token_hex(32)
        self._sessions[token] = time.time() + SESSION_TTL
        return token

    def validate_session(self, session_token: str | None) -> bool:
        if not session_token:
            return False
        expire = self._sessions.get(session_token)
        if expire is None:
            return False
        if time.time() > expire:
            del self._sessions[session_token]
            return False
        return True

    def remove_session(self, session_token: str | None) -> None:
        if session_token:
            self._sessions.pop(session_token, None)
