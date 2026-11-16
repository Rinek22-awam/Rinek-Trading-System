"""Tambah satu sumber sinyal, dituntun langkah demi langkah (MENU > Sumber sinyal > Tambah),
atau ganti channel/topik sumber yang sudah ada.

Semua pilihan memakai nomor dari daftar, jadi user tidak perlu mengetik ID channel.
Butuh login Telegram pembaca (akun user) untuk melihat channel dan topik yang diikuti.
Sumber baru mendapat kode tetap (S01, S02, ...) dan file SOP sendiri (sumber/<kode>.yaml).
"""
import os
from typing import Callable, List, Optional, Tuple

from . import sources

Ask = Callable[[str], str]
STEPS = 6


def _input(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def _choose(ask: Ask, items: List[str], prompt="  Pilih nomor (0 = batal): ") -> Optional[int]:
    for i, item in enumerate(items, 1):
        print(f"  {i}. {item}")
    while True:
        raw = ask(prompt)
        if raw in ("0", ""):
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(items):
            return int(raw) - 1
        print("  Nomor tidak ada di daftar.")


def _topic_numbers(ask: Ask, topics: dict, prompt: str, label="topik") -> Optional[List[int]]:
    while True:
        raw = ask(prompt)
        if not raw:
            return None
        try:
            nums = [int(x) for x in raw.replace(" ", "").split(",") if x]
        except ValueError:
            print(f"  Tulis nomor {label} saja, pisahkan dengan koma.")
            continue
        wrong = [n for n in nums if n not in topics]
        if nums and not wrong:
            return nums
        print(f"  Nomor {label} tidak ada di daftar: {', '.join(map(str, wrong)) or '-'}")


def step(n: int, text: str, total: int = STEPS):
    print(f"\n  Langkah {n} dari {total}: {text}\n")


async def list_topics(client, entity) -> dict:
    from telethon import functions
    from datetime import datetime, timezone

    result = {}
    offset = (datetime.fromtimestamp(0, timezone.utc), 0, 0)
    while True:
        date, message, topic = offset
        page = await client(functions.messages.GetForumTopicsRequest(
            peer=entity, offset_date=date, offset_id=message, offset_topic=topic, limit=100))
        topics = [t for t in page.topics if getattr(t, "title", None)]
        result.update((t.id, t.title) for t in topics)
        if not topics or len(result) >= page.count:
            return result
        last = topics[-1]
        dates = {m.id: m.date for m in page.messages if getattr(m, "date", None)}
        next_offset = (dates.get(last.top_message, last.date), last.top_message, last.id)
        if next_offset == offset:
            return result
        offset = next_offset


def source_dialog(dialog):
    return dialog.is_channel or getattr(dialog, "is_group", False)


def inbox_dialog(dialog):
    entity = dialog.entity
    return (getattr(entity, "broadcast", False) and
            (getattr(entity, "creator", False) or
             getattr(getattr(entity, "admin_rights", None), "post_messages", False)))


async def pick_dialog(client, ask: Ask, accepts, empty_message: str):
    dialogs = [d async for d in client.iter_dialogs() if accepts(d)]
    if not dialogs:
        print(empty_message)
        return None
    i = _choose(ask, [d.name for d in dialogs])
    return dialogs[i] if i is not None else None


async def pick_channel(client, ask: Ask, total: int = STEPS) -> Optional[Tuple]:
    """Langkah channel + topik. (dialog, topik, abaikan) atau None kalau batal."""
    step(1, "pilih channel atau grup", total)
    d = await pick_dialog(client, ask, source_dialog,
                          "  Akun Telegram-mu belum mengikuti channel/grup apa pun.")
    if d is None:
        return None
    entity = d.entity

    step(2, "topik yang dibaca", total)
    topik, abaikan = "semua", []
    if getattr(entity, "forum", False):
        topics = await list_topics(client, entity)
        print(f"  Topik di '{d.name}':")
        for tid, name in topics.items():
            print(f"    {tid:>8}   {name}")
        print()
        mode = _choose(ask, ["Semua topik",
                             "Semua topik, KECUALI beberapa (misalnya topik rekap / watcher / obrolan)",
                             "Hanya topik tertentu"])
        if mode is None:
            return None
        if mode == 1:
            abaikan = _topic_numbers(ask, topics, "  Nomor topik yang DIABAIKAN, pisahkan koma: ")
            if abaikan is None:
                return None
        elif mode == 2:
            topik = _topic_numbers(ask, topics, "  Nomor topik yang DIBACA, pisahkan koma: ")
            if topik is None:
                return None
    else:
        print(f"  '{d.name}' tidak memakai topik: semua pesannya dibaca.")
    return d, topik, abaikan


async def choose_inbox(client, ask: Ask = _input):
    """Pilih channel yang bisa diposting akun pembaca dan dibaca eksekutor."""
    return await pick_dialog(client, ask, inbox_dialog, "  Tidak ada channel tempat akun pembaca bisa posting.")


def choose_account(ask: Ask, folder=None) -> Optional[str]:
    """Pilih akun Bybit yang sudah ada atau masukkan API baru. Nama akun, atau None (batal)."""
    from copier import accounts
    accs = accounts.load(folder)
    items = [a.describe() for a in accs] + ["+ Masukkan API key baru"]
    i = _choose(ask, items)
    if i is None:
        return None
    if i < len(accs):
        return accs[i].nama
    acc = new_account(ask, accs)
    if acc is None:
        return None
    accounts.save(accs + [acc], folder)
    print(f"\n  [OK] Akun '{acc.nama}' disimpan di akun-bybit.env.")
    return acc.nama


def choose_accounts(ask: Ask, platform, folder=None):
    from copier import accounts, accounts_mt5

    items = accounts.load(folder) if platform == "bybit" else accounts_mt5.load()
    if not items:
        if platform == "bybit":
            name = choose_account(ask, folder)
            return [name] if name else None
        print("  Tambah akun lewat Setelan universal > MT5 > Akun MT5.")
        return None
    choices = {index: account.nama for index, account in enumerate(items, 1)}
    for index, name in choices.items():
        print(f"  {index}. {name}")
    numbers = _topic_numbers(ask, choices, "  Nomor akun tujuan, pisahkan dengan koma (Enter = batal): ",
                             label="akun")
    return list(dict.fromkeys(choices[index] for index in numbers)) if numbers else None


def new_account(ask: Ask, existing) -> Optional[object]:
    """Tanya nama, mode, key, secret. Key/secret diketik di jendela ini, tidak dikirim ke mana pun."""
    from copier import accounts
    print("\n  API key Bybit baru. Buat di Bybit: API Management > Create New Key > System-generated.")
    print("  Izin: centang hanya Contract (Orders + Positions). JANGAN centang Withdraw.")
    print("  Akun Bybit wajib CROSS margin dan mode posisi one-way.\n")
    taken = {a.nama.lower() for a in existing}
    while True:
        nama = ask("  Nama akun (misal Utama, Sub STX; Enter = batal): ")
        if not nama:
            return None
        if not accounts.valid_name(nama):
            print("  Pakai huruf, angka, spasi, - atau _ saja (maks 30).")
        elif nama.lower() in taken:
            print(f"  Nama '{nama}' sudah dipakai.")
        else:
            break
    m = _choose(ask, ["demo (Demo Trading bybit.com, untuk tes)", "testnet (testnet.bybit.com)",
                      "live (uang asli)"])
    if m is None:
        return None
    key = ask("  API key: ")
    secret = ask("  API secret: ")
    if not key or not secret:
        print("  Key/secret kosong. Dibatalkan.")
        return None
    return accounts.Account(nama, accounts.MODES[m], key, secret)


async def add_source(client, path: str = sources.PATH, ask: Ask = _input, root: str = ".",
                     accounts_folder=None) -> Optional[sources.Source]:
    from copier import sopfiles
    print("\n  TAMBAH SUMBER SINYAL")
    print("  Pilih channel/grup, topiknya, pengolahnya, tujuannya, lalu namanya.")
    existing = sources.load(path)

    picked = await pick_channel(client, ask)
    if picked is None:
        print("  Dibatalkan.")
        return None
    d, topik, abaikan = picked

    step(3, "diolah oleh")
    j = _choose(ask, ["Claude CLI: semua chat dibaca dan ditafsirkan (gambar, caption, reply, "
                      "banyak setup per gambar). Cocok untuk channel apa pun",
                      "Bot pembaca kode: hanya teks format TraderAwam (🟢 LONG · XXXUSDT, Entry, TP, SL)"])
    if j is None:
        print("  Dibatalkan.")
        return None
    olah = ["claude", "traderawam"][j]

    step(4, "dikirim ke bot eksekutor mana")
    k = _choose(ask, ["MT5 (Exness)", "Bybit"])
    if k is None:
        print("  Dibatalkan.")
        return None
    ke = ["mt5", "bybit"][k]
    from copier import accounts_mt5
    selected = None
    if ke == "bybit" or accounts_mt5.FILE.exists():
        selected = choose_accounts(ask, ke, accounts_folder)
        if selected is None:
            print("  Dibatalkan.")
            return None

    step(5, "SOP awal (risiko, TP, BE, pengaman; bisa diubah nanti)")
    same = [s for s in existing if s.ke == ke and s.kode]
    choice = _choose(ask, ["Bawaan (risiko 1%, TP 50/30/20, BE setelah TP1, ...)"] +
                     [f"Salin dari {s.kode} {s.nama}" for s in same])
    if choice is None:
        print("  Dibatalkan.")
        return None
    values = None
    if choice > 0:
        universal = os.path.join(root, sopfiles.UNIVERSAL[ke])
        values = sopfiles.sop_values(sopfiles.effective(universal, same[choice - 1].kode, root))

    step(6, "nama sumber (ditulis di setup dan jurnal)")
    taken = {s.nama for s in existing}
    default = sources.slug(d.name)
    while True:
        nama = sources.slug(ask(f"  Nama (Enter = {default}): ") or default)
        if nama not in taken:
            break
        print(f"  Nama '{nama}' sudah dipakai sumber lain. Pakai nama lain.")

    src = sources.Source(nama=nama, channel=str(d.id), judul=d.name, topik=topik, abaikan=abaikan,
                         olah=olah, ke=ke, kode=sopfiles.new_code(existing, root),
                         akun=selected[0] if selected and ke == "bybit" else "", akun_tujuan=selected)
    print(f"\n  Ringkasan:\n    {src.describe()}")
    if ask("\n  Simpan? (y/n): ").lower() not in ("y", "ya", "yes"):
        print("  Tidak disimpan.")
        return None
    sopfiles.write_source(src.kode, ke, nama, values, root)
    sources.save(existing + [src], path)
    print(f"\n  [OK] Tersimpan sebagai {src.kode}. SOP-nya bisa diatur di MENU > Sumber sinyal > {src.kode}.")
    print("  Nyalakan ulang pembaca supaya sumber baru ikut ditonton.")
    return src


async def change_channel(client, code: str, path: str = sources.PATH, ask: Ask = _input) -> bool:
    """Ganti channel/topik sumber yang sudah ada; kode, SOP, dan setelan lain tetap."""
    items = sources.load(path)
    src = sources.by_code(items, code)
    if src is None:
        print(f"  Sumber {code} tidak ada.")
        return False
    print(f"\n  GANTI CHANNEL / TOPIK {src.kode} {src.nama}")
    print(f"  Sekarang: {src.judul or src.channel}, {src.topic_text()}")
    picked = await pick_channel(client, ask, total=2)
    if picked is None:
        print("  Dibatalkan, tidak diubah.")
        return False
    d, src.topik, src.abaikan = picked
    src.channel, src.judul = str(d.id), d.name
    sources.save(items, path)
    print(f"\n  [OK] {src.kode} sekarang: {src.judul}, {src.topic_text()}. Nyalakan ulang pembaca.")
    return True
