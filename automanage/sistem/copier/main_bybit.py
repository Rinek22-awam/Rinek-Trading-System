"""Titik masuk bot eksekutor Bybit:  python -m copier.main_bybit

Membaca channel kotak masuk yang SAMA dengan bot MT5, tapi lewat bot Telegram sendiri
(BYBIT_TG_BOT_TOKEN), jurnal sendiri, dan setelan universal sendiri (config-bybit.yaml).
SOP per sumber (sumber/<kode>.yaml); akun Bybit per sumber dari akun-bybit.env.
"""
import argparse
import logging
import os
import sys

from copier import envfile

from . import bybit_cooldown
from .broker_bybit import BybitBroker, request_log
from .config import load_config
from .main import _need, executor_telegram, find_ids
from .process_control import log_file, managed, ready
from .single import BYBIT_BOT_PORT, claim
from .telegram import TelegramBot

log = logging.getLogger("copier")


def main():
    os.makedirs("logs", exist_ok=True)
    console = logging.StreamHandler(sys.stdout)
    console.addFilter(lambda record: record.name != request_log.name)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        handlers=[console,
                                  log_file("logs/bot-bybit.log")])
    envfile.load()
    ap = argparse.ArgumentParser(description="Eksekutor setup dari channel Telegram ke Bybit")
    ap.add_argument("-c", "--config", default="config-bybit.yaml")
    ap.add_argument("--cari-id", action="store_true", help="tampilkan ID channel dan ID kamu")
    a = ap.parse_args()

    lock = claim(BYBIT_BOT_PORT)   # held until this program exits
    if lock is None:
        raise SystemExit("Bot Bybit sudah jalan di jendela lain.")
    with lock:
        if a.cari_id:
            find_ids(TelegramBot(telegram_token()))
            return
        with managed(BYBIT_BOT_PORT, log) as stopping:
            run_bot(a.config, stopping)


def telegram_token():
    token = _need("BYBIT_TG_BOT_TOKEN")
    mt5_bot_id = os.getenv("TG_BOT_TOKEN", "").strip().partition(":")[0]
    if mt5_bot_id and token.partition(":")[0] == mt5_bot_id:
        raise SystemExit(
            "Bybit dan MT5 memakai bot Telegram yang sama. Buat bot kedua di BotFather, "
            "masukkan tokennya ke BYBIT_TG_BOT_TOKEN, lalu tambahkan bot Bybit ke channel kotak masuk."
        )
    return token


def run_bot(config, stopping):
    if seconds := bybit_cooldown.remaining():
        raise SystemExit(f"Bybit masih dalam jeda HTTP 403. Coba lagi dalam {seconds} detik.")
    bot = executor_telegram("bybit", telegram_token())

    from .app_bybit import BybitAdapter
    from .multi import MultiBybit
    from .journal import Journal
    from .sopfiles import migrate

    for line in migrate():
        log.info("susunan setelan: %s", line)
    cfg = load_config(config)
    def make(acc):
        return BybitBroker(acc.mode, acc.key, acc.secret, on_ip_cooldown=bybit_cooldown.save,
                           account_name=acc.nama)
    journal = Journal(cfg["bot"]["jurnal"])
    with bot:
        app = MultiBybit(cfg, BybitAdapter(cfg, None), journal, bot, bot.inbox, bot.admin)
        app.route(config, make)
        if not app.startup_checks():
            raise SystemExit(1)
        app.log_startup()
        ready(log, "Bot Bybit jalan. Tekan Ctrl+C untuk berhenti.", port=BYBIT_BOT_PORT)
        try:
            app.run_telethon(stopping)
        except KeyboardInterrupt:
            pass
        finally:
            app.profiles.close()
        log.info("Bot Bybit dihentikan.")
        app.notify("⏹ Bot Bybit dimatikan. Limit, SL, dan TP yang sudah terpasang tetap ada di Bybit, "
                   "tapi TP parsial untuk entry yang baru terisi, BE, dan trailing tidak dijaga.")


if __name__ == "__main__":
    main()
