"""Aksi yang dipakai bersama oleh MENU.bat (menu.py) dan Dashboard, supaya hasilnya selalu sama.

Tidak ada input() atau print() di sini: pemanggil yang bertanya ke user dan menampilkan hasil.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import psutil

from . import main as runtime, settings
from .settings import GeneratorError, redact
from .telegram import BotSender

NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ACTION_WAIT_SECONDS = 1   # tombol jurnal/Telegram/TradingView menunggu kunci sebentar sebelum menolak


# ---------- menjalankan perintah generator ----------

def python_exe(console=True):
    """python.exe untuk proses berjendela/berkeluaran, pythonw.exe untuk jendela tanpa konsol."""
    current = Path(sys.executable)
    name = "python.exe" if console else "pythonw.exe"
    candidate = current.with_name(name)
    return str(candidate) if os.name == "nt" and candidate.is_file() else str(current)


def command(*args):
    return [python_exe(), "-m", "generator.main", *args]


def launch(*args, window=False, capture=False):
    """Jalankan perintah generator sebagai proses terpisah. window=True: jendela konsol sendiri."""
    flags = NEW_CONSOLE if window else NO_WINDOW
    options = dict(cwd=settings.SYSTEM, creationflags=flags)
    if capture:
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        options.update(stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env)
    elif not window:
        options.update(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    return subprocess.Popen(command(*args), **options)


def open_dashboard():
    """Buka Dashboard Tkinter tanpa jendela konsol (pythonw)."""
    return subprocess.Popen([python_exe(console=False), "-m", "generator.dashboard"], cwd=settings.SYSTEM,
                            creationflags=NO_WINDOW, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


# ---------- analisa sekarang (dengan pesan opsional) ----------

def message_file():
    """File kosong baru di data/pesan untuk menampung pesan user."""
    from datetime import datetime
    from uuid import uuid4

    runtime.MESSAGE_DIR.mkdir(parents=True, exist_ok=True)
    path = runtime.MESSAGE_DIR / f"pesan-{datetime.now(settings.WIB):%Y%m%d-%H%M%S}-{uuid4().hex[:6]}.txt"
    path.write_text("", encoding="utf-8")
    return path


def save_message(text):
    """Simpan pesan dari Dashboard ke file sementara. Kosong = None (analisa tanpa pesan)."""
    text = (text or "").strip()
    if not text:
        return None
    if len(text) > runtime.MESSAGE_LIMIT:
        raise GeneratorError(f"Pesan terlalu panjang ({len(text):,} karakter, maksimal {runtime.MESSAGE_LIMIT:,}).")
    path = message_file()
    path.write_text(text, encoding="utf-8")
    return path


def message_from_notepad(editor=("notepad",)):
    """MENU.bat: buka Notepad berisi file kosong, tunggu ditutup, lalu baca. Kosong = None (file dihapus)."""
    path = message_file()
    subprocess.run([*editor, str(path)], check=False)
    text = runtime.read_message(path) if path.exists() else ""
    if not text:
        path.unlink(missing_ok=True)
        return None, 0
    return path, len(text)


def analysis_args(skill=None, message_path=None):
    """Argumen perintah `once` untuk skill terpilih (None = mode uji fungsi) dan file pesan (opsional)."""
    args = ["once"]
    if skill:
        args += ["--skill", skill]
    if message_path:
        args += ["--pesan-file", str(message_path)]
    return tuple(args)


# ---------- jurnal: hapus analisa dan reset ----------

LOCKED_EXCEL = "File Excel jurnal sedang dibuka. Tutup Excel dulu, lalu coba lagi."


def journal_change(work):
    """Jalankan perubahan jurnal hanya saat tidak ada analisa berjalan (dan analisa baru menunggu sampai selesai)."""
    from . import journal

    with runtime.wait_for_lock(runtime.WORK_PORT, ACTION_WAIT_SECONDS) as available:
        if not available:
            raise GeneratorError("Generator sedang menganalisa. Coba lagi setelah selesai.")
        try:
            return work()
        except journal.JournalLocked:
            raise GeneratorError(LOCKED_EXCEL) from None
        except journal.JournalError as exc:
            raise GeneratorError(str(exc)) from None


def delete_analyses(ids):
    """Hapus analisa terpilih dari jurnal. Mengembalikan daftar analisa yang dihapus."""
    from . import journal

    return journal_change(lambda: journal.delete_entries(ids))


def reset_journal():
    """Jurnal mulai dari nol; data lama dipindah ke jurnal/arsip/<tanggal-jam>/. Mengembalikan folder arsip."""
    from . import journal

    return journal_change(journal.reset)


# ---------- generator jalan terus ----------

def generator_running():
    with runtime.local_lock(runtime.DAEMON_PORT) as available:
        return not available


def analysis_running():
    with runtime.local_lock(runtime.WORK_PORT) as available:
        return not available


def start_generator(window=True, parent=None):
    """window=True (menu): jendela konsol sendiri. window=False (Dashboard): jalan di latar, log tampil di Dashboard.

    parent = nomor proses Dashboard: generator berhenti sendiri kalau proses itu sudah tidak ada.
    """
    if generator_running():
        raise GeneratorError("Generator sudah nyala.")
    extra = ("--induk", str(parent)) if parent else ()
    return launch("daemon", *extra, window=window)


def generator_process():
    """Proses generator jalan terus dari file PID, hanya kalau benar perintah generator daemon."""
    try:
        pid = int(runtime.PID_FILE.read_text(encoding="utf-8").strip())
        process = psutil.Process(pid)
        line = process.cmdline()
    except (OSError, ValueError, psutil.Error):
        return None
    return process if "generator.main" in line and "daemon" in line else None


def stop_generator():
    """Hentikan generator jalan terus (dan analisa yang sedang jalan di dalamnya)."""
    process = generator_process()
    if process is None:
        if generator_running():
            raise GeneratorError("Generator nyala tapi tidak bisa dihentikan dari sini. Tutup jendela generatornya.")
        return False
    try:
        children = process.children(recursive=True)
        for child in reversed(children):
            try:
                child.kill()
            except psutil.Error:
                pass
        process.kill()
        process.wait(timeout=10)
    except psutil.Error:
        raise GeneratorError("Generator tidak bisa dihentikan. Tutup jendela generatornya.") from None
    runtime.PID_FILE.unlink(missing_ok=True)
    return True


# ---------- Telegram ----------

def bot():
    return BotSender(settings.read_secrets()["GEN_TG_BOT_TOKEN"])


def save_token(token):
    token = token.strip()
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        raise GeneratorError("Bentuk token bot tidak valid. Salin token dari BotFather.")
    try:
        BotSender(token).call("getMe")
    except GeneratorError as exc:
        raise GeneratorError(redact(str(exc), {"token": token})) from None
    settings.save_secrets({"GEN_TG_BOT_TOKEN": token})


def api_changes(api_id, api_hash):
    """Cek API ID/hash. Mengembalikan perubahan yang perlu disimpan ({} kalau sama)."""
    api_id, api_hash = api_id.strip(), api_hash.strip()
    if api_id and (not re.fullmatch(r"[0-9]{1,10}", api_id) or not 0 < int(api_id) <= 2147483647):
        raise GeneratorError("API ID harus angka positif yang valid.")
    if api_hash and not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash):
        raise GeneratorError("API hash harus 32 karakter heksadesimal.")
    current = settings.read_secrets()
    updates = {key: value for key, value in (("GEN_TG_API_ID", api_id), ("GEN_TG_API_HASH", api_hash))
               if value and value != current[key]}
    if updates and any(not (updates.get(key) or current[key]) for key in ("GEN_TG_API_ID", "GEN_TG_API_HASH")):
        raise GeneratorError("Isi API ID dan API hash untuk akun Telegram.")
    return updates


def session_file():
    return settings.SYSTEM / "data" / "generator.session"


def save_api(updates, reset_session):
    """Simpan API. Kalau ada sesi lama dan reset_session=False, tidak disimpan (pemanggil sudah bertanya)."""
    with runtime.wait_for_lock(runtime.WORK_PORT, ACTION_WAIT_SECONDS) as available:
        if not available:
            raise GeneratorError("Generator sedang bekerja. Coba lagi setelah selesai.")
        session = session_file()
        if session.is_file():
            if not reset_session:
                return False
            for suffix in ("", "-journal", "-shm", "-wal"):
                session.with_name(session.name + suffix).unlink(missing_ok=True)
        settings.save_secrets(updates)
    return True


def check_inbox(chat_id):
    """(judul channel, bot bisa posting) untuk ID channel. Melempar error kalau bukan channel."""
    chat_id = str(chat_id).strip()
    if not re.fullmatch(r"-100[0-9]{1,16}", chat_id):
        raise GeneratorError("ID channel harus berbentuk -100 diikuti angka.")
    sender = bot()
    chat = sender.call("getChat", chat_id=int(chat_id))
    if chat.get("type") != "channel":
        raise GeneratorError("ID itu bukan channel.")
    bot_id = sender.call("getMe")["id"]
    try:
        member = sender.call("getChatMember", chat_id=int(chat_id), user_id=bot_id)
        can_post = member.get("status") == "creator" or (
            member.get("status") == "administrator" and member.get("can_post_messages") is True)
    except GeneratorError:
        can_post = False
    return chat.get("title") or chat_id, can_post


def save_inbox(chat_id):
    settings.update_yaml(settings.SYSTEM / "config.yaml", {("telegram", "kotak_masuk"): str(chat_id)})


def save_report(chat_id, sender_mode):
    """Simpan chat pribadi laporan. Mode bot: dicek dulu bahwa itu chat pribadi."""
    chat_id = str(chat_id).strip()
    if not re.fullmatch(r"[1-9][0-9]{0,18}", chat_id):
        raise GeneratorError("ID chat pribadi harus angka positif.")
    if sender_mode == "bot":
        chat = bot().call("getChat", chat_id=int(chat_id))
        if chat.get("type") != "private":
            raise GeneratorError("ID itu bukan chat pribadi.")
    settings.update_yaml(settings.SYSTEM / "config.yaml", {("telegram", "laporan_ke"): chat_id})


# ---------- TradingView ----------

def save_launcher(value):
    path = Path(str(value).strip().strip('"')).expanduser().resolve()
    if path.suffix.lower() not in (".bat", ".cmd") or not path.is_file():
        raise GeneratorError("Skrip .bat atau .cmd tidak ditemukan.")
    settings.update_yaml(settings.SYSTEM / "config.yaml", {("tradingview", "peluncur"): str(path)})
    return path


def save_tv_wait(value):
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        seconds = 0
    if not seconds > 0 or seconds > 3600:
        raise GeneratorError("Lama tunggu harus angka antara 1 dan 3600 detik.")
    settings.update_yaml(settings.SYSTEM / "config.yaml", {("tradingview", "tunggu_detik"): seconds})


def open_tradingview():
    base = settings.read_config()
    with runtime.wait_for_lock(runtime.WORK_PORT, ACTION_WAIT_SECONDS) as available:
        if not available:
            raise GeneratorError("Generator sedang bekerja. Coba lagi setelah selesai.")
        runtime.ensure_tradingview(base)
