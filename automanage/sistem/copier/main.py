import argparse
import logging
import os
import sys

from copier import envfile

from .config import load_config
from .process_control import log_file, managed, ready
from .single import BOT_PORT, claim
from .telegram import TelegramBot

log = logging.getLogger("copier")


def _need(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        raise SystemExit(f"{name} belum diisi di file .env")
    return v


def find_ids(bot: TelegramBot):
    """Print chat ids seen by the bot, to fill TG_INBOX_CHANNEL_ID and TG_ADMIN_USER_ID."""
    print("Kirim satu postingan di channel kotak masuk, dan kirim /start ke bot di chat pribadi.")
    print("Lalu tekan Enter di sini...")
    input()
    for u in bot.get_updates(0, wait=0):
        m = u.get("channel_post") or u.get("message") or {}
        chat = m.get("chat", {})
        who = m.get("from", {})
        if chat.get("type") == "channel":
            print(f"Channel  '{chat.get('title')}'  ->  TG_INBOX_CHANNEL_ID={chat['id']}")
        elif chat.get("type") == "private":
            print(f"Chat pribadi dengan {who.get('first_name')}  ->  TG_ADMIN_USER_ID={who.get('id')}")


def executor_telegram(role, token):
    from .executor_telegram import ExecutorTelegram

    try:
        api_id = int(_need("TG_API_ID"))
        inbox = int(_need("TG_INBOX_CHANNEL_ID"))
        admin = int(_need("TG_ADMIN_USER_ID"))
    except ValueError:
        raise SystemExit("TG_API_ID, TG_INBOX_CHANNEL_ID, dan TG_ADMIN_USER_ID harus berupa angka.") from None
    return ExecutorTelegram(role, token, api_id, _need("TG_API_HASH"), inbox, admin)


def main(*, desktop=False):
    os.makedirs("logs", exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout),
                                  log_file("logs/bot.log")])
    envfile.load()
    ap = argparse.ArgumentParser(description="Eksekutor setup dari channel Telegram ke MT5")
    ap.add_argument("-c", "--config", default="config.yaml")
    ap.add_argument("--cari-id", action="store_true", help="tampilkan ID channel dan ID kamu")
    a = ap.parse_args()

    lock = claim(BOT_PORT)   # held until this program exits
    if lock is None:
        raise SystemExit("Bot sudah jalan di jendela lain. Tutup dulu jendela itu kalau mau menyalakan ulang.")
    with lock:
        if a.cari_id:
            find_ids(TelegramBot(_need("TG_BOT_TOKEN")))
            return
        with managed(BOT_PORT, log) as stopping:
            run_bot(a.config, stopping, desktop=desktop)


def run_bot(config, stopping, *, desktop=False):
    bot = executor_telegram("mt5", _need("TG_BOT_TOKEN"))

    from .app import App
    from .broker import Broker
    from .journal import Journal
    from .sopfiles import migrate

    for line in migrate():
        log.info("susunan setelan: %s", line)
    cfg = load_config(config)
    if not desktop:
        from .multi import MultiMT5
        with bot:
            journal = Journal(cfg["bot"]["jurnal"])
            app = MultiMT5(cfg, None, journal, bot, bot.inbox, bot.admin)
            try:
                app.route(config)
                app.startup_checks()
                app.log_startup()
                ready(log, "Bot MT5 jalan. Tekan Ctrl+C untuk berhenti.", port=BOT_PORT)
                app.run_telethon(stopping)
            except KeyboardInterrupt:
                pass
            finally:
                if hasattr(app.profiles, "close"):
                    app.profiles.close()
                journal.db.close()
            app.notify("⏹ Bot dimatikan. Limit dan SL/TP tetap ada di MT5, tapi BE/trailing tidak dijaga.")
        return
    broker = Broker(cfg)
    if not broker.connect(os.getenv("MT5_LOGIN"), os.getenv("MT5_PASSWORD"),
                          os.getenv("MT5_SERVER"), os.getenv("MT5_PATH")):
        raise SystemExit("Tidak bisa terhubung ke MT5. Pastikan MT5 desktop terbuka dan sudah login.")
    journal = Journal(cfg["bot"]["jurnal"])
    with bot:
        app = App(cfg, broker, journal, bot, bot.inbox, bot.admin, sources_root=".", universal_path=config)
        if not app.startup_checks():
            raise SystemExit(1)
        app.log_startup()
        ready(log, "Bot jalan. Tekan Ctrl+C untuk berhenti.", port=BOT_PORT)
        try:
            app.run_telethon(stopping)
        except KeyboardInterrupt:
            pass
        log.info("Bot MT5 dihentikan.")
        app.notify("⏹ Bot dimatikan. Limit dan SL/TP tetap ada di MT5, tapi BE/trailing tidak dijaga.")


if __name__ == "__main__":
    main()
