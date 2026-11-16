"""Signal reader: watches the source channel with your Telegram account and posts
SETUP / UPDATE messages into the inbox channel, where the executor bot picks them up.

It never trades. It posts as your account (not as the bot) because a Telegram bot
does not receive the channel posts it makes itself.
"""
import argparse
import asyncio
import io
import logging
import os
import sys
import time

from copier import envfile

from copier import sopfiles
from copier.config import load_config
from copier.process_control import log_file, managed, ready
from copier.single import READER_PORT, claim

from . import sources
from .brain import stamp
from .textsignal import build_notice_update, build_text_setup

log = logging.getLogger("reader")
CONFIG = "config.yaml"


def _need(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        raise SystemExit(f"{name} belum diisi di file .env")
    return v


def _peer(value):
    """'-100123' / -100123 -> int id, anything else (a @username or t.me link) as is."""
    s = str(value).strip()
    return int(s) if s.lstrip("-").isdigit() else s


def topic_of(msg) -> int:
    """Forum topic a message belongs to. Messages in the General topic carry no topic: 1."""
    rt = getattr(msg, "reply_to", None)
    if rt is not None and getattr(rt, "forum_topic", False):
        return rt.reply_to_top_id or rt.reply_to_msg_id
    return 1


def pick(by_chat: dict, chat_id: int, topic: int):
    """Sumber pertama di daftar yang membaca pesan dari chat + topik ini, atau None."""
    return next((s for s in by_chat.get(chat_id, []) if s.matches(topic)), None)


def info(src, text: str) -> str:
    return stamp(f"UPDATE\nSumber: {src.nama}\nKe: {src.ke}\nAksi: INFO\nTeks: {text}", src.kode)


def telegram_client():
    from telethon import TelegramClient

    return TelegramClient("pembaca", int(_need("TG_API_ID")), _need("TG_API_HASH"))


async def run(cfg: dict, config_path: str = "config.yaml", add: bool = False, edit: str = "",
              *, interactive=True):
    client = telegram_client()
    try:
        if interactive:
            await client.start()
        else:
            await client.connect()
            if not await client.is_user_authorized():
                raise SystemExit("Login Telegram Reader dulu lewat MENU.bat > Nyalakan.")
        await _watch_sources(client, cfg, add, edit)
    finally:
        await client.disconnect()


async def _watch_sources(client, cfg, add, edit):
    from telethon import events, utils

    if add or edit:
        from .wizard import add_source, change_channel
        await (change_channel(client, edit) if edit else add_source(client))
        return

    srcs = [s for s in sources.load(sources.PATH, cfg) if s.kode]
    if not srcs:
        raise SystemExit("Belum ada sumber sinyal. Tambah dulu lewat MENU > Sumber sinyal.")
    dialogs_loaded = False

    async def resolve_peer(value):
        nonlocal dialogs_loaded
        peer = _peer(value)
        try:
            return await client.get_entity(peer)
        except ValueError:
            if dialogs_loaded or not isinstance(peer, int):
                raise
        await client.get_dialogs()   # populate the cache only for an unknown numeric ID
        dialogs_loaded = True
        return await client.get_entity(peer)

    inbox = await resolve_peer(_need("TG_INBOX_CHANNEL_ID"))
    by_chat, chats, entity_of = {}, [], {}
    for s in srcs:
        try:
            ent = await resolve_peer(s.channel)
        except (ValueError, TypeError):
            log.error("sumber %s: channel %s tidak ditemukan (akunmu sudah join?)", s.nama, s.channel)
            continue
        cid = utils.get_peer_id(ent)
        if cid not in by_chat:
            chats.append(ent)
            entity_of[cid] = ent
        by_chat.setdefault(cid, []).append(s)
    if not chats:
        raise SystemExit("Tidak ada channel sumber yang bisa dibuka. Cek daftar sumber di MENU.")

    # Tiap sumber memilih pengolahnya sendiri: Claude (semua chat, gambar, reply, ingatan per
    # channel) atau bot pembaca kode (teks format TraderAwam).
    brain_add = None
    if any(s.olah == "claude" for s in srcs):
        brain_add = _start_brain(client, cfg, inbox, utils.get_peer_id(inbox), entity_of)

    @client.on(events.NewMessage(chats=chats))
    async def on_message(event):
        msg = event.message
        src = pick(by_chat, event.chat_id, topic_of(msg))
        if src is None:
            return   # topik yang tidak ditonton
        try:
            if src.olah == "claude":
                await brain_add(msg, event.chat_id, topic_of(msg), src)
            elif msg.raw_text:   # includes captions; the parser still requires a complete signal
                await on_text_signal(msg, src)
        except Exception:
            log.exception("gagal memproses pesan %s", msg.id)

    async def on_text_signal(msg, src):
        setup, why = build_text_setup(msg.raw_text, src.nama, src.ke)
        if setup:
            setup = stamp(setup, src.kode)
            await client.send_message(inbox, setup)
            log.info("setup teks diposting:\n%s", setup)
        elif why:   # mirip sinyal tapi angkanya janggal: kabari, jangan diam
            log.info("sinyal teks %s dari %s dilewati: %s", msg.id, src.kode, why)
            await client.send_message(inbox, info(src, f"Sinyal teks tidak dijadikan setup ({why})."))
        else:       # notifikasi koin (dibatalkan / SL tersentuh) -> tombol di bot; sisanya diam
            upd = build_notice_update(msg.raw_text, src.nama, src.ke)
            if upd:
                await client.send_message(inbox, stamp(upd, src.kode))
                log.info("notifikasi %s diteruskan", src.nama)

    for s in srcs:
        log.info("Menonton %s", s.describe())
    ready(log, "Pembaca jalan. Menunggu pesan Telegram baru; riwayat tidak diproses otomatis.",
          port=READER_PORT)
    await client.run_until_disconnected()


def journal_paths(cfg: dict) -> tuple:
    """Jurnal bot MT5 dan bot Bybit, untuk menutup konteks yang setupnya sudah selesai."""
    paths = [cfg["bot"]["jurnal"]]
    if os.path.exists("config-bybit.yaml"):
        try:
            paths.append(load_config("config-bybit.yaml")["bot"]["jurnal"])
        except Exception:
            log.warning("config-bybit.yaml tidak terbaca; jurnal Bybit tidak dicocokkan")
    return tuple(dict.fromkeys(paths))


async def to_msg(msg, chat: int, topic: int):
    """Pesan Telethon -> Msg untuk otak (gambar ikut diunduh)."""
    from .brain import Msg
    from .pipeline import is_image, reply_target
    images = []
    if is_image(msg):
        data = await msg.download_media(file=bytes)
        if data:
            images.append((data, msg.file.mime_type or "image/jpeg"))
    when = msg.date.timestamp() if getattr(msg, "date", None) else time.time()
    return Msg(id=msg.id, chat=chat, topic=topic, time=when, text=msg.raw_text or "",
               reply_to=reply_target(msg), images=images)


def _start_brain(client, cfg: dict, inbox, inbox_chat: int, entity_of: dict):
    """Siapkan otak Claude: ingatan, penampung paket, dan satu antrean (Claude dipanggil
    satu per satu supaya ingatan tidak bentrok dan kuota tidak terkuras sekaligus).
    Mengembalikan fungsi add(pesan, chat, topik, sumber)."""
    from .brain import ClaudeBrain
    from .memory import Memory
    from .pipeline import Collector, Pipeline, worth_reading

    p = cfg["pembaca"]
    brain = ClaudeBrain()

    def models(src):
        """Model Claude khusus sumber (SOP sumber), dibaca tiap kali supaya perubahan langsung berlaku."""
        try:
            c = sopfiles.effective(CONFIG, src.kode)["claude"]
            return {"image_model": c["model_gambar"], "text_model": c["model_teks"]}
        except Exception:
            log.exception("SOP %s tidak terbaca, model Claude bawaan dipakai", src.kode)
            return {}

    async def post(text, image=None):
        if image:
            photo = io.BytesIO(image)
            photo.name = "sinyal.jpg"
            sent = await client.send_file(inbox, photo, caption=text)
        else:
            sent = await client.send_message(inbox, text)
        return getattr(sent, "id", None)

    async def fetch(chat, msg_id):
        ent = entity_of.get(chat)
        old = await client.get_messages(ent, ids=msg_id) if ent is not None else None
        return await to_msg(old, chat, 0) if old else None

    pipe = Pipeline(Memory(), brain.think, post, p, fetch=fetch, inbox_chat=inbox_chat,
                    journals=journal_paths(cfg), models=models)
    queue = asyncio.Queue()

    async def worker():
        while True:
            batch, src = await queue.get()
            try:
                await pipe.handle(batch, src)
            except Exception:
                log.exception("otak gagal memproses paket")

    asyncio.ensure_future(worker())

    async def flush(key, items):
        await queue.put(([m for m, _ in items], items[0][1]))

    collector = Collector(flush)

    async def add(msg, chat, topic, src):
        if not worth_reading(msg):
            return
        collector.add((chat, topic, src.nama), (await to_msg(msg, chat, topic), src))

    log.info("Otak Claude aktif (model per sumber), ingatan data/ingatan.db")
    return add


def try_image(cfg: dict, path: str, text: str = "", code: str = ""):
    """Baca satu gambar (+ teks pesan, opsional) dengan otak Claude dan tampilkan postingan
    yang akan dikirim. Tanpa Telegram, ingatan sementara (tidak disimpan)."""
    from types import SimpleNamespace

    from .brain import ClaudeBrain, Msg
    from .memory import Memory
    from .pipeline import Pipeline

    p = cfg["pembaca"]
    images = []
    if path:
        with open(path, "rb") as f:
            images.append((f.read(), "image/png" if path.lower().endswith(".png") else "image/jpeg"))
    out = []

    async def post(t, image=None):
        out.append(t + ("\n[dengan gambar]" if image else ""))
        return len(out)

    items = [s for s in sources.load(sources.PATH, cfg) if s.olah == "claude"]
    src = sources.by_code(items, code) or (items[0] if items else None)
    claude = sopfiles.effective(CONFIG, src.kode)["claude"] if src else cfg["claude"]
    brain = ClaudeBrain(image_model=claude["model_gambar"], text_model=claude["model_teks"])
    pipe = Pipeline(Memory(":memory:"), brain.think, post, p)
    src = src or SimpleNamespace(nama="coba", ke="mt5", kode="")
    print(f"Dibaca dengan SOP sumber {src.kode or '-'} {src.nama} (model {claude['model_gambar']})\n")
    asyncio.run(pipe.handle([Msg(id=1, chat=0, topic=0, time=time.time(), text=text, images=images)], src))
    if not out:
        print("Claude menilai pesan ini tidak berisi setup atau update (diabaikan).")
    for t in out:
        print(t, end="\n\n")


async def run_managed(cfg, config_path, stopping, *, interactive):
    reader = asyncio.current_task()
    stop_requested = False

    async def watch_stop():
        nonlocal stop_requested
        while not stopping():
            await asyncio.sleep(0.25)
        stop_requested = True
        reader.cancel()

    watcher = asyncio.create_task(watch_stop())
    try:
        await run(cfg, config_path, interactive=interactive)
    except asyncio.CancelledError:
        if not stop_requested:
            raise
        log.info("Reader dihentikan.")
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)


def main():
    os.makedirs("logs", exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout),
                                  log_file("logs/pembaca.log")])
    logging.getLogger("telethon").setLevel(logging.WARNING)
    envfile.load()
    ap = argparse.ArgumentParser(description="Pembaca sinyal: channel sumber -> channel kotak masuk")
    ap.add_argument("-c", "--config", default="config.yaml")
    ap.add_argument("--coba-gambar", metavar="FILE", help="baca satu gambar dengan Claude, tanpa Telegram")
    ap.add_argument("--teks", default="", help="dipakai bersama --coba-gambar: teks pesan yang ikut gambar")
    ap.add_argument("--sumber", default="", help="dipakai bersama --coba-gambar: kode sumber (S01)")
    ap.add_argument("--tambah-sumber", action="store_true", help="tambah satu sumber sinyal ke daftar")
    ap.add_argument("--ubah-sumber", default="", metavar="KODE", help="ganti channel/topik satu sumber")
    ap.add_argument("--non-interaktif", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.coba_gambar:
        try_image(load_config(a.config), a.coba_gambar, a.teks, a.sumber)
        return
    lock = claim(READER_PORT)   # the Telegram login file can only be used by one program
    if lock is None:
        raise SystemExit("Pembaca sudah jalan di jendela lain. Tutup dulu jendela pembaca itu.")
    with lock:
        if a.tambah_sumber or a.ubah_sumber:
            for line in sopfiles.migrate():
                log.info("susunan setelan: %s", line)
            asyncio.run(run(load_config(a.config), a.config, add=a.tambah_sumber,
                            edit=a.ubah_sumber.upper()))
            return
        with managed(READER_PORT, log) as stopping:
            for line in sopfiles.migrate():
                log.info("susunan setelan: %s", line)
            asyncio.run(run_managed(load_config(a.config), a.config, stopping,
                                    interactive=not a.non_interaktif))


if __name__ == "__main__":
    main()
