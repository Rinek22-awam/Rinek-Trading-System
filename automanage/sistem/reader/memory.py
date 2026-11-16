"""Ingatan pembaca per channel (chat + topik), disimpan di data/ingatan.db.

Claude dipanggil tanpa ingatan sendiri (setiap panggilan mulai dari nol). Supaya hemat
token, yang dikirim ke Claude bukan seluruh riwayat chat, tapi ringkasan pendek dari sini:

- konteks: setup yang pernah dilihat dari channel itu (terbuka / selesai), dengan pesan
  sumbernya dan nomor postingan SETUP-nya di kotak masuk (untuk dicocokkan ke jurnal bot);
- pesan: beberapa pesan terakhir channel itu, dipotong pendek, plus apa hasilnya;
- gaya: catatan singkat yang ditulis Claude sendiri tentang cara channel itu menulis sinyal.

Konteks ditutup oleh Claude (SL kena, close, cancel), oleh jurnal bot (setup selesai,
ditolak, kedaluwarsa), atau karena umurnya lewat batas.
"""
import os
import sqlite3
import time
from pathlib import Path
from typing import Iterable, List, Optional

PATH = "data/ingatan.db"
KEEP_MESSAGES = 200        # pesan yang disimpan per channel
MAX_AGE_DAYS = 7           # konteks terbuka lebih tua dari ini ditutup sendiri
PROFILE_MAX = 600          # panjang maksimal catatan gaya channel
NOTE_MAX = 200             # panjang maksimal catatan per konteks
JOURNAL_FINAL = {"rejected": "ditolak bot", "declined": "ditolak user",
                 "done": "selesai", "cancelled": "dibatalkan", "expired": "kedaluwarsa"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS konteks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat INTEGER, topic INTEGER, created REAL, updated REAL,
    status TEXT DEFAULT 'open', reason TEXT DEFAULT '',
    summary TEXT, symbol TEXT DEFAULT '', dest TEXT DEFAULT '',
    note TEXT DEFAULT '', inbox_msg INTEGER
);
CREATE TABLE IF NOT EXISTS konteks_pesan (
    konteks_id INTEGER, chat INTEGER, msg_id INTEGER,
    PRIMARY KEY (chat, msg_id, konteks_id)
);
CREATE TABLE IF NOT EXISTS pesan (
    chat INTEGER, topic INTEGER, msg_id INTEGER, time REAL, reply_to INTEGER,
    text TEXT, has_image INTEGER, result TEXT DEFAULT '',
    PRIMARY KEY (chat, msg_id)
);
CREATE TABLE IF NOT EXISTS gaya (
    chat INTEGER, topic INTEGER, text TEXT, updated REAL,
    PRIMARY KEY (chat, topic)
);
"""


def label(context_id: int) -> str:
    """Nama konteks yang dilihat Claude: K12."""
    return f"K{context_id}"


def parse_label(text) -> Optional[int]:
    s = str(text or "").strip().upper().lstrip("K#")
    return int(s) if s.isdigit() else None


class Memory:
    def __init__(self, path: str = PATH, clock=time.time):
        if path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)
        self.clock = clock

    # --- konteks ------------------------------------------------------------
    def new_context(self, chat: int, topic: int, summary: str, symbol: str = "", dest: str = "",
                    inbox_msg: Optional[int] = None, msg_ids: Iterable[int] = (),
                    note: str = "", status: str = "open", reason: str = "") -> int:
        now = self.clock()
        cur = self.db.execute(
            "INSERT INTO konteks (chat, topic, created, updated, status, reason, summary, symbol, "
            "dest, note, inbox_msg) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (chat, topic, now, now, status, reason, summary, symbol, dest, note[:NOTE_MAX], inbox_msg))
        cid = cur.lastrowid
        self.link(cid, chat, msg_ids)
        self.db.commit()
        return cid

    def link(self, context_id: int, chat: int, msg_ids: Iterable[int]):
        self.db.executemany("INSERT OR IGNORE INTO konteks_pesan (konteks_id, chat, msg_id) "
                            "VALUES (?, ?, ?)", [(context_id, chat, m) for m in msg_ids])
        self.db.commit()

    def get(self, context_id: Optional[int]) -> Optional[dict]:
        if context_id is None:
            return None
        r = self.db.execute("SELECT * FROM konteks WHERE id = ?", (context_id,)).fetchone()
        return dict(r) if r else None

    def context_for_message(self, chat: int, msg_id: int) -> Optional[dict]:
        """Konteks (terbuka atau selesai) yang memuat pesan ini; yang terbaru kalau lebih dari satu."""
        r = self.db.execute(
            "SELECT k.* FROM konteks k JOIN konteks_pesan p ON p.konteks_id = k.id "
            "WHERE p.chat = ? AND p.msg_id = ? ORDER BY k.id DESC LIMIT 1", (chat, msg_id)).fetchone()
        return dict(r) if r else None

    def open_contexts(self, chat: int, topic: int, limit: int = 12) -> List[dict]:
        rows = self.db.execute(
            "SELECT * FROM konteks WHERE chat = ? AND topic = ? AND status = 'open' "
            "ORDER BY id DESC LIMIT ?", (chat, topic, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def close(self, context_id: int, reason: str = ""):
        self.db.execute("UPDATE konteks SET status = 'closed', reason = ?, updated = ? "
                        "WHERE id = ? AND status = 'open'", (reason[:NOTE_MAX], self.clock(), context_id))
        self.db.commit()

    def set_note(self, context_id: int, note: str):
        self.db.execute("UPDATE konteks SET note = ?, updated = ? WHERE id = ?",
                        (note[:NOTE_MAX], self.clock(), context_id))
        self.db.commit()

    def expire(self, max_age_days: float = MAX_AGE_DAYS) -> List[int]:
        limit = self.clock() - max_age_days * 86400
        ids = [r[0] for r in self.db.execute(
            "SELECT id FROM konteks WHERE status = 'open' AND created < ?", (limit,))]
        for cid in ids:
            self.close(cid, f"otomatis, lebih dari {max_age_days:g} hari")
        return ids

    def sync_journals(self, inbox_chat: int, journal_paths: Iterable[str]) -> List[int]:
        """Tutup konteks yang setupnya di jurnal bot sudah selesai / ditolak / kedaluwarsa.
        Jurnal hanya dibaca (mode read-only), tidak pernah diubah."""
        rows = self.db.execute("SELECT id, inbox_msg FROM konteks WHERE status = 'open' "
                               "AND inbox_msg IS NOT NULL").fetchall()
        if not rows:
            return []
        closed = []
        for path in journal_paths:
            if not path or not os.path.exists(path):
                continue
            try:
                jdb = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
            except sqlite3.Error:
                continue
            try:
                for cid, inbox_msg in rows:
                    if cid in closed:
                        continue
                    try:
                        matches = jdb.execute("SELECT id, status FROM setups WHERE msg_chat = ? AND msg_id = ? "
                                              "ORDER BY id", (inbox_chat, inbox_msg)).fetchall()
                    except sqlite3.Error:   # jurnal belum punya tabel (bot belum pernah jalan)
                        break
                    if matches and all(r[1] in JOURNAL_FINAL for r in matches):
                        r = matches[0]
                        self.close(cid, f"setup #{r[0]} {JOURNAL_FINAL[r[1]]} (jurnal bot)")
                        closed.append(cid)
            finally:
                jdb.close()
        return closed

    # --- pesan --------------------------------------------------------------
    def add_message(self, chat: int, topic: int, msg_id: int, when: float, reply_to: Optional[int],
                    text: str, has_image: bool, result: str = ""):
        self.db.execute("INSERT OR REPLACE INTO pesan (chat, topic, msg_id, time, reply_to, text, "
                        "has_image, result) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (chat, topic, msg_id, when, reply_to, (text or "")[:500], int(bool(has_image)),
                         result[:200]))
        self.db.execute("DELETE FROM pesan WHERE chat = ? AND topic = ? AND msg_id NOT IN ("
                        "SELECT msg_id FROM pesan WHERE chat = ? AND topic = ? "
                        "ORDER BY msg_id DESC LIMIT ?)", (chat, topic, chat, topic, KEEP_MESSAGES))
        self.db.commit()

    def message(self, chat: int, msg_id: int) -> Optional[dict]:
        r = self.db.execute("SELECT * FROM pesan WHERE chat = ? AND msg_id = ?", (chat, msg_id)).fetchone()
        return dict(r) if r else None

    def recent(self, chat: int, topic: int, n: int = 10) -> List[dict]:
        rows = self.db.execute("SELECT * FROM pesan WHERE chat = ? AND topic = ? "
                               "ORDER BY msg_id DESC LIMIT ?", (chat, topic, n)).fetchall()
        return [dict(r) for r in reversed(rows)]

    # --- gaya channel -------------------------------------------------------
    def profile(self, chat: int, topic: int) -> str:
        r = self.db.execute("SELECT text FROM gaya WHERE chat = ? AND topic = ?", (chat, topic)).fetchone()
        return r[0] if r else ""

    def set_profile(self, chat: int, topic: int, text: str):
        self.db.execute("INSERT OR REPLACE INTO gaya (chat, topic, text, updated) VALUES (?, ?, ?, ?)",
                        (chat, topic, (text or "").strip()[:PROFILE_MAX], self.clock()))
        self.db.commit()
