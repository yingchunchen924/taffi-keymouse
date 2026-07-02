from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import APP_VERSION, DB_VERSION
from .models import Macro, ReferenceImage, RunRecord
from .paths import DB_PATH, ensure_dirs
from .utils import now_text, screen_size, safe_name


class Database:
    def __init__(self, path: Path = DB_PATH) -> None:
        ensure_dirs()
        self.path = path
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.migrate()

    def close(self) -> None:
        self.conn.close()

    def migrate(self) -> None:
        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS app_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS macros (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL DEFAULT '',
                    tags TEXT NOT NULL DEFAULT '',
                    favorite INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    screen_width INTEGER NOT NULL DEFAULT 0,
                    screen_height INTEGER NOT NULL DEFAULT 0,
                    event_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    macro_id INTEGER NOT NULL,
                    position INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    time_offset REAL NOT NULL DEFAULT 0,
                    payload TEXT NOT NULL,
                    FOREIGN KEY(macro_id) REFERENCES macros(id) ON DELETE CASCADE
                )
                """
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_macro_position ON events(macro_id, position)"
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reference_images (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    macro_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    width INTEGER NOT NULL DEFAULT 0,
                    height INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(macro_id, name),
                    FOREIGN KEY(macro_id) REFERENCES macros(id) ON DELETE CASCADE
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS run_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    macro_id INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    status TEXT NOT NULL,
                    loops INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(macro_id) REFERENCES macros(id) ON DELETE CASCADE
                )
                """
            )
            self.conn.execute("INSERT OR REPLACE INTO app_meta(key, value) VALUES('db_version', ?)", (str(DB_VERSION),))
            self.conn.execute("INSERT OR REPLACE INTO app_meta(key, value) VALUES('app_version', ?)", (APP_VERSION,))

    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except Exception:
            return row["value"]

    def set_setting(self, key: str, value: Any) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    def create_macro(self, name: str, description: str = "", tags: str = "") -> int:
        name = safe_name(name)
        w, h = screen_size()
        ts = now_text()
        with self.conn:
            cur = self.conn.execute(
                """
                INSERT INTO macros(name, description, tags, created_at, updated_at, screen_width, screen_height)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (name, description, tags, ts, ts, w, h),
            )
            return int(cur.lastrowid)

    def update_macro_meta(self, macro_id: int, name: str, description: str, tags: str, favorite: bool = False) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE macros
                SET name=?, description=?, tags=?, favorite=?, updated_at=?
                WHERE id=?
                """,
                (safe_name(name), description, tags, 1 if favorite else 0, now_text(), macro_id),
            )

    def delete_macro(self, macro_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM macros WHERE id=?", (macro_id,))

    def list_macros(self, query: str = "") -> List[Macro]:
        if query:
            like = f"%{query}%"
            rows = self.conn.execute(
                """
                SELECT * FROM macros
                WHERE name LIKE ? OR description LIKE ? OR tags LIKE ?
                ORDER BY favorite DESC, updated_at DESC
                """,
                (like, like, like),
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM macros ORDER BY favorite DESC, updated_at DESC").fetchall()
        return [self._macro_from_row(row) for row in rows]

    def get_macro(self, macro_id: int) -> Optional[Macro]:
        row = self.conn.execute("SELECT * FROM macros WHERE id=?", (macro_id,)).fetchone()
        return self._macro_from_row(row) if row else None

    def get_macro_by_name(self, name: str) -> Optional[Macro]:
        row = self.conn.execute("SELECT * FROM macros WHERE name=?", (safe_name(name),)).fetchone()
        return self._macro_from_row(row) if row else None

    def save_events(self, macro_id: int, events: List[Dict[str, Any]]) -> None:
        w, h = screen_size()
        with self.conn:
            self.conn.execute("DELETE FROM events WHERE macro_id=?", (macro_id,))
            for position, event in enumerate(events):
                self.conn.execute(
                    """
                    INSERT INTO events(macro_id, position, event_type, time_offset, payload)
                    VALUES(?, ?, ?, ?, ?)
                    """,
                    (
                        macro_id,
                        position,
                        str(event.get("type", "unknown")),
                        float(event.get("time", 0)),
                        json.dumps(event, ensure_ascii=False),
                    ),
                )
            self.conn.execute(
                "UPDATE macros SET event_count=?, updated_at=?, screen_width=?, screen_height=? WHERE id=?",
                (len(events), now_text(), w, h, macro_id),
            )

    def load_events(self, macro_id: int) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT payload FROM events WHERE macro_id=? ORDER BY position ASC",
            (macro_id,),
        ).fetchall()
        events: List[Dict[str, Any]] = []
        for row in rows:
            try:
                events.append(json.loads(row["payload"]))
            except Exception:
                pass
        return events

    def add_reference(self, macro_id: int, name: str, file_path: str, width: int, height: int) -> int:
        with self.conn:
            cur = self.conn.execute(
                """
                INSERT OR REPLACE INTO reference_images(macro_id, name, file_path, created_at, width, height)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (macro_id, name, file_path, now_text(), width, height),
            )
            return int(cur.lastrowid)

    def delete_reference(self, ref_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM reference_images WHERE id=?", (ref_id,))

    def list_references(self, macro_id: int) -> List[ReferenceImage]:
        rows = self.conn.execute(
            "SELECT * FROM reference_images WHERE macro_id=? ORDER BY created_at DESC",
            (macro_id,),
        ).fetchall()
        return [self._ref_from_row(row) for row in rows]

    def get_reference_by_name(self, macro_id: int, name: str) -> Optional[ReferenceImage]:
        row = self.conn.execute(
            "SELECT * FROM reference_images WHERE macro_id=? AND name=?",
            (macro_id, name),
        ).fetchone()
        return self._ref_from_row(row) if row else None

    def start_run(self, macro_id: int) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO run_records(macro_id, started_at, status) VALUES(?, ?, ?)",
                (macro_id, now_text(), "running"),
            )
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, loops: int, message: str = "") -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE run_records SET ended_at=?, status=?, loops=?, message=? WHERE id=?",
                (now_text(), status, loops, message, run_id),
            )

    def list_runs(self, macro_id: Optional[int] = None, limit: int = 50) -> List[RunRecord]:
        if macro_id:
            rows = self.conn.execute(
                "SELECT * FROM run_records WHERE macro_id=? ORDER BY id DESC LIMIT ?",
                (macro_id, limit),
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM run_records ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._run_from_row(row) for row in rows]

    def _macro_from_row(self, row: sqlite3.Row) -> Macro:
        return Macro(
            id=int(row["id"]),
            name=row["name"],
            description=row["description"],
            tags=row["tags"],
            favorite=bool(row["favorite"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            screen_width=int(row["screen_width"]),
            screen_height=int(row["screen_height"]),
            event_count=int(row["event_count"]),
        )

    def _ref_from_row(self, row: sqlite3.Row) -> ReferenceImage:
        return ReferenceImage(
            id=int(row["id"]),
            macro_id=int(row["macro_id"]),
            name=row["name"],
            file_path=row["file_path"],
            created_at=row["created_at"],
            width=int(row["width"]),
            height=int(row["height"]),
        )

    def _run_from_row(self, row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            id=int(row["id"]),
            macro_id=int(row["macro_id"]),
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            status=row["status"],
            loops=int(row["loops"]),
            message=row["message"],
        )
