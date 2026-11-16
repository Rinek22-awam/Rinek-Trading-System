"""Full local resets available from the terminal utilities menu."""
from contextlib import closing
from pathlib import Path
from secrets import randbits
import sqlite3
from tempfile import TemporaryDirectory

from yaml import YAMLError

from copier.config import load_config
from copier.journal import Journal
from copier.single import BOT_PORT, BYBIT_BOT_PORT, READER_PORT, claim
from reader.memory import PATH as READER_MEMORY

from .ui import ask, choose, clear, pause, title


BOTS = (
    ("MT5", BOT_PORT, "config.yaml", ("bot.log", "desktop-bot.log")),
    ("Bybit", BYBIT_BOT_PORT, "config-bybit.yaml", ("bot-bybit.log",)),
    ("Reader", READER_PORT, None, ("pembaca.log", "desktop-reader.log")),
)


def matching_files(path, prefix, suffix=""):
    if path.parent.exists():
        return sorted(item for item in path.parent.iterdir()
                      if item.name.startswith(prefix) and item.name.endswith(suffix)
                      and (item.is_file() or item.is_symlink()))
    return []


def sqlite_files(path):
    return [path.with_name(path.name + suffix) for suffix in ("-journal", "-wal", "-shm")]


def fresh_journal(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path.parent) as directory:
        replacement = Path(directory) / path.name
        with closing(Journal(str(replacement)).db) as db:
            # Old Telegram buttons must not confirm a new setup that reuses the same ID.
            with db:
                db.execute("INSERT INTO kv (key, value) VALUES (?, ?)",
                           ("button_generation", str(randbits(48) + 1)))
        path.unlink(missing_ok=True)
        for sidecar in sqlite_files(path):
            sidecar.unlink(missing_ok=True)
        replacement.replace(path)


def journal_path(config):
    return Path(load_config(config)["bot"]["jurnal"])


def verify_journal(path):
    if not path.exists():
        return
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            tables = {name for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    except sqlite3.Error as error:
        raise ValueError(f"File {path} tidak bisa diverifikasi sebagai jurnal bot.") from error
    if not {"setups", "legs", "events"}.issubset(tables):
        raise ValueError(f"File {path} bukan jurnal bot.")


def reset_bot(bot):
    name, port, config, logs = bot
    lock = claim(port)
    if lock is None:
        raise RuntimeError(f"Hentikan bot {name} dulu sebelum reset.")
    with lock:
        if config:
            path = journal_path(config)
            other_config = next(entry[2] for entry in BOTS if entry[2] and entry[2] != config)
            if path.resolve() in (journal_path(other_config).resolve(), Path(READER_MEMORY).resolve()):
                raise ValueError("Lokasi jurnal dipakai bersama bot lain. Periksa konfigurasi jurnal.")
            verify_journal(path)
            endings = (".db", ".db-journal", ".db-wal", ".db-shm")
            for backup in matching_files(path, path.stem + "-cadangan-", endings):
                backup.unlink(missing_ok=True)
            fresh_journal(path)
        else:
            path = Path(READER_MEMORY)
            if any(path.resolve() == journal_path(entry[2]).resolve() for entry in BOTS if entry[2]):
                raise ValueError("Lokasi ingatan Reader dipakai sebagai jurnal bot lain.")
            path.unlink(missing_ok=True)
            for sidecar in sqlite_files(path):
                sidecar.unlink(missing_ok=True)

        for filename in logs:
            log = Path("logs") / filename
            log.unlink(missing_ok=True)
            for rotated in matching_files(log, filename + "."):
                rotated.unlink(missing_ok=True)


def screen():
    while True:
        clear()
        title("RESET BOT")
        print("  MT5 / Bybit: hapus log, jurnal setup, dan cadangan jurnal bot yang dipilih.")
        print("  Reader: hapus log dan seluruh ingatan Claude CLI, termasuk catatan gaya channel.")
        print("  Log desktop lama untuk bot yang dipilih ikut dihapus. Reset tanpa cadangan.")
        print("  Akun, konfigurasi, SOP, sumber sinyal, dan data Telegram tetap tersimpan.")
        print("  Order dan posisi di broker tetap berjalan. Bot kehilangan riwayat setup lamanya.\n")
        pick = choose([f"Reset bot {bot[0]}" for bot in BOTS])
        if pick is None:
            return
        bot = BOTS[pick]
        if ask(f"\n  Ketik RESET untuk menghapus data lokal bot {bot[0]}: ") != "RESET":
            print("  Reset dibatalkan.")
        else:
            try:
                reset_bot(bot)
            except (OSError, RuntimeError, ValueError, sqlite3.Error, YAMLError) as error:
                print(f"  [X] Reset belum selesai: {error}")
            else:
                print(f"  [OK] Reset bot {bot[0]} selesai. Bot bisa dinyalakan kembali.")
        pause()
