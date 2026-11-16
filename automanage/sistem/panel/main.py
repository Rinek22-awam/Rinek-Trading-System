"""Main menu: one place to start the bot / reader and reach every tool and setting."""
import asyncio
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime

from copier import envfile

from copier import accounts, sopfiles
from copier.config import load_config
from copier.journal import Journal, backup_path
from copier.single import BOT_PORT, BYBIT_BOT_PORT, READER_PORT, SUPERVISOR_PORT, claim, is_running
from reader import sources

from . import sources_menu
from .settings import UNIVERSAL_SECTIONS, UNIVERSAL_SECTIONS_BYBIT, Settings
from .ui import ask, choose, clear, pause, title

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUS = {"received": "diterima", "rejected": "ditolak", "awaiting_confirm": "menunggu konfirmasi",
          "declined": "kamu tolak", "pending": "limit terpasang", "running": "berjalan",
          "done": "selesai", "cancelled": "dibatalkan", "expired": "kedaluwarsa"}


def bot_on():
    return is_running(BOT_PORT)


def reader_on():
    return is_running(READER_PORT)


def launch(window_title: str, module: str, *, environment=None):
    """Start a program in its own window; the window stays open after it stops."""
    cmd = (f'start "{window_title}" cmd /c "python -m {module} & echo. & '
           f'echo {window_title} berhenti. Tutup jendela ini. & pause >nul"')
    subprocess.Popen(cmd, shell=True, cwd=ROOT, env=environment)


def here(*args):
    """Run a tool in this window and wait for it."""
    subprocess.call([sys.executable, *args], cwd=ROOT)


def open_dashboard(environment):
    try:
        import tkinter  # Check before launching in the background.
        subprocess.Popen(
            [sys.executable, "-m", "panel.dashboard"], cwd=ROOT, env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            start_new_session=os.name != "nt",
        )
    except (ImportError, OSError) as exc:
        print(f"\n  [X] Dashboard tidak bisa dibuka: {exc}")
        pause()


def header():
    clear()
    title("BOT EKSEKUTOR SINYAL - MENU")
    cfg = load_config(os.path.join(ROOT, "config.yaml"))
    try:
        items = sources.load(os.path.join(ROOT, sources.PATH))
        src = sources_menu.summary(items)
    except Exception:
        src = "daftar sumber rusak, cek Sumber sinyal"
    try:
        demo_bybit = load_config(os.path.join(ROOT, "config-bybit.yaml"))["akun"]["hanya_demo"]
    except Exception:
        demo_bybit = True
    demo = lambda on: "hanya demo" if on else "REAL boleh"
    print(f"  Pembaca   : {'● jalan' if reader_on() else '○ mati'}")
    print(f"  Bot MT5   : {'● jalan' if bot_on() else '○ mati'}")
    print(f"  Bot Bybit : {'● jalan' if bybit_on() else '○ mati'}")
    print(f"  Pengawas  : {'● jalan' if supervisor_on() else '○ mati'}")
    print(f"  Sumber    : {src}")
    print(f"  Akun      : MT5 {demo(cfg['akun']['hanya_demo'])} · Bybit {demo(demo_bybit)}\n")
    return cfg


# --- Jalankan -----------------------------------------------------------------
def bybit_on():
    return is_running(BYBIT_BOT_PORT)


def supervisor_on():
    return is_running(SUPERVISOR_PORT)


def _missing_env(*keys):
    return [k for k in keys if not os.getenv(k, "").strip()]


def _can_start_reader(cfg) -> str:
    try:
        items = [s for s in sources.load(os.path.join(ROOT, sources.PATH)) if s.kode]
    except Exception as e:
        return f"daftar sumber rusak: {e}"
    if not items:
        return "belum ada sumber sinyal. Tambah dulu: Sumber sinyal"
    miss = _missing_env("TG_API_ID", "TG_API_HASH", "TG_INBOX_CHANNEL_ID")
    return f"isi dulu di .env: {', '.join(miss)}" if miss else ""


def _can_start_mt5(cfg) -> str:
    miss = _missing_env("TG_BOT_TOKEN", "TG_INBOX_CHANNEL_ID", "TG_ADMIN_USER_ID", "TG_API_ID", "TG_API_HASH")
    return f"isi dulu di .env: {', '.join(miss)}" if miss else ""


def _can_start_bybit(cfg) -> str:
    miss = _missing_env("BYBIT_TG_BOT_TOKEN", "TG_INBOX_CHANNEL_ID", "TG_ADMIN_USER_ID", "TG_API_ID", "TG_API_HASH")
    if miss:
        return f"isi dulu di .env: {', '.join(miss)}"
    if not accounts.load():
        return "belum ada akun Bybit: Setelan universal > Bybit > Akun Bybit"
    return ""


def _can_start_supervisor(cfg) -> str:
    miss = _missing_env("SUPERVISOR_TG_BOT_TOKEN", "TG_ADMIN_USER_ID", "TG_API_ID", "TG_API_HASH")
    return f"isi dulu lewat Utilitas atau .env: {', '.join(miss)}" if miss else ""


# (nama di menu, judul jendela, modul, sedang jalan?, cek sebelum nyala, pesan setelah nyala)
PROGRAMS = [
    ("Pembaca sinyal", "Pembaca Sinyal", "reader.main", reader_on, _can_start_reader,
     "Pembaca menonton daftar sumber dan mengirim SETUP berlabel ke kotak masuk."),
    ("Bot MT5 (Exness)", "Bot Eksekutor MT5", "copier.main", bot_on, _can_start_mt5,
     "Pastikan terminal akun tujuan sudah login dan Algo Trading aktif."),
    ("Bot Bybit", "Bot Eksekutor Bybit", "copier.main_bybit", bybit_on, _can_start_bybit,
     "Bot Bybit memakai SOP tiap sumber dan koneksi API masing-masing akun tujuan."),
    ("Bot Pengawas", "Bot Pengawas", "copier.supervisor", supervisor_on, _can_start_supervisor,
     "Kirim /status di chat pribadi bot Pengawas untuk cek koneksi."),
]


def start_menu(environment=None):
    while True:
        cfg = header()
        print("  NYALAKAN  (tiap program membuka jendela sendiri; tutup jendelanya untuk mematikan)\n")
        pick = choose([f"{name:<20} {'● jalan' if on() else '○ mati'}"
                       for name, _, _, on, _, _ in PROGRAMS], back="Kembali")
        if pick is None:
            return
        name, window, module, on, check, note = PROGRAMS[pick]
        if on():
            print(f"\n  {name} sudah jalan. Untuk mematikan, tutup jendelanya.")
        else:
            why = check(cfg)
            if why:
                print(f"\n  [X] {name} belum bisa dinyalakan: {why}")
            else:
                launch(window, module, environment=environment if module == "copier.supervisor" else None)
                print(f"\n  Membuka jendela {name}. Biarkan jendelanya terbuka.")
                print(f"  {note}")
        pause()


# --- Utilitas -----------------------------------------------------------------
def try_image():
    print("\n  Seret file gambar sinyal ke jendela ini, lalu tekan Enter.")
    path = ask("  File: ").strip().strip('"')
    if not path:
        return
    if not os.path.isfile(path):
        print(f"  [X] File tidak ditemukan: {path}")
    else:
        items = [s for s in sources.load(os.path.join(ROOT, sources.PATH)) if s.olah == "claude"]
        code = ""
        if len(items) > 1:
            print("\n  Pakai SOP (model Claude) sumber mana?\n")
            i = choose([f"{s.kode} {s.nama}" for s in items], back="Sumber pertama")
            code = items[i].kode if i is not None else ""
        text = ask("  Teks pesan yang ikut gambar (boleh kosong): ")
        print("\n  Membaca dengan Claude, tunggu 30-90 detik...\n")
        here("-m", "reader.main", "--coba-gambar", path, "--teks", text, "--sumber", code)
    pause()


def bybit_manual():
    print("\n  Menu tes manual Bybit: tiap order ditanya dulu, mode live ditolak.\n")
    here("-m", "panel.bybit_tes")
    pause()


def readiness(cfg):
    from .check import run_all
    print("  Mengecek, tunggu sebentar...\n")
    run_all(cfg)
    pause()


def history(cfg):
    db = os.path.join(ROOT, cfg["bot"]["jurnal"])
    if not os.path.exists(db):
        print("\n  Jurnal masih kosong.")
        pause()
        return
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute("SELECT id, created, symbol, side, status, pnl, reason FROM setups "
                       "ORDER BY id DESC LIMIT 15").fetchall()
    print("\n  15 setup terakhir:\n")
    for sid, created, sym, side, status, pnl, reason in rows:
        when = datetime.fromtimestamp(created).strftime("%d/%m %H:%M")
        money = f"{pnl:+.2f}" if pnl is not None else ""
        why = f"  ({reason[:45]})" if reason else ""
        print(f"  #{sid:<4} {when}  {sym:<7} {side:<4} {STATUS.get(status, status):<20} {money}{why}")
    pause()


def reset_journal(cfg, *, bybit=False):
    if bybit:
        return _reset_journal(cfg)
    lock = claim(BOT_PORT)
    if lock is None:
        print("\n  Bot MT5 sedang jalan. Hentikan dulu bot, lalu coba lagi.")
        pause()
        return
    with lock:
        _reset_journal(cfg)


def _reset_journal(cfg):
    db = os.path.join(ROOT, cfg["bot"]["jurnal"])
    if not os.path.exists(db):
        print("\n  Jurnal masih kosong.")
        pause()
        return
    journal = Journal(db)
    try:
        backup = backup_path(db)
        print("\n  Reset jurnal menghapus riwayat dan mengulang nomor setup dari #1.")
        if ask("\n  Ketik RESET untuk lanjut: ") == "RESET":
            total = journal.reset(backup)
            print(f"\n  Jurnal sudah kosong. {total} setup dihapus.")
            print(f"  Cadangan: {backup}")
        else:
            print("  Dibatalkan, jurnal tidak diubah.")
    finally:
        journal.db.close()
    pause()


def reset_bybit_journal():
    lock = claim(BYBIT_BOT_PORT)
    if lock is None:
        print("\n  Bot Bybit sedang jalan. Tutup dulu jendelanya sebelum reset jurnal.")
        pause()
        return
    with lock:
        reset_journal(load_config(os.path.join(ROOT, "config-bybit.yaml")), bybit=True)


def change_bot_token():
    bots = [("MT5", "TG_BOT_TOKEN"), ("Bybit", "BYBIT_TG_BOT_TOKEN"),
            ("Pengawas", "SUPERVISOR_TG_BOT_TOKEN")]
    print("\n  Pilih bot yang tokennya mau diganti.\n")
    pick = choose([name for name, _ in bots], back="Batal")
    if pick is None:
        return
    name, key = bots[pick]
    try:
        token = ask("  Token dari BotFather (Enter = batal): ")
    except KeyboardInterrupt:
        print()
        return
    if not token:
        return
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        print("\n  [X] Format token Telegram salah. Salin token lengkap dari BotFather.")
    else:
        try:
            envfile.save_setting(key, token)
        except OSError:
            print("\n  [X] Tidak bisa menyimpan token ke .env.")
        else:
            print(f"\n  [OK] Token bot {name} disimpan. Jika bot sedang jalan, nyalakan ulang bot {name}.")
    pause()


def change_inbox():
    from telethon.errors import RPCError

    from reader.main import telegram_client
    from reader.wizard import choose_inbox

    missing = _missing_env("TG_API_ID", "TG_API_HASH")
    if missing:
        print(f"\n  [X] Isi dulu di .env: {', '.join(missing)}")
        pause()
        return

    async def pick():
        async with telegram_client() as client:
            return await choose_inbox(client, ask)

    lock = claim(READER_PORT)
    if lock is None:
        print("\n  Sesi Telegram sedang dipakai. Tutup pembaca dulu, lalu coba lagi.")
        pause()
        return
    print("\n  Pilih channel kotak masuk untuk pembaca, bot MT5, dan bot Bybit.\n")
    try:
        with lock:
            dialog = asyncio.run(pick())
    except KeyboardInterrupt:
        print("\n  Dibatalkan.")
        return
    except (EOFError, OSError, ValueError, RPCError):
        print("\n  [X] Daftar channel gagal dimuat. Cek koneksi dan login Telegram pembaca.")
        pause()
        return
    if dialog is not None:
        print(f"\n  Channel terpilih: {dialog.name}")
        print("  Pastikan bot eksekutor yang dipakai sudah menjadi anggota channel ini.")
        if ask("  Simpan? (y/n): ").lower() in ("y", "ya", "yes"):
            try:
                envfile.save_setting("TG_INBOX_CHANNEL_ID", str(dialog.id))
            except OSError:
                print("\n  [X] Tidak bisa menyimpan channel kotak masuk ke .env.")
            else:
                print("\n  [OK] Kotak masuk disimpan. Nyalakan ulang pembaca dan eksekutor yang sedang jalan.")
    pause()


def run_tests():
    here("-m", "pytest", "-q")
    pause()


def sources_screen(cfg):
    sources_menu.screen(cfg, reader_on, here, ROOT)


def universal_menu():
    from . import mt5_accounts

    mt5 = Settings(os.path.join(ROOT, "config.yaml"), bot_running=bot_on, sections=UNIVERSAL_SECTIONS,
                   heading="SETELAN UNIVERSAL MT5", symbols=False)
    mt5.extra = [("Akun MT5", lambda: mt5_accounts.screen(mt5))]
    bybit = Settings(os.path.join(ROOT, "config-bybit.yaml"), bot_running=bybit_on, bybit=True,
                     sections=UNIVERSAL_SECTIONS_BYBIT, heading="SETELAN UNIVERSAL BYBIT", symbols=False,
                     extra=[("Akun Bybit (API key per akun)", lambda: sources_menu.accounts_screen(ROOT))])
    while True:
        header()
        print("  SETELAN UNIVERSAL  (berlaku untuk semua sumber; SOP ada per sumber)\n")
        pick = choose(["MT5 (Exness): akun demo, nama pair, magic",
                       "Bybit: akun demo, batas posisi, akun & API key (koin dicari sendiri)"],
                      back="Kembali")
        if pick is None:
            return
        [mt5, bybit][pick].run()


def tools_menu():
    from .reset_bot import screen as reset_bot_screen

    while True:
        cfg = header()
        print("  UTILITAS\n")
        pick = choose(["Coba baca gambar sinyal (tanpa Telegram)",
                       "Cek kesiapan (MT5, Bybit, Telegram, Claude)",
                       "Tes manual Bybit (coba order satu per satu, demo saja)",
                       "Riwayat setup",
                       "Reset jurnal MT5 (hapus riwayat, ada cadangan)",
                       "Reset jurnal Bybit (mulai dari #1, ada cadangan)",
                       "Reset bot (MT5 / Bybit / Reader)",
                       "Isi/ganti token bot Telegram (MT5 / Bybit / Pengawas)",
                       "Ganti channel kotak masuk sinyal",
                       "Tes logika program"], back="Kembali")
        if pick is None:
            return
        [try_image, lambda: readiness(cfg), bybit_manual, lambda: history(cfg), lambda: reset_journal(cfg),
         reset_bybit_journal, reset_bot_screen, change_bot_token, change_inbox, run_tests][pick]()


def main():
    for stream in (sys.stdout, sys.stderr):   # symbols like ● on any Windows console
        stream.reconfigure(encoding="utf-8", errors="replace")
    os.chdir(ROOT)
    environment = os.environ.copy()
    envfile.load()
    changes = sopfiles.migrate()
    if changes:
        clear()
        title("SETELAN DIPINDAH KE SUSUNAN BARU")
        print("  SOP sekarang per sumber. Yang diubah otomatis:\n")
        for line in changes:
            print(f"  - {line}")
        pause()
    while True:
        cfg = header()
        pick = choose(["Nyalakan (pembaca, bot MT5, bot Bybit, Pengawas)",
                       "Buka Dashboard",
                       "Sumber sinyal (tambah, hapus, SOP per sumber)",
                       "Setelan universal (MT5 / Bybit)",
                       "Utilitas"], back="Keluar")
        if pick is None:
            return
        [lambda: start_menu(environment), lambda: open_dashboard(environment), lambda: sources_screen(cfg),
         universal_menu, tools_menu][pick]()


if __name__ == "__main__":
    main()
