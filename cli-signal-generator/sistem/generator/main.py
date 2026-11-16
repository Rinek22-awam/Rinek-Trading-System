import argparse
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from queue import SimpleQueue
from threading import Thread
from urllib.request import urlopen

from .claude import LOW_LABEL, MESSAGE_LIMIT, SETUP_LABEL, analyze, extract_setups, no_setup, no_setup_reason
from . import heartbeat, journal, skills
from .settings import (
    DATA, EFFORTS, LOGS, MODEL_NAME, SECRET_KEYS, SYSTEM, WIB, GeneratorError, list_skills, read_config, read_secrets,
    redact, set_report_chat, valid_inbox, valid_report,
)
from .telegram import AccountSender, BotSender, open_sender, send_with_retry

ROUTINE = "pesan_rutin"   # penanda pesan rutin di antrean generator
PID_FILE = DATA / "generator.pid"   # nomor proses generator jalan terus (untuk tombol Stop di Dashboard)
DAEMON_PORT = 47656
WORK_PORT = 47657
POLL_SECONDS = 1
TEST_MESSAGE = """SETUP
Kode: TES
Pair: XAUUSD
Arah: BUY
Entry: 1000
SL: 990
TP1: 1010
Ke: mt5
Sumber: cli_uji
Catatan: UJI GENERATOR dari menu"""


@contextmanager
def local_lock(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as lock:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            lock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            lock.bind(("127.0.0.1", port))
        except OSError as exc:
            if exc.errno not in (13, 98, 10013, 10048) and getattr(exc, "winerror", None) not in (10013, 10048):
                raise
            yield False
        else:
            yield True


@contextmanager
def wait_for_lock(port, seconds, sleep=time.sleep, clock=time.monotonic):
    """Seperti local_lock, tapi menunggu sebentar kalau kunci sedang dipegang.

    Dashboard dan menu mengecek status dengan memegang kunci sepersekian detik. Tanpa menunggu, analisa
    yang kebetulan datang di saat itu ikut tertolak.
    """
    deadline = clock() + seconds
    while True:
        with local_lock(port) as acquired:
            if acquired or clock() >= deadline:
                yield acquired
                return
        sleep(LOCK_RETRY_SECONDS)


LOCK_WAIT_SECONDS = 5      # lama analisa manual menunggu kunci sebelum dinyatakan dilewati
LOCK_RETRY_SECONDS = 0.25
SKIPPED = "dilewati"       # hasil once() kalau generator sedang menganalisa
EXIT_SKIPPED = 3           # kode keluar untuk SKIPPED, dibaca Dashboard dan menu


TELEGRAM_LIMIT = 4000   # batas aman satu pesan Telegram (maks 4096 karakter)


def check_destinations(config, with_report):
    if not valid_inbox(config):
        raise GeneratorError("Isi kotak_masuk dengan ID channel -100... di config.yaml.")
    if with_report and config.report and not valid_report(config):
        raise GeneratorError("laporan_ke harus ID chat pribadi (angka positif), atau kosongkan supaya dideteksi otomatis.")


def resolve_report(config, sender):
    """laporan_ke kosong -> mode bot: ambil ID dari orang yang menekan Start di bot, lalu simpan. Mode akun: Saved Messages."""
    if config.report:
        return config
    if not isinstance(sender, BotSender):
        return replace(config, report="me")
    chat_id = sender.find_private_chat()
    if chat_id is None:
        raise GeneratorError("ID chat pribadimu belum terdeteksi. Buka chat dengan bot generator, tekan Start "
                             "(atau kirim pesan apa saja), lalu coba lagi.")
    set_report_chat(chat_id)
    logging.info("ID chat pribadi terdeteksi otomatis dan disimpan ke config.yaml.")
    return replace(config, report=str(chat_id))


def split_message(text, limit=TELEGRAM_LIMIT):
    """Pecah teks panjang jadi beberapa pesan, di batas baris kalau bisa."""
    parts, current = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) > limit:
            parts.append(current)
            current = ""
        current += line
    if current.strip():
        parts.append(current)
    return [part.rstrip("\n") for part in parts if part.strip()]


def message_tag(config):
    """Cap untuk analisa yang diberi pesan user. Isi pesannya sendiri tidak ditampilkan di Telegram."""
    return " (dengan pesan)" if config.message else ""


def force_low(blocks, low):
    """Analisa dengan pesan: semua blok SETUP dijadikan SETUP RENDAH, supaya dicek user dulu, tidak langsung ke bot."""
    return [], [LOW_LABEL + block[len(SETUP_LABEL):] for block in blocks] + list(low)


def send_analysis(sender, config, answer):
    """Kirim jawaban analisa lengkap (termasuk blok SETUP) ke chat pribadi."""
    title = (f"📊 Analisa CLI · {config.skill_name or 'mode uji'}{' · dengan pesan' if config.message else ''}"
             f" · {datetime.now(WIB):%Y-%m-%d %H:%M} WIB")
    for index, part in enumerate(split_message(answer.strip())):
        notify(sender, config, f"{title}\n\n{part}" if index == 0 else part)


def notify(sender, config, message):
    try:
        send_with_retry(sender, config.report, message)
    except GeneratorError as exc:
        logging.warning("Kabar status gagal dikirim. %s", exc)
        return False
    return True


def tradingview_ready():
    try:
        with urlopen("http://127.0.0.1:9222/json/list", timeout=3) as response:
            targets = json.load(response)
        return any(
            target.get("type") == "page" and re.match(r"^https?://(?:[\w-]+\.)*tradingview\.com/chart(?:[/?#]|$)", target.get("url", ""), re.I)
            for target in targets
        )
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def ensure_tradingview(config, sleep=time.sleep, clock=time.monotonic):
    """Pastikan chart TradingView (CDP 9222) siap. Kalau belum, buka lewat skrip peluncur."""
    if tradingview_ready():
        return
    launcher = config.tv_launcher
    if not launcher.is_file():
        raise GeneratorError(f"Chart TradingView belum terbuka dan skrip peluncur TradingView tidak ditemukan: {launcher}")
    logging.info("Chart TradingView belum terbuka. Membuka TradingView...")
    try:
        # Tanpa pipa: Aplikasi yang dibuka skrip mewarisi handle, jadi pipa bikin proses ini menunggu aplikasi ditutup.
        subprocess.run(
            ["cmd", "/c", str(launcher)], cwd=str(launcher.parent),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=90, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        logging.warning("Skrip peluncur TradingView tidak selesai dengan normal. Tetap menunggu chart.")
    deadline = clock() + config.tv_wait
    while clock() < deadline:
        if tradingview_ready():
            logging.info("Chart TradingView siap.")
            return
        sleep(3)
    raise GeneratorError(
        "Chart TradingView tidak siap. Pastikan TradingView terbuka di halaman chart dan akun sudah login."
    )


def run_analysis(config, secrets):
    check_destinations(config, with_report=True)
    with open_sender(config, secrets) as sender:
        config = resolve_report(config, sender)
        tag = message_tag(config)
        logging.info("Analisa dimulai%s.", tag)
        notify(sender, config, f"CLI mulai analisa{tag}.")
        started = datetime.now(WIB)
        sent = 0
        answer = ""
        blocks = []
        try:
            ensure_tradingview(config)
            answer = analyze(config, secrets)
            blocks = extract_setups(answer)
            low = extract_setups(answer, LOW_LABEL)
            if config.message:
                blocks, low = force_low(blocks, low)
            if blocks:
                # Kotak masuk duluan (untuk eksekutor), baru analisa ke chat pribadi.
                for block in blocks:
                    send_with_retry(sender, config.inbox, block)
                    sent += 1
                status = f"✅ CLI terkirim{tag}. {sent} blok SETUP masuk ke kotak masuk."
                journal.record(config, journal.BOT, answer, blocks, low, started=started)
            elif low:
                status = f"⚪ CLI selesai{tag}. Tidak ada setup untuk bot."
                journal.record(config, journal.LOW, answer, low=low, started=started)
            elif re.search(r"(?im)^\s*GAGAL TRADINGVIEW\b", answer):
                raise GeneratorError("Claude gagal membuka atau menyambung ke TradingView. Cek data/hasil untuk detail.")
            elif no_setup(answer):
                # Alasan ikut di status, supaya tetap sampai walau analisa lengkap ke PM dimatikan.
                status = f"⚪ CLI selesai{tag}. Tidak ada setup.\n{no_setup_reason(answer)}"
                journal.record(config, journal.NONE, answer, detail=no_setup_reason(answer), started=started)
            else:
                raise GeneratorError("Jawaban Claude tidak dikenali. Cek data/hasil untuk melihat jawabannya.")
            if config.send_analysis:
                send_analysis(sender, config, answer)
            if low:
                # Tiap blok jadi satu pesan sendiri di chat pribadi, supaya gampang disalin.
                delivered = sum(notify(sender, config, block) for block in low)
                kind = ("setup (analisa dengan pesan: semua blok dicek kamu dulu)" if config.message
                        else "setup conviction rendah")
                status += (f" ⚠️ {len(low)} {kind} menunggu keputusanmu di chat ini"
                           + (f" ({len(low) - delivered} gagal terkirim, cek data/hasil)" if delivered < len(low) else "")
                           + ". Mau eksekusi: salin bloknya, ganti baris pertama jadi SETUP, posting ke kotak masuk.")
        except Exception as exc:
            if answer and config.send_analysis:
                send_analysis(sender, config, answer)
            detail = str(exc) if isinstance(exc, GeneratorError) else "Terjadi gangguan saat menjalankan analisa."
            detail = redact(detail, secrets)
            status = f"❌ CLI gagal{tag}. {detail}"
            if sent:
                status += f" {sent} blok sudah terkirim."
            journal.record(config, journal.FAIL, answer, blocks[:sent], detail=detail,
                           meta=getattr(exc, "meta", None) or getattr(answer, "meta", None), started=started)
            logging.error("%s", status)
            notify(sender, config, status)
            return False
        logging.info("%s", status)
        return notify(sender, config, status)


MESSAGE_DIR = DATA / "pesan"   # file pesan sementara dari menu/Dashboard


def read_message(path):
    """Isi file pesan user. Kosong = tanpa pesan."""
    try:
        text = Path(path).read_text(encoding="utf-8-sig").strip()
    except OSError:
        raise GeneratorError("File pesan untuk Claude tidak bisa dibaca.") from None
    if len(text) > MESSAGE_LIMIT:
        raise GeneratorError(f"Pesan terlalu panjang ({len(text):,} karakter, maksimal {MESSAGE_LIMIT:,}).")
    return text


def discard_message(path):
    """Hapus file pesan sementara setelah analisa (isinya sudah tersimpan di file hasil). Hanya di data/pesan."""
    if not path:
        return
    file = Path(path)
    try:
        if file.resolve().parent == MESSAGE_DIR.resolve():
            file.unlink(missing_ok=True)
    except OSError:
        logging.warning("File pesan sementara tidak bisa dihapus.")


def once(config, secrets):
    """Analisa sekarang. SKIPPED kalau generator masih sibuk setelah menunggu sebentar."""
    with wait_for_lock(WORK_PORT, LOCK_WAIT_SECONDS) as acquired:
        if not acquired:
            logging.warning("Analisa dilewati: generator sedang menganalisa. Jalankan lagi setelah selesai.")
            return SKIPPED
        return run_analysis(config, secrets)


CATCH_UP_MINUTES = 3     # jadwal yang terlewat karena PC tersendat masih dikejar sampai sekian menit
MISSED_CHECK_MINUTES = 1440   # jadwal terlewat yang dicatat di log: paling jauh 24 jam ke belakang


def pending_minutes(last, now, catch_up=CATCH_UP_MINUTES):
    """Menit jadwal yang perlu diproses sejak menit `last` (sudah diproses) sampai `now`.

    Mengembalikan (dijalankan, terlewat). Dijalankan = menit yang belum lewat dari `catch_up` menit, jadi
    PC yang tersendat beberapa detik tidak menghilangkan jadwal. Terlewat = menit yang lebih lama (PC sleep):
    tidak dijalankan susulan karena pasarnya sudah lewat, hanya dicatat. Jam mundur: tidak ada yang diulang.
    """
    current = now.replace(second=0, microsecond=0)
    if current <= last:
        return [], []
    gap = int((current - last).total_seconds() // 60)
    first_run = max(1, gap - catch_up + 1)
    run = [last + timedelta(minutes=step) for step in range(first_run, gap + 1)]
    first_missed = max(1, first_run - MISSED_CHECK_MINUTES)
    missed = [last + timedelta(minutes=step) for step in range(first_missed, first_run)]
    return run, missed


def load_skills():
    """Semua skill yang setelannya valid (yang rusak dicatat di log dan dilewati)."""
    base = read_config()
    result = []
    for name in list_skills():
        try:
            result.append(skills.read_skill(name, base))
        except GeneratorError as exc:
            logging.warning("%s", exc)
    return result


def load_due(now):
    return [skill.config for skill in load_skills() if skill.due(now)]


def report_missed(minutes, since):
    """Catat di log jadwal yang terlewat (PC sleep / tersendat lama). Tidak dijalankan susulan.

    `since` = menit pertama yang tidak sempat diproses (bisa lebih lama dari 24 jam yang dicek).
    """
    if not minutes:
        return
    try:
        routine = heartbeat.read()
    except GeneratorError:
        routine = None
    found = []
    for skill in load_skills():
        found += [(minute, skill.config.skill_name) for minute in minutes if skill.due(minute)]
    if routine is not None:
        found += [(minute, "pesan rutin") for minute in minutes if routine.due(minute)]
    since = f"{since:%d/%m %H:%M}"
    if not found:
        logging.info("Generator sempat tidak berdetak sejak %s (PC sleep atau tersendat). Tidak ada jadwal terlewat.",
                     since)
        return
    listing = ", ".join(f"{name} {minute:%d/%m %H:%M}" for minute, name in sorted(found))
    logging.warning("Jadwal terlewat karena generator tidak berdetak sejak %s (PC sleep atau tersendat): %s. "
                    "Tidak dijalankan susulan karena sudah lewat lebih dari %d menit.",
                    since, listing, CATCH_UP_MINUTES)


def report_failure(config, secrets, detail):
    """Kabar gagal lewat jalur terpisah, untuk error yang tidak sempat dilaporkan run_analysis sendiri."""
    name = config.skill_name or "mode uji"
    try:
        with open_sender(config, secrets) as sender:
            notify(sender, resolve_report(config, sender), f"❌ CLI gagal ({name}). {detail} Generator tetap jalan.")
    except Exception:
        logging.warning("Kabar gagal untuk %s juga tidak bisa dikirim ke Telegram.", name)


def run_queue(configs, secrets):
    """Jalankan skill yang jatuh tempo bergantian. Satu skill gagal tidak menghentikan skill berikutnya."""
    if not configs:
        return
    while True:
        with local_lock(WORK_PORT) as acquired:
            if acquired:
                for config in configs:
                    name = config.skill_name or "mode uji"
                    try:
                        run_analysis(config, secrets)
                    except GeneratorError as exc:
                        detail = redact(str(exc), secrets)
                        logging.error("Analisa %s gagal. %s", name, detail)
                        report_failure(config, secrets, detail)
                    except Exception as exc:
                        # Hanya nama jenis error yang dicatat: pesan aslinya bisa memuat data yang tidak perlu.
                        logging.error("Analisa %s gagal karena gangguan tak terduga (%s).", name, type(exc).__name__)
                        report_failure(config, secrets, "Gangguan tak terduga, cek log generator.")
                return
        time.sleep(POLL_SECONDS)


def send_routine(now=None):
    """Kirim pesan rutin 'CLI masih nyala' ke chat pribadi (setelan dibaca ulang, jadi selalu yang terbaru)."""
    config = read_config()
    secrets = read_secrets()
    check_destinations(config, with_report=True)
    with open_sender(config, secrets) as sender:
        config = resolve_report(config, sender)
        send_with_retry(sender, config.report, heartbeat.message(config, now or datetime.now(WIB)))
    logging.info("Pesan rutin terkirim ke chat pribadi.")


def consume_queue(pending):
    """Pekerja antrean. Tidak boleh berhenti: error apa pun dicatat, lalu menunggu jadwal berikutnya."""
    while True:
        configs, secrets = pending.get()
        if configs == ROUTINE:
            # Lewat antrean yang sama dengan analisa, supaya tidak memakai sesi Telegram bersamaan.
            # Untuk penanda ini, isi kedua bukan rahasia tapi jam jadwalnya.
            try:
                send_routine(now=secrets)
            except GeneratorError as exc:
                logging.warning("Pesan rutin gagal dikirim. %s", exc)
            except Exception as exc:
                logging.warning("Pesan rutin gagal dikirim (%s).", type(exc).__name__)
            continue
        try:
            run_queue(configs, secrets)
        except Exception as exc:
            logging.error("Antrean analisa terganggu (%s). Generator tetap jalan dan menunggu jadwal berikutnya.",
                          type(exc).__name__)


def daemon(config, secrets, parent=None):
    # Menunggu sebentar: Dashboard mengecek "generator nyala?" dengan memegang kunci ini sepersekian detik.
    with wait_for_lock(DAEMON_PORT, LOCK_WAIT_SECONDS) as acquired:
        if not acquired:
            logging.info("Generator jalan terus sudah nyala.")
            return True
        logging.info("Generator nyala. Jadwal dibaca dari setelan masing-masing skill.")
        write_pid()
        try:
            run_daemon(secrets, parent)
        finally:
            clear_pid()
        return True


def write_pid():
    """Catat nomor proses generator jalan terus, supaya Dashboard bisa menghentikannya."""
    try:
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        logging.warning("Nomor proses generator tidak bisa dicatat. Stop dari Dashboard tidak tersedia.")


def clear_pid():
    try:
        if PID_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
            PID_FILE.unlink()
    except (OSError, ValueError):
        pass


def parent_gone(parent):
    """True kalau generator dinyalakan Dashboard dan Dashboard itu sudah tidak ada."""
    if not parent:
        return False
    import psutil

    return not psutil.pid_exists(parent)


def queue_minute(pending, minute, now, secrets):
    """Masukkan pesan rutin dan skill yang jatuh tempo di `minute` ke antrean."""
    try:
        if heartbeat.read().due(minute):
            pending.put((ROUTINE, now))
    except GeneratorError as exc:
        logging.warning("%s", exc)
    try:
        configs = load_due(minute)
        if configs:
            pending.put((configs, read_secrets()))
    except GeneratorError as exc:
        logging.error("%s", redact(str(exc), secrets))
    except Exception as exc:
        logging.error("Jadwal %s gagal dibaca (%s).", f"{minute:%H:%M}", type(exc).__name__)


def run_daemon(secrets, parent=None):
    pending = SimpleQueue()
    Thread(target=consume_queue, args=(pending,), daemon=True).start()
    # Menit saat generator dinyalakan tidak dijalankan; jadwal mulai dari menit berikutnya.
    last_minute = datetime.now(WIB).replace(second=0, microsecond=0)
    last_pulse = None
    while True:
        time.sleep(POLL_SECONDS)
        now = datetime.now(WIB)
        minute = now.replace(second=0, microsecond=0)
        if minute != last_pulse:
            last_pulse = minute
            if parent_gone(parent):
                logging.info("Dashboard sudah tertutup. Generator ikut berhenti.")
                return
            try:
                heartbeat.pulse(now)   # "terakhir aktif" di menu
            except OSError:
                pass
        run, missed = pending_minutes(last_minute, now)
        if not run:
            continue   # menit yang sama, atau jam mundur: tunggu sampai lewat menit terakhir
        since, last_minute = last_minute + timedelta(minutes=1), run[-1]
        try:
            report_missed(missed, since)
        except Exception as exc:
            logging.warning("Jadwal terlewat tidak bisa dicek (%s).", type(exc).__name__)
        for item in run:
            queue_minute(pending, item, now, secrets)


def ready(config, secrets):
    def show(ok, message):
        print(f"{'✅' if ok else '❌'} {message}")
        return bool(ok)

    checks = [show(sys.version_info >= (3, 13), f"Python {sys.version.split()[0]}")]
    claude = shutil.which("claude")
    checks.append(show(claude, "Perintah claude ditemukan"))
    auth = False
    if claude:
        try:
            result = subprocess.run(
                [claude, "auth", "status"], capture_output=True, timeout=20, check=False,
            )
            auth = result.returncode == 0 and json.loads(result.stdout).get("loggedIn") is True
        except (OSError, ValueError, AttributeError, subprocess.TimeoutExpired):
            pass
    checks.append(show(auth, "Claude Code login"))
    print(f"• Model analisa: {config.model} · effort {config.effort or 'bawaan'} (bawaan global; tiap skill bisa punya setelan sendiri)")
    builtin = "semua" if config.builtin_tools is None else f"{len(config.builtin_tools)}"
    tv = "semua" if config.tv_tools is None else f"{len(config.tv_tools)}"
    print(f"• Tool yang diizinkan: bawaan Claude {builtin} · TradingView {tv} (atur di Skill & analisa)")
    for key in SECRET_KEYS:
        filled = bool(secrets[key])
        needed = (key == "GEN_TG_BOT_TOKEN") == (config.sender == "bot")
        if needed:
            checks.append(show(filled, f".env {key} {'terisi' if filled else 'kosong'}"))
        else:
            print(f"• .env {key} {'terisi' if filled else 'kosong'} (opsional untuk mode ini)")
    checks.append(show(valid_inbox(config), "ID kotak_masuk terisi dan berbentuk -100..."))
    if config.report and not valid_report(config):
        checks.append(show(False, "laporan_ke bukan ID chat pribadi. Kosongkan supaya dideteksi otomatis."))
    if tradingview_ready():
        show(True, "Chart TradingView menjawab di port 9222")
    else:
        print("• Chart belum terdeteksi di port 9222. Program akan membuka TradingView saat analisa.")
    checks.append(show(config.tv_launcher.is_file(), f"Skrip peluncur TradingView ditemukan ({config.tv_launcher.name})"))
    checks.append(show(shutil.which("node"), "Perintah node ditemukan"))
    checks.extend(skill_status(config, show))
    try:
        print(f"• Pesan rutin (tanda masih hidup): {heartbeat.read().describe()}")
    except GeneratorError as exc:
        checks.append(show(False, str(exc)))
    if config.sender == "bot":
        bot_id = None
        can_post = False
        try:
            bot = BotSender(secrets["GEN_TG_BOT_TOKEN"])
            bot_id = bot.call("getMe")["id"]
            if valid_inbox(config):
                member = bot.call("getChatMember", chat_id=int(config.inbox), user_id=bot_id)
                can_post = member.get("status") == "creator" or (
                    member.get("status") == "administrator" and member.get("can_post_messages") is True
                )
        except (GeneratorError, KeyError, TypeError, AttributeError):
            pass
        checks.append(show(bot_id, "Token bot valid (getMe)"))
        checks.append(show(can_post, "Bot admin dengan izin posting di kotak masuk"))
        checks.append(show(*private_chat_status(config, bot if bot_id else None)))
    else:
        print(f"• Laporan dikirim ke {config.report if config.report else 'Saved Messages akunmu'}")
        checks.append(show((SYSTEM / "data" / "generator.session").is_file(), "File sesi akun tersedia. Login lewat menu Telegram bila perlu."))
    return all(checks)


def skill_status(config, show):
    """Tampilkan semua skill (NYALA/MATI, jadwal, model, kode). Kembalikan hasil cek yang wajib lolos."""
    names = list_skills()
    if not names:
        print("• Belum ada skill. Analisa sekarang hanya bisa mode uji fungsi (TES).")
        return []
    checks, active = [], []
    for name in names:
        try:
            skill = skills.read_skill(name, config)
        except GeneratorError as exc:
            checks.append(show(False, str(exc)))
            continue
        cfg = skill.config
        line = (f"• Skill {name} [{'NYALA' if skill.enabled else 'MATI'}] · {skill.describe()} · "
                f"{cfg.model}/{cfg.effort or 'bawaan'} · kode {cfg.setup_code}")
        if cfg.setup_code != "TES":
            line += "  ⚠️ bukan TES: setup bisa jadi order beneran di Automanage"
        print(line)
        if skill.enabled:
            active.append(name)
            if not skill.days or (skill.mode == "jam" and not skill.times):
                print(f"  ⚠️ {name} NYALA tapi jadwalnya kosong, jadi tidak akan jalan sendiri.")
    if not active:
        print("• Belum ada skill NYALA: generator jalan terus tidak akan menganalisa sesuai jadwal.")
    return checks


def private_chat_status(config, bot):
    """(ok, pesan) untuk chat pribadi tujuan laporan. Kosong -> coba deteksi otomatis dan simpan."""
    if bot is None:
        return False, "Chat pribadi belum bisa dicek (token bot bermasalah)"
    try:
        if config.report:
            if not valid_report(config):
                return False, "laporan_ke bukan ID chat pribadi"
            bot.call("getChat", chat_id=int(config.report))
            return True, "Bot bisa mengirim ke chat pribadimu"
        chat_id = bot.find_private_chat()
        if chat_id is None:
            return False, "Chat pribadi belum terdeteksi: buka chat dengan bot generator, tekan Start, lalu cek lagi"
        set_report_chat(chat_id)
        return True, "Chat pribadi terdeteksi otomatis dan disimpan"
    except GeneratorError as exc:
        return False, f"Chat pribadi: {exc}"


EFFORT_NOTES = {
    "low": "cepat, paling hemat, analisa paling dangkal",
    "medium": "seimbang",
    "high": "dalam",
    "xhigh": "lebih dalam, lebih lama",
    "max": "paling dalam, paling lama dan paling boros kuota",
}


def pick_claude_settings(config, ask, save):
    """Pilih model, lalu effort, untuk satu skill. Enter = tetap, 0 = batal. save(model, effort) menyimpan."""
    choices = list(config.model_choices)
    print("Setelan analisa Claude\n")
    print(f"Sekarang: model {config.model} · effort {config.effort or 'bawaan'}\n")
    print("Langkah 1/2: pilih model")
    for number, name in enumerate(choices, 1):
        mark = "   <- aktif" if name == config.model else ""
        print(f"  {number}. {name}{mark}")
    manual = len(choices) + 1
    print(f"  {manual}. Ketik manual (nama lengkap, misal claude-opus-5-5, atau keluarga baru)")
    print("Nama singkat (fable/opus/sonnet/haiku) otomatis memakai versi terbaru.")
    answer = ask("\nNomor model (Enter = tetap, 0 = batal): ").strip()
    model = config.model
    if answer == "0":
        print("Dibatalkan. Tidak ada yang diubah.")
        return True
    if answer:
        if not re.fullmatch(r"[0-9]{1,6}", answer) or not 1 <= int(answer) <= manual:
            print("Nomor tidak ada di daftar. Tidak ada yang diubah.")
            return False
        if int(answer) == manual:
            model = ask("Ketik nama model: ").strip()
            if not MODEL_NAME.fullmatch(model):
                print("Nama model tidak valid. Tidak ada yang diubah.")
                return False
        else:
            model = choices[int(answer) - 1]

    print("\nLangkah 2/2: pilih effort (seberapa keras Claude berpikir)")
    for number, level in enumerate(EFFORTS, 1):
        mark = "   <- aktif" if level == config.effort else ""
        print(f"  {number}. {level:<7} {EFFORT_NOTES[level]}{mark}")
    print(f"  {len(EFFORTS) + 1}. bawaan Claude Code{'   <- aktif' if not config.effort else ''}")
    answer = ask("\nNomor effort (Enter = tetap, 0 = batal): ").strip()
    effort = config.effort
    if answer == "0":
        print("Dibatalkan. Tidak ada yang diubah.")
        return True
    if answer:
        if not re.fullmatch(r"[0-9]{1,6}", answer) or not 1 <= int(answer) <= len(EFFORTS) + 1:
            print("Nomor tidak ada di daftar. Tidak ada yang diubah.")
            return False
        effort = EFFORTS[int(answer) - 1] if int(answer) <= len(EFFORTS) else ""

    if (model, effort) == (config.model, config.effort):
        print("\nTidak ada yang diubah.")
        return True
    save(model, effort)
    print(f"\n✅ Tersimpan: model {model} · effort {effort or 'bawaan'}")
    print("Tanpa model cadangan: kalau model penuh/tidak tersedia, analisa sesi itu gagal dan dilaporkan.")
    return True


def setup_logging(secrets):
    class SecretFilter(logging.Filter):
        def filter(self, record):
            record.msg = redact(record.getMessage(), secrets)
            record.args = ()
            return True

    LOGS.mkdir(parents=True, exist_ok=True)
    handlers = [
        logging.StreamHandler(),
        RotatingFileHandler(LOGS / "generator.log", maxBytes=512_000, backupCount=3, encoding="utf-8"),
    ]
    for handler in handlers:
        handler.addFilter(SecretFilter())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)
    logging.getLogger("telethon").setLevel(logging.CRITICAL + 1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("daemon", "once", "test", "ready", "login", "rutin"))
    parser.add_argument("--skill")
    parser.add_argument("--induk", type=int)   # nomor proses Dashboard yang menyalakan generator
    parser.add_argument("--pesan-file")        # file berisi pesan user untuk analisa sekarang (dari menu/Dashboard)
    args = parser.parse_args()
    action = args.action
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        secrets = read_secrets()
        setup_logging(secrets)
        if action in ("daemon", "once"):
            skills.migrate()
        config = read_config()
        if action == "daemon":
            return 0 if daemon(config, secrets, parent=args.induk) else 1
        if action == "ready":
            return 0 if ready(config, secrets) else 1
        if action == "rutin":
            send_routine()
            return 0
        if action == "once":
            if args.skill:
                config = skills.read_skill(args.skill, config).config
            if args.pesan_file:
                config = replace(config, message=read_message(args.pesan_file))
            result = None
            try:
                result = once(config, secrets)
                return EXIT_SKIPPED if result == SKIPPED else (0 if result else 1)
            finally:
                if result != SKIPPED:   # dilewati: file pesan disimpan supaya bisa dipakai lagi
                    discard_message(args.pesan_file)
        with wait_for_lock(WORK_PORT, LOCK_WAIT_SECONDS) as acquired:
            if not acquired:
                logging.warning("Dilewati: generator sedang menganalisa. Coba lagi setelah selesai.")
                return EXIT_SKIPPED
            if action == "test":
                check_destinations(config, with_report=False)
                with open_sender(config, secrets) as sender:
                    send_with_retry(sender, config.inbox, TEST_MESSAGE)
                logging.info("Blok SETUP Kode: TES terkirim ke kotak masuk.")
            else:
                if config.sender != "akun":
                    raise GeneratorError("Pilih cara kirim akun di menu Telegram sebelum login.")
                with open_sender(config, secrets) as sender:
                    sender.login()
                logging.info("Akun Telegram sudah login.")
    except GeneratorError as exc:
        logging.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        logging.info("Generator dihentikan.")
        return 0
    except Exception:
        logging.error("Generator gagal berjalan. Cek akses folder, dependensi, dan setelan.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
