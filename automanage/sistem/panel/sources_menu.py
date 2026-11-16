"""MENU > Sumber sinyal: daftar sumber, tambah, hapus, dan pengaturan PER SUMBER
(channel/topik, pengolah, tujuan, akun Bybit, nama, dan seluruh SOP-nya)."""
import os
from pathlib import Path
import re
import shutil

from copier import accounts, accounts_mt5, sopfiles
from copier.config import load_config
from copier.models import ACTIVE, AWAITING
from copier.single import READER_PORT, claim
from reader import sources

from .settings import SOURCE_SECTIONS, SOURCE_SECTIONS_BYBIT, Settings
from .ui import ask, choose, clear, pause, title


def summary(items) -> str:
    """Satu baris untuk header menu, contoh: '2 sumber (1 ke MT5, 1 ke Bybit)'."""
    if not items:
        return "belum ada"
    per = {k: sum(1 for s in items if s.ke == k) for k in sources.TUJUAN}
    parts = [f"{n} ke {sources.TUJUAN[k]}" for k, n in per.items() if n]
    return f"{len(items)} sumber ({', '.join(parts)})"


def risk_text(root, s) -> str:
    try:
        cfg = sopfiles.effective(os.path.join(root, sopfiles.UNIVERSAL[s.ke]), s.kode, root)
    except Exception as e:
        return f"SOP rusak: {e}"
    r = cfg["risiko"]
    unit = "USDT" if s.ke == "bybit" else "$"
    return f"risiko {r['persen']}%" if r["mode"] == "persen" else f"risiko {r['dolar']} {unit}"


def row(root, s) -> str:
    names = s.account_names()
    akun = f" · akun {', '.join(names)}" if names else ""
    olah = "Claude" if s.olah == "claude" else "TraderAwam"
    return f"{s.kode} {s.nama:<14} -> {sources.TUJUAN[s.ke]:<5} · {olah}{akun} · {risk_text(root, s)}"


def busy_setups(root, code: str) -> list:
    """Setup sumber ini yang masih menunggu konfirmasi / aktif, di jurnal MT5 dan Bybit."""
    import sqlite3
    found = []
    for ke, f in sopfiles.UNIVERSAL.items():
        try:
            jpath = os.path.join(root, load_config(os.path.join(root, f))["bot"]["jurnal"])
        except Exception:
            continue
        if not os.path.exists(jpath):
            continue
        con = sqlite3.connect(f"file:{jpath}?mode=ro", uri=True)
        try:
            states = (AWAITING, *ACTIVE)
            q = f"SELECT id FROM setups WHERE kode = ? AND status IN ({', '.join('?' * len(states))})"
            found += [f"#{r[0]} ({sources.TUJUAN[ke]})" for r in con.execute(q, (code, *states))]
        except sqlite3.Error:
            pass
        finally:
            con.close()
    return found


def change_code(code, raw, root="."):
    new = raw.strip().upper()
    if not re.fullmatch(r"[A-Z0-9_-]+", new) or re.fullmatch(r"CON|PRN|AUX|NUL|(?:COM|LPT)[0-9]", new):
        raise ValueError("Pakai huruf A–Z, angka, strip (-), atau garis bawah (_), misalnya AYAM.")
    if new == code:
        return code

    lock = claim(READER_PORT)
    if lock is None:
        raise ValueError("Hentikan Pembaca sinyal sebelum ganti kode.")
    with lock:
        path = os.path.join(root, sources.PATH)
        items = sources.load(path)
        source = sources.by_code(items, code)
        if source is None:
            raise ValueError(f"Sumber {code} sudah tidak tersedia.")
        old_sop = Path(sopfiles.sop_path(code, root))
        new_sop = Path(sopfiles.sop_path(new, root))
        archive = Path(root, sopfiles.ARCHIVE_DIR)
        archived = any(re.fullmatch(rf"{re.escape(new)}-[0-9]{{8}}-[0-9]{{6}}\.yaml", file.name, re.I)
                       for file in archive.glob("*.yaml"))
        if sources.by_code(items, new) or new_sop.exists() or archived:
            raise ValueError(f"Kode {new} sudah pernah dipakai. Pilih kode lain.")
        busy = busy_setups(root, code)
        if busy:
            raise ValueError(f"{code} masih punya setup aktif atau menunggu konfirmasi: {', '.join(busy)}.")

        try:
            if old_sop.exists():
                shutil.copyfile(old_sop, new_sop)
            else:
                sopfiles.write_source(new, source.ke, source.nama, root=root)
            source.kode = new
            sources.save(items, path)
        except Exception:
            new_sop.unlink(missing_ok=True)
            raise
        sopfiles.archive_source(code, root)
    return new


def remove(items, path, root) -> bool:
    if not items:
        print("  Belum ada sumber untuk dihapus.")
        return False
    print("  Sumber mana yang dihapus?\n")
    i = choose([f"{s.kode} {s.nama} -> {sources.TUJUAN[s.ke]} ({s.judul or s.channel}, {s.topic_text()})"
                for s in items])
    if i is None:
        return False
    s = items[i]
    busy = busy_setups(root, s.kode)
    if busy:
        print(f"\n  [X] {s.kode} masih punya setup aktif / menunggu konfirmasi: {', '.join(busy)}.")
        print("  Tunggu selesai, atau batalkan/tutup lewat Telegram dulu, supaya tetap dijaga SOP-nya.")
        return False
    if ask(f"\n  Hapus sumber {s.kode} '{s.nama}'? SOP-nya disimpan di sumber/terhapus/. (y/n): ").lower() \
            not in ("y", "ya", "yes"):
        print("  Tidak jadi dihapus.")
        return False
    sources.save([x for x in items if x is not s], path)
    sopfiles.archive_source(s.kode, root)
    print(f"\n  [OK] {s.kode} '{s.nama}' dihapus. Nyalakan ulang pembaca supaya berlaku.")
    return True


def screen(cfg, reader_on, run_tool, root=".", bot_running=lambda: False):
    """reader_on(): apakah pembaca jalan. run_tool(*args): jalankan modul di jendela ini."""
    path = os.path.join(root, sources.PATH)
    while True:
        clear()
        title("SUMBER SINYAL")
        print("  Tiap sumber punya kode, pengolah, tujuan, dan SOP sendiri. Pembaca menulis kodenya")
        print("  di setiap setup; bot memakai SOP sumber itu. Setup tanpa kode ditolak.\n")
        try:
            items = sources.load(path)
        except Exception as e:   # file diedit manual dan rusak
            print(f"  [X] sumber.yaml tidak bisa dibaca: {e}")
            pause()
            return
        if not items:
            print("  (belum ada sumber, pembaca tidak bisa dinyalakan)\n")
        pick = choose([row(root, s) for s in items] + ["+ Tambah sumber", "Hapus sumber"], back="Kembali")
        if pick is None:
            return
        if pick < len(items):
            source_screen(items[pick].kode, reader_on, run_tool, root)
            continue
        if pick == len(items):
            if reader_on():
                print("\n  Pembaca sedang jalan. Tutup dulu jendela pembaca (login Telegram dipakai), lalu coba lagi.")
            else:
                run_tool("-m", "reader.main", "--tambah-sumber")
        else:
            print()
            remove(items, path, root)
        pause()


# --- satu sumber ------------------------------------------------------------------
def source_screen(code, reader_on, run_tool, root="."):
    path = os.path.join(root, sources.PATH)
    while True:
        items = sources.load(path)
        s = sources.by_code(items, code)
        if s is None:
            return
        sop = sopfiles.sop_path(s.kode, root)
        if not os.path.exists(sop):
            sopfiles.write_source(s.kode, s.ke, s.nama, None, root)
        sections = SOURCE_SECTIONS_BYBIT if s.ke == "bybit" else SOURCE_SECTIONS
        settings = Settings(sop, bybit=s.ke == "bybit", sections=sections, symbols=False, live=True,
                            heading=f"SOP {s.kode} {s.nama}")
        clear()
        title(f"SUMBER {s.kode} · {s.nama}")
        print(f"  Channel : {s.judul or s.channel} ({s.topic_text()})")
        print(f"  SOP     : {os.path.relpath(sop, root)} · {risk_text(root, s)}")
        print("  Perubahan SOP langsung dibaca bot. Perubahan channel/pengolah/tujuan/akun/nama:")
        print("  nyalakan ulang pembaca.\n")
        ident = [f"Channel & topik     : {s.judul or s.channel}, {s.topic_text()}",
                 f"Diolah oleh         : {sources.OLAH[s.olah]}",
                 f"Tujuan              : {sources.TUJUAN[s.ke]}"]
        ident.append(f"Akun tujuan         : {', '.join(s.account_names()) or '(belum dipilih)'}")
        ident.append(f"Nama                : {s.nama}")
        ident.append(f"Ganti kode          : {s.kode}")
        pick = choose(ident + [f"SOP · {name}" for name, _ in sections], back="Kembali")
        if pick is None:
            return
        actions = [lambda: _channel(s, reader_on, run_tool), lambda: _olah(s, items, path),
                   lambda: _tujuan(s, items, path, root)]
        actions.append(lambda: _akun(s, items, path))
        actions.append(lambda: _nama(s, items, path))
        if pick == len(actions):
            raw = ask(f"\n  Kode baru untuk {code} (Enter = batal): ").strip()
            if raw:
                try:
                    code = change_code(code, raw, root)
                except (ValueError, OSError) as error:
                    print(f"\n  [X] {error}")
                    pause()
            continue
        if pick < len(actions):
            actions[pick]()
        else:
            settings.section(*sections[pick - len(ident)])


def _channel(s, reader_on, run_tool):
    if reader_on():
        print("\n  Pembaca sedang jalan. Tutup dulu jendela pembaca, lalu coba lagi.")
    else:
        run_tool("-m", "reader.main", "--ubah-sumber", s.kode)
    pause()


def _olah(s, items, path):
    print(f"\n  Diolah oleh sekarang: {sources.OLAH[s.olah]}\n")
    keys = list(sources.OLAH)
    i = choose([sources.OLAH[k] for k in keys], back="Batal")
    if i is not None and keys[i] != s.olah:
        s.olah = keys[i]
        sources.save(items, path)
        print(f"\n  [OK] {s.kode} sekarang diolah oleh {sources.OLAH[s.olah]}. Nyalakan ulang pembaca.")
        pause()


def _tujuan(s, items, path, root):
    new = "bybit" if s.ke == "mt5" else "mt5"
    busy = busy_setups(root, s.kode)
    if busy:
        print(f"\n  [X] Tujuan tidak bisa diganti: {s.kode} masih punya setup {', '.join(busy)}.")
        pause()
        return
    if ask(f"\n  Ganti tujuan {s.kode} dari {sources.TUJUAN[s.ke]} ke {sources.TUJUAN[new]}? "
           f"SOP yang sama dipertahankan. (y/n): ").lower() not in ("y", "ya"):
        return
    selected = None
    if new == "bybit" or accounts_mt5.FILE.exists():
        from reader.wizard import choose_accounts
        selected = choose_accounts(ask, new)
        if selected is None:
            print("  Dibatalkan.")
            pause()
            return
    values = sopfiles.sop_values(sopfiles.effective(os.path.join(root, sopfiles.UNIVERSAL[s.ke]), s.kode, root))
    s.ke, s.akun, s.akun_tujuan = new, "", selected
    sopfiles.write_source(s.kode, new, s.nama, values, root)
    sources.save(items, path)
    print(f"\n  [OK] {s.kode} sekarang ke {sources.TUJUAN[new]}. Cek SOP-nya, lalu nyalakan ulang pembaca.")
    pause()


def _akun(s, items, path):
    from reader.wizard import choose_accounts
    print(f"\n  Akun tujuan {s.kode}: {', '.join(s.account_names()) or '(belum dipilih)'}\n")
    selected = choose_accounts(ask, s.ke)
    if selected:
        s.akun_tujuan = selected
        sources.save(items, path)
        print(f"\n  [OK] {s.kode} memakai akun {', '.join(selected)}.")
    pause()


def _nama(s, items, path):
    raw = ask(f"\n  Nama baru untuk {s.kode} (Enter = batal): ")
    if not raw:
        return
    nama = sources.slug(raw)
    if any(x.nama == nama and x is not s for x in items):
        print(f"  Nama '{nama}' sudah dipakai sumber lain.")
    else:
        s.nama = nama
        sources.save(items, path)
        print(f"  [OK] Nama {s.kode} sekarang '{nama}'.")
    pause()


# --- akun Bybit (Setelan universal > Bybit) ------------------------------------------
def accounts_screen(root="."):
    while True:
        clear()
        title("AKUN BYBIT")
        print("  Disimpan di akun-bybit.env (folder depan, tidak ikut git). Tiap sumber Bybit")
        print("  bisa memilih beberapa akun; dua sumber boleh berbagi akun.\n")
        accs = accounts.load()
        try:
            items = sources.load(os.path.join(root, sources.PATH))
        except Exception:
            items = []
        rows = []
        for a in accs:
            users = [s.kode for s in items if s.ke == "bybit" and uses_account(s, a.nama)]
            rows.append(f"{a.describe()} · dipakai: {', '.join(users) or '-'}")
        pick = choose(rows + ["+ Tambah akun (API key baru)", "Hapus akun"], back="Kembali")
        if pick is None:
            return
        if pick < len(accs):
            _edit_account(accs, pick)
        elif pick == len(accs):
            from reader.wizard import new_account
            acc = new_account(ask, accs)
            if acc:
                accounts.save(accs + [acc])
                print(f"\n  [OK] Akun '{acc.nama}' disimpan.")
            pause()
        else:
            _remove_account(accs, items)


def _edit_account(accs, i):
    a = accs[i]
    print(f"\n  {a.describe()}\n")
    j = choose(["Ganti API key + secret", f"Ganti mode (sekarang {a.mode})"], back="Batal")
    if j == 0:
        key, secret = ask("  API key baru: "), ask("  API secret baru: ")
        if key and secret:
            a.key, a.secret = key, secret
            accounts.save(accs)
            print("  [OK] Disimpan. Nyalakan ulang bot Bybit.")
    elif j == 1:
        k = choose(list(accounts.MODES), back="Batal")
        if k is not None:
            a.mode = accounts.MODES[k]
            accounts.save(accs)
            print(f"  [OK] Mode {a.nama} = {a.mode}. Nyalakan ulang bot Bybit.")
    if j is not None:
        pause()


def uses_account(source, name):
    return name.casefold() in {a.casefold() for a in source.account_names()}


def _bybit_account_busy(root, name):
    import sqlite3
    from contextlib import closing
    from pathlib import Path

    cfg = load_config(os.path.join(root, sopfiles.UNIVERSAL["bybit"]))
    path = Path(root, cfg["bot"]["jurnal"]).resolve()
    if not path.exists():
        return False
    states = (AWAITING, *ACTIVE)
    marks = ", ".join("?" for _ in states)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        fields = {row[1] for row in db.execute("PRAGMA table_info(setups)")}
        if not fields:
            return False
        clause = " AND (account = ? OR account = '')" if "account" in fields else ""
        args = (*states, name.casefold()) if clause else states
        return db.execute(f"SELECT 1 FROM setups WHERE status IN ({marks}){clause} LIMIT 1", args).fetchone() is not None


def _remove_account(accs, items):
    if not accs:
        print("  Belum ada akun.")
        pause()
        return
    i = choose([a.describe() for a in accs], back="Batal")
    if i is None:
        return
    a = accs[i]
    users = [s.kode for s in items if s.ke == "bybit" and uses_account(s, a.nama)]
    if users:
        print(f"\n  [X] Akun '{a.nama}' masih dipakai {', '.join(users)}. Ganti akun sumber itu dulu.")
    elif _bybit_account_busy(".", a.nama):
        print(f"\n  [X] Akun '{a.nama}' masih punya setup yang perlu dikelola.")
    elif ask(f"\n  Hapus akun '{a.nama}' dari akun-bybit.env? (y/n): ").lower() in ("y", "ya"):
        accounts.save([x for j, x in enumerate(accs) if j != i])
        print("  [OK] Dihapus.")
    pause()
