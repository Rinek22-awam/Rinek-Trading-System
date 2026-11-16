"""Alur otak pembaca, terlepas dari Telethon supaya bisa dites:

    pesan channel -> saring (GIF, stiker, emoji doang dilewati)
                  -> tampung per channel beberapa detik (gambar + caption + pesan susulan = satu paket)
                  -> satu panggilan Claude per paket, dengan ringkasan ingatan channel
                  -> dicek kode -> SETUP / UPDATE ke kotak masuk -> ingatan diperbarui
"""
import asyncio
import logging
import re
import time
from typing import Awaitable, Callable, Dict, List, Optional

from copier.setup_format import parse_setup

from . import brain as B
from .memory import Memory, label, parse_label

log = logging.getLogger(__name__)

QUIET_SEC = 4.0       # paket dikirim setelah channel diam selama ini...
MAX_WAIT_SEC = 20.0   # ...atau paling lama sejak pesan pertama paket
RECENT_N = 10         # pesan terakhir yang ikut dikirim ke Claude
MAX_REPLIED_IMAGES = 2

_HAS_WORD = re.compile(r"[^\W_]", re.UNICODE)


# --- saringan dan reply (objek pesan Telethon) ----------------------------------
def is_image(msg) -> bool:
    if getattr(msg, "photo", None):
        return True
    doc = getattr(msg, "document", None)
    if doc is None or getattr(msg, "sticker", None) or getattr(msg, "gif", None):
        return False
    mime = getattr(getattr(msg, "file", None), "mime_type", "") or ""
    return mime.startswith("image/") and mime != "image/gif"


def worth_reading(msg) -> bool:
    """GIF, stiker, video, voice, polling, pesan layanan, dan teks tanpa huruf/angka
    (emoji doang) tidak dikirim ke Claude."""
    if getattr(msg, "action", None):
        return False
    for kind in ("gif", "sticker", "video", "voice", "video_note", "poll"):
        if getattr(msg, kind, None):
            return False
    if is_image(msg):
        return True
    return bool(_HAS_WORD.search(getattr(msg, "raw_text", "") or ""))


def reply_target(msg) -> Optional[int]:
    """Nomor pesan yang di-reply, atau None. Di grup bertopik setiap pesan "membalas"
    pesan pembuka topik; itu bukan reply sungguhan."""
    rt = getattr(msg, "reply_to", None)
    if rt is None:
        return None
    if getattr(rt, "forum_topic", False):
        return rt.reply_to_msg_id if getattr(rt, "reply_to_top_id", None) else None
    return getattr(rt, "reply_to_msg_id", None)


# --- penampung paket -----------------------------------------------------------------
class Collector:
    """Menampung pesan per kunci (chat, topik) lalu memanggil flush(kunci, [pesan]) setelah
    channel diam `quiet` detik atau paling lama `max_wait` detik sejak pesan pertama."""

    def __init__(self, flush: Callable[[tuple, list], Awaitable[None]], quiet: float = QUIET_SEC,
                 max_wait: float = MAX_WAIT_SEC, clock=time.monotonic):
        self.flush, self.quiet, self.max_wait, self.clock = flush, quiet, max_wait, clock
        self.items: Dict[tuple, list] = {}
        self.first: Dict[tuple, float] = {}
        self.last: Dict[tuple, float] = {}
        self.tasks: Dict[tuple, asyncio.Task] = {}

    def add(self, key: tuple, item):
        now = self.clock()
        self.items.setdefault(key, []).append(item)
        self.first.setdefault(key, now)
        self.last[key] = now
        if key not in self.tasks or self.tasks[key].done():
            self.tasks[key] = asyncio.ensure_future(self._wait(key))

    async def _wait(self, key):
        while True:
            due = min(self.last[key] + self.quiet, self.first[key] + self.max_wait)
            delay = due - self.clock()
            if delay <= 0:
                break
            await asyncio.sleep(delay)
        batch = self.items.pop(key, [])
        self.first.pop(key, None)
        self.last.pop(key, None)
        if batch:
            await self.flush(key, batch)


# --- otak: paket -> postingan --------------------------------------------------------
Poster = Callable[[str, Optional[bytes]], Awaitable[Optional[int]]]
Fetcher = Callable[[int, int], Awaitable[Optional[B.Msg]]]


class Pipeline:
    """post(teks, gambar|None) -> nomor pesan di kotak masuk. fetch(chat, msg_id) -> Msg
    (untuk pesan yang di-reply tapi tidak ada di ingatan). think(prompt_for, images) -> dict."""

    def __init__(self, memory: Memory, think, post: Poster, cfg_pembaca: dict,
                 fetch: Optional[Fetcher] = None, inbox_chat: Optional[int] = None,
                 journals: tuple = (), clock=time.time, models=None):
        """models(sumber) -> {"image_model", "text_model"}: model Claude khusus sumber itu."""
        self.memory, self.think, self.post, self.fetch = memory, think, post, fetch
        self.p, self.inbox_chat, self.journals, self.clock = cfg_pembaca, inbox_chat, journals, clock
        self.models = models

    async def _send(self, src, text: str, image=None):
        """Kirim ke kotak masuk dengan kode sumber, supaya bot memakai SOP sumber ini."""
        return await self.post(B.stamp(text, getattr(src, "kode", "")), image)

    def tidy(self):
        """Bersihkan ingatan: tutup konteks yang di jurnal bot sudah selesai, atau terlalu tua."""
        if self.inbox_chat is not None:
            for cid in self.memory.sync_journals(self.inbox_chat, self.journals):
                log.info("ingatan: %s ditutup (jurnal bot)", label(cid))
        for cid in self.memory.expire():
            log.info("ingatan: %s ditutup (umur)", label(cid))

    async def handle(self, batch: List[B.Msg], src) -> List[str]:
        """Proses satu paket dari satu channel. Mengembalikan teks yang diposting (untuk tes/log)."""
        chat, topic = batch[0].chat, batch[0].topic
        self.tidy()
        now = self.clock()
        mem = self.memory
        ids = {m.id for m in batch}
        open_ctx = mem.open_contexts(chat, topic)
        recent = [r for r in mem.recent(chat, topic, RECENT_N) if r["msg_id"] not in ids]

        # pesan yang di-reply: dari ingatan kalau ada, kalau tidak diambil dari Telegram
        replied, ctx_of, images = [], {}, {m.id: m.images for m in batch if m.images}
        known_recent = {r["msg_id"] for r in recent}
        fetched_images = 0
        for target in dict.fromkeys(m.reply_to for m in batch if m.reply_to and m.reply_to not in ids):
            ctx = mem.context_for_message(chat, target)
            if ctx:
                ctx_of[target] = label(ctx["id"])
            if target in known_recent and (ctx is None or ctx["status"] == "open"):
                continue   # sudah terlihat di RECENT MESSAGES / OPEN SETUPS
            rec = mem.message(chat, target)
            item = {"id": target, "context": ctx, "text": rec["text"] if rec else "",
                    "has_image": bool(rec and rec["has_image"])}
            if rec is None and self.fetch is not None:
                try:
                    old = await self.fetch(chat, target)
                except Exception:
                    log.exception("gagal mengambil pesan #%s", target)
                    old = None
                if old is not None:
                    item.update(text=old.text, has_image=old.has_image)
                    if old.images and ctx is None and fetched_images < MAX_REPLIED_IMAGES:
                        images[f"r{target}"] = old.images[:1]
                        fetched_images += 1
            replied.append(item)

        profile = mem.profile(chat, topic)   # dibaca di sini: prompt_for jalan di thread lain

        def prompt_for(names):
            for item in replied:
                item["images"] = names.get(f"r{item['id']}", [])
            new_names = {k: v for k, v in names.items() if not str(k).startswith("r")}
            return B.build_prompt(batch, profile, open_ctx, recent, replied, new_names, now, ctx_of)

        results: Dict[int, List[str]] = {m.id: [] for m in batch}
        posted: List[str] = []
        try:
            kw = self.models(src) if self.models else {}
            answer = await asyncio.to_thread(self.think, prompt_for, images, **kw)
        except Exception as e:
            log.exception("otak gagal")
            why = str(e)
            head = ("Kuota Claude habis atau dibatasi, pembaca tidak bisa membaca untuk sementara. "
                    if B.is_quota_error(why) else "Claude gagal membaca. ")
            quote = " | ".join(B._flat(m.text, 150) for m in batch if m.text)
            text = B.build_info(f"{head}Pesan #{', #'.join(str(m.id) for m in batch)} terlewat"
                                f"{': ' + quote if quote else ''}. ({B._flat(why, 200)})", src.nama, src.ke)
            await self._send(src, text)
            posted.append(text)
            self._remember(batch, {m.id: ["gagal dibaca"] for m in batch})
            return posted

        used_images = set()
        for a in answer["aksi"]:
            kind = str(a.get("jenis") or "").lower()
            from_ids = [int(x) for x in a.get("dari_pesan") or [] if str(x).lstrip("-").isdigit()]
            from_ids = [x for x in from_ids if x in ids] or [m.id for m in batch]
            if kind == "setup":
                posted += await self._setup(a, batch, from_ids, used_images, src, results)
            elif kind == "update":
                posted += await self._update(a, batch, from_ids, src, results)
            elif kind == "info" and a.get("teks"):
                text = B.build_info(str(a["teks"]), src.nama, src.ke)
                await self._send(src, text)
                posted.append(text)
                for x in from_ids:
                    results[x].append("info")

        for t in answer["tutup"]:
            cid = parse_label(t.get("id"))
            c = mem.get(cid)
            if c and c["chat"] == chat and c["status"] == "open":
                mem.close(cid, f"Claude: {B._flat(t.get('alasan') or 'selesai', 150)}")
        for n in answer["catatan"]:
            cid = parse_label(n.get("id"))
            c = mem.get(cid)
            if c and c["chat"] == chat and n.get("teks"):
                mem.set_note(cid, B._flat(str(n["teks"]), 200))
        if answer["gaya"]:
            mem.set_profile(chat, topic, answer["gaya"])
        self._remember(batch, results)
        return posted

    async def _setup(self, a, batch, from_ids, used_images, src, results) -> List[str]:
        caption = " ".join(m.text for m in batch if m.id in from_ids)
        from .updates import is_highrisk
        hr = is_highrisk(caption, self.p) if self.p.get("kata_highrisk") else False
        text, why, symbol = B.build_setup(a, self.p["simbol_dari_harga"], src.nama, src.ke, hr)
        image = None
        for m in batch:
            if m.id in from_ids and m.images and m.id not in used_images:
                image = m.images[0][0]
                used_images.add(m.id)
                break
        chat, topic = batch[0].chat, batch[0].topic
        if text:
            inbox_msg = await self._send(src, text, image)
            s, _ = parse_setup(text)
            summary = _summary(s, src.ke) if s else text.replace("\n", " ")
            cid = self.memory.new_context(chat, topic, summary, symbol=s.symbol if s else symbol,
                                          dest=src.ke, inbox_msg=inbox_msg, msg_ids=from_ids)
            for x in from_ids:
                results[x].append(f"setup {label(cid)}")
            log.info("setup %s diposting:\n%s", label(cid), text)
            return [text]
        # Claude melihat setup tapi kode menolaknya: kabari, dan tetap diingat supaya update
        # trader berikutnya tidak dikira setup baru
        raw = (f"{symbol or '?'} {a.get('arah') or '?'} entry {'/'.join(map(str, B._list(a.get('entry')))) or '-'} "
               f"SL {a.get('sl') or '-'} TP {'/'.join(map(str, B._list(a.get('tp')))) or '-'}")
        cid = self.memory.new_context(chat, topic, f"{raw} (tidak dikirim ke bot: {why})", symbol=symbol,
                                      dest=src.ke, msg_ids=from_ids)
        info = B.build_info(f"Claude melihat setup tapi tidak dikirim ke bot ({why}). Dibaca: {raw}. "
                            f"Alasan Claude: {a.get('alasan') or '-'}", src.nama, src.ke)
        await self._send(src, info, image)
        for x in from_ids:
            results[x].append(f"setup {label(cid)} ditolak kode")
        log.info("setup dari Claude ditolak kode: %s", why)
        return [info]

    async def _update(self, a, batch, from_ids, src, results) -> List[str]:
        chat = batch[0].chat
        ctx = self.memory.get(parse_label(a.get("konteks")))
        if ctx and ctx["chat"] != chat:
            ctx = None
        if ctx is None:   # Claude tidak menyebut konteks: pakai pesan yang di-reply kalau ada
            for m in batch:
                if m.id in from_ids and m.reply_to:
                    ctx = self.memory.context_for_message(chat, m.reply_to)
                    if ctx:
                        break
        quote = " ".join(m.text for m in batch if m.id in from_ids)
        text = B.build_update(a, src.nama, src.ke, ctx, quote)
        await self._send(src, text)
        if ctx:
            self.memory.link(ctx["id"], chat, from_ids)
        tag = f"update {label(ctx['id']) if ctx else '?'} {str(a.get('aksi') or 'INFO').upper()}"
        for x in from_ids:
            results[x].append(tag)
        log.info("update diposting:\n%s", text)
        return [text]

    def _remember(self, batch, results):
        for m in batch:
            self.memory.add_message(m.chat, m.topic, m.id, m.time, m.reply_to, m.text, m.has_image,
                                    ", ".join(results.get(m.id) or []) or "diabaikan")


def _summary(s, dest: str) -> str:
    entry = f"entry {B._fmt(s.entry)}" if s.entry else "market"
    if s.entry2:
        entry += f"/{B._fmt(s.entry2)}"
    tps = "/".join(B._fmt(t) for t in s.tps)
    return f"{s.symbol} {s.side} {s.order_type} {entry} SL {B._fmt(s.sl)} TP {tps} → {dest}"
