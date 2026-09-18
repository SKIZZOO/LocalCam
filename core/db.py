from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


class EventStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=1.5)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA busy_timeout=1500')
        return conn

    def _init(self) -> None:
        with self.lock:
            conn = self._conn()
            conn.executescript(
                '''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    camera_id TEXT NOT NULL,
                    camera_name TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'motion',
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    snapshot_path TEXT,
                    acknowledged INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_events_started ON events(started_at);
                CREATE INDEX IF NOT EXISTS idx_events_camera ON events(camera_id);
                CREATE INDEX IF NOT EXISTS idx_events_ack ON events(acknowledged);
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'viewer',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    last_login TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
                '''
            )
            conn.commit()
            conn.close()

    def start_event(self, camera_id: str, camera_name: str, snapshot_path: str = '') -> int:
        with self.lock:
            conn = self._conn()
            cur = conn.execute(
                'INSERT INTO events(camera_id,camera_name,started_at,snapshot_path) VALUES(?,?,?,?)',
                (camera_id, camera_name, datetime.now().isoformat(timespec='seconds'), snapshot_path),
            )
            conn.commit(); event_id = int(cur.lastrowid); conn.close(); return event_id

    def end_event(self, event_id: int) -> None:
        with self.lock:
            conn = self._conn(); conn.execute('UPDATE events SET ended_at=? WHERE id=?', (datetime.now().isoformat(timespec='seconds'), event_id)); conn.commit(); conn.close()

    def acknowledge(self, event_id: int) -> None:
        with self.lock:
            conn = self._conn(); conn.execute('UPDATE events SET acknowledged=1 WHERE id=?', (event_id,)); conn.commit(); conn.close()

    def list_day(self, day: str, camera_id: str = '') -> list[dict[str, Any]]:
        start = f'{day}T00:00:00'; end = f'{day}T23:59:59'
        with self.lock:
            conn = self._conn()
            if camera_id:
                cur = conn.execute('SELECT * FROM events WHERE started_at BETWEEN ? AND ? AND camera_id=? ORDER BY started_at DESC', (start, end, camera_id))
            else:
                cur = conn.execute('SELECT * FROM events WHERE started_at BETWEEN ? AND ? ORDER BY started_at DESC', (start, end))
            rows = [dict(row) for row in cur.fetchall()]; conn.close(); return rows

    def ensure_legacy_admin(self, password_hash: str) -> None:
        if not password_hash: return
        with self.lock:
            conn = self._conn(); row = conn.execute('SELECT COUNT(*) AS c FROM users').fetchone()
            if int(row['c']) == 0:
                conn.execute('INSERT INTO users(username,password_hash,role,enabled,created_at) VALUES(?,?,?,?,?)', ('admin', password_hash, 'admin', 1, datetime.now().isoformat(timespec='seconds')))
                conn.commit()
            conn.close()

    def user_count(self) -> int:
        with self.lock:
            conn = self._conn(); n = int(conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]); conn.close(); return n

    def create_user(self, username: str, password_hash: str, role: str = 'viewer') -> int:
        role = role if role in ('admin', 'operator', 'viewer') else 'viewer'; username = username.strip()
        if not username or len(username) < 3 or len(username) > 64: raise ValueError('Username must be 3-64 characters')
        with self.lock:
            conn = self._conn()
            try:
                cur = conn.execute('INSERT INTO users(username,password_hash,role,enabled,created_at) VALUES(?,?,?,?,?)', (username, password_hash, role, 1, datetime.now().isoformat(timespec='seconds')))
                conn.commit(); return int(cur.lastrowid)
            except sqlite3.IntegrityError as exc:
                raise ValueError('Username already exists') from exc
            finally: conn.close()

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        with self.lock:
            conn = self._conn(); row = conn.execute('SELECT * FROM users WHERE username=?', (username.strip(),)).fetchone(); conn.close(); return dict(row) if row else None

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        with self.lock:
            conn = self._conn(); row = conn.execute('SELECT * FROM users WHERE id=?', (int(user_id),)).fetchone(); conn.close(); return dict(row) if row else None

    def list_users(self) -> list[dict[str, Any]]:
        with self.lock:
            conn = self._conn(); rows = [dict(row) for row in conn.execute('SELECT id,username,role,enabled,created_at,last_login FROM users ORDER BY username').fetchall()]; conn.close(); return rows

    def update_user(self, user_id: int, *, role: str | None = None, enabled: bool | None = None, password_hash: str | None = None) -> None:
        parts=[]; values=[]
        if role is not None: parts.append('role=?'); values.append(role if role in ('admin','operator','viewer') else 'viewer')
        if enabled is not None: parts.append('enabled=?'); values.append(1 if enabled else 0)
        if password_hash is not None: parts.append('password_hash=?'); values.append(password_hash)
        if not parts: return
        values.append(int(user_id))
        with self.lock:
            conn=self._conn(); conn.execute(f"UPDATE users SET {', '.join(parts)} WHERE id=?", tuple(values)); conn.commit(); conn.close()

    def delete_user(self, user_id: int) -> None:
        with self.lock:
            conn=self._conn(); conn.execute('DELETE FROM users WHERE id=?', (int(user_id),)); conn.commit(); conn.close()

    def mark_login(self, user_id: int) -> None:
        with self.lock:
            conn=self._conn(); conn.execute('UPDATE users SET last_login=? WHERE id=?', (datetime.now().isoformat(timespec='seconds'), int(user_id))); conn.commit(); conn.close()
