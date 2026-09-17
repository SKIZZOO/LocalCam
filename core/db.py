from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


class EventStore:
    """Small SQLite event store used by the LocalCam web UI."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
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
            conn.commit()
            event_id = int(cur.lastrowid)
            conn.close()
            return event_id

    def end_event(self, event_id: int) -> None:
        with self.lock:
            conn = self._conn()
            conn.execute(
                'UPDATE events SET ended_at=? WHERE id=?',
                (datetime.now().isoformat(timespec='seconds'), event_id),
            )
            conn.commit()
            conn.close()

    def acknowledge(self, event_id: int) -> None:
        with self.lock:
            conn = self._conn()
            conn.execute('UPDATE events SET acknowledged=1 WHERE id=?', (event_id,))
            conn.commit()
            conn.close()

    def list_day(self, day: str, camera_id: str = '') -> list[dict[str, Any]]:
        start = f'{day}T00:00:00'
        end = f'{day}T23:59:59'
        with self.lock:
            conn = self._conn()
            if camera_id:
                cur = conn.execute(
                    'SELECT * FROM events WHERE started_at BETWEEN ? AND ? AND camera_id=? ORDER BY started_at DESC',
                    (start, end, camera_id),
                )
            else:
                cur = conn.execute(
                    'SELECT * FROM events WHERE started_at BETWEEN ? AND ? ORDER BY started_at DESC',
                    (start, end),
                )
            rows = [dict(row) for row in cur.fetchall()]
            conn.close()
            return rows
