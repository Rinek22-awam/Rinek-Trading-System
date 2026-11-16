"""Journal: the system's ledger in a local SQLite file. Survives restarts."""
import json
import os
import sqlite3
import time
from datetime import datetime
from typing import List, Optional

from .models import Setup

_SCHEMA = """
CREATE TABLE IF NOT EXISTS setups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created REAL, updated REAL, status TEXT, reason TEXT DEFAULT '',
    symbol TEXT, broker_symbol TEXT, side TEXT, entry REAL, sl REAL, tps TEXT,
    order_type TEXT, entry2 REAL, sl2 REAL, risk_mode TEXT, risk_value REAL,
    source TEXT, note TEXT, raw TEXT, kode TEXT DEFAULT '',
    msg_chat INTEGER, msg_id INTEGER, posted REAL, account TEXT NOT NULL DEFAULT '',
    confirm_msg_id INTEGER, confirm_deadline REAL, warning TEXT DEFAULT '',
    expires REAL, pending_cancel TEXT, watch_entry_price INTEGER, risk_amount REAL, total_lot REAL,
    peak REAL, tp_level INTEGER DEFAULT 0, pnl REAL,
    UNIQUE (msg_chat, msg_id, account)
);
CREATE TABLE IF NOT EXISTS legs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    setup_id INTEGER, idx INTEGER, ticket INTEGER, volume REAL, tp REAL,
    status TEXT, fill_price REAL, fill_time REAL, profit REAL, next_try REAL DEFAULT 0,
    close_time REAL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    setup_id INTEGER, time REAL, kind TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""


class Journal:
    def __init__(self, path: str = "data/jurnal.db", clock=time.time):
        if path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)
        self._add_columns()
        self._add_accounts()
        self.clock = clock

    def _add_accounts(self):
        columns = [row[1] for row in self.db.execute("PRAGMA table_info(setups)")]
        if "account" in columns:
            return
        names = ", ".join(columns)
        schema = _SCHEMA.split("CREATE TABLE IF NOT EXISTS legs", 1)[0]
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            sequence = self.db.execute("SELECT seq FROM sqlite_sequence WHERE name = 'setups'").fetchone()
            self.db.execute("ALTER TABLE setups RENAME TO setups_legacy")
            self.db.execute(schema)
            self.db.execute(f"INSERT INTO setups ({names}) SELECT {names} FROM setups_legacy")
            self.db.execute("DROP TABLE setups_legacy")
            if sequence:
                self.db.execute("DELETE FROM sqlite_sequence WHERE name = 'setups'")
                self.db.execute("INSERT INTO sqlite_sequence (name, seq) VALUES ('setups', ?)", sequence)

    def by_messages(self, chat, msg_id):
        return [_row(r) for r in self.db.execute(
            "SELECT * FROM setups WHERE msg_chat = ? AND msg_id = ? ORDER BY id", (chat, msg_id))]

    def _add_columns(self):
        """Tambah kolom yang belum ada di jurnal lama."""
        for table, col, kind in (("setups", "kode", "TEXT DEFAULT ''"),
                                 ("setups", "pending_cancel", "TEXT"), ("setups", "watch_entry_price", "INTEGER"),
                                 ("legs", "close_time", "REAL")):
            have = {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}
            if col not in have:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")
        self.db.commit()

    # --- setups -------------------------------------------------------------
    def add_setup(self, s: Setup, status: str, **meta) -> int:
        now = self.clock()
        row = dict(created=now, updated=now, status=status, symbol=s.symbol, side=s.side,
                   entry=s.entry, sl=s.sl, tps=json.dumps(s.tps), order_type=s.order_type,
                   entry2=s.entry2, sl2=s.sl2, risk_mode=s.risk_mode, risk_value=s.risk_value,
                   source=s.source, note=s.note, raw=s.raw, kode=s.code)
        row.update(meta)
        cols = ", ".join(row)
        cur = self.db.execute(f"INSERT INTO setups ({cols}) VALUES ({', '.join('?' * len(row))})",
                              list(row.values()))
        self.db.commit()
        return cur.lastrowid

    def get(self, setup_id: int) -> Optional[dict]:
        r = self.db.execute("SELECT * FROM setups WHERE id = ?", (setup_id,)).fetchone()
        return _row(r)

    def update(self, setup_id: int, **fields):
        with self.db:
            self._update_setup(setup_id, fields)

    def _update_setup(self, setup_id: int, fields: dict):
        fields["updated"] = self.clock()
        if "tps" in fields:
            fields["tps"] = json.dumps(fields["tps"])
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE setups SET {sets} WHERE id = ?", [*fields.values(), setup_id])

    def by_status(self, *statuses) -> List[dict]:
        q = f"SELECT * FROM setups WHERE status IN ({', '.join('?' * len(statuses))}) ORDER BY id"
        return [_row(r) for r in self.db.execute(q, statuses)]

    def since(self, t: float) -> List[dict]:
        return [_row(r) for r in self.db.execute(
            "SELECT * FROM setups WHERE created >= ? ORDER BY id", (t,))]

    def reset(self, backup_to: str) -> int:
        """Copy the journal to `backup_to`, then forget all setups, legs and events.
        Bot state (stop mode, Telegram offset) is kept. Numbering starts again at #1,
        with a new generation for Telegram buttons. Returns how many setups were removed."""
        dest = sqlite3.connect(backup_to)
        self.db.backup(dest)
        dest.close()
        with self.db:
            n = self.db.execute("SELECT COUNT(*) FROM setups").fetchone()[0]
            for table in ("setups", "legs", "events"):
                self.db.execute(f"DELETE FROM {table}")
            self.db.execute("DELETE FROM sqlite_sequence WHERE name IN ('setups', 'legs', 'events')")
            generation = self.get_kv("button_generation", 0) + 1
            self.db.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)",
                            ("button_generation", json.dumps(generation)))
        return n

    def by_message(self, chat: int, msg_id: int) -> Optional[dict]:
        """Setup yang berasal dari postingan ini di kotak masuk."""
        return _row(self.db.execute("SELECT * FROM setups WHERE msg_chat = ? AND msg_id = ?",
                                    (chat, msg_id)).fetchone())

    def message_seen(self, chat: int, msg_id: int) -> bool:
        return self.db.execute("SELECT 1 FROM setups WHERE msg_chat = ? AND msg_id = ?",
                               (chat, msg_id)).fetchone() is not None

    # --- legs ---------------------------------------------------------------
    def add_leg(self, setup_id: int, idx: int, ticket: int, volume: float, tp: float, status: str,
                *, setup_fields=None):
        with self.db:
            self.db.execute("INSERT INTO legs (setup_id, idx, ticket, volume, tp, status) "
                            "VALUES (?, ?, ?, ?, ?, ?)", (setup_id, idx, ticket, volume, tp, status))
            if setup_fields is not None:
                self._update_setup(setup_id, setup_fields)

    def legs(self, setup_id: int) -> List[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM legs WHERE setup_id = ? ORDER BY idx", (setup_id,))]

    def update_leg(self, leg_id: int, **fields):
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE legs SET {sets} WHERE id = ?", [*fields.values(), leg_id])
        self.db.commit()

    def all_tickets(self) -> set:
        return {r[0] for r in self.db.execute("SELECT ticket FROM legs")}

    # --- events and key/value -----------------------------------------------
    def event(self, setup_id: Optional[int], kind: str, detail: str = ""):
        self.db.execute("INSERT INTO events (setup_id, time, kind, detail) VALUES (?, ?, ?, ?)",
                        (setup_id, self.clock(), kind, detail))
        self.db.commit()

    def events(self, setup_id: int) -> List[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM events WHERE setup_id = ? ORDER BY id", (setup_id,))]

    def get_kv(self, key: str, default=None):
        r = self.db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def set_kv(self, key: str, value):
        self.db.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)",
                        (key, json.dumps(value)))
        self.db.commit()


def backup_path(db_path: str) -> str:
    """Keep backups distinguishable when multiple journals share a folder."""
    stem, _ = os.path.splitext(db_path)
    return f"{stem}-cadangan-{datetime.now():%Y%m%d-%H%M%S-%f}.db"


def _row(r) -> Optional[dict]:
    if r is None:
        return None
    d = dict(r)
    d["tps"] = json.loads(d["tps"] or "[]")
    return d
