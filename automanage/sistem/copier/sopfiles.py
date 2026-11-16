"""Letak setelan sejak SOP dipisah per sumber:

    config.yaml           setelan UNIVERSAL MT5 (akun demo, nama simbol, magic, jurnal, pembaca)
    config-bybit.yaml     setelan UNIVERSAL Bybit (akun demo, batas nilai posisi, nama simbol)
    sumber.yaml           daftar sumber (kode, channel, topik, pengolah, tujuan, akun Bybit)
    sumber/S01.yaml       SOP satu sumber (risiko, TP, BE/trailing, entry, gerbang, pengaman, Claude)

Config yang dipakai bot untuk satu setup = bawaan + universal + SOP sumbernya.
`migrate()` mengubah susunan lama (semua SOP di config.yaml / config-bybit.yaml) ke susunan
ini sekali jalan, dengan cadangan file lama di data/cadangan-setelan-<waktu>/.
"""
import copy
import os
import shutil
import sqlite3
import time
from typing import List, Optional

import yaml

from . import accounts
from .config import DEFAULTS, _merge, load_config, with_defaults

SOP_DIR = "sumber"
ARCHIVE_DIR = os.path.join(SOP_DIR, "terhapus")
UNIVERSAL = {"mt5": "config.yaml", "bybit": "config-bybit.yaml"}
SOP_KEYS = ("risiko", "tp", "sop", "entry", "gerbang", "pengaman", "claude")


def _fmt(value) -> str:
    from panel.yamledit import fmt   # satu cara menulis nilai, sama dengan editor menu
    return fmt(value)


def _get(cfg, path):
    for k in path:
        if isinstance(cfg, dict) and k not in cfg and str(k).isdigit():
            k = int(k)
        cfg = cfg.get(k) if isinstance(cfg, dict) else None
    return cfg


def _render(spec, cfg: dict) -> str:
    """spec: baris teks (komentar/judul) atau (path, komentar). Nilai diambil dari cfg."""
    out = []
    for item in spec:
        if isinstance(item, str):
            out.append(item)
            continue
        path, note = item
        indent = "  " * (len(path) - 1)
        line = f"{indent}{path[-1]}: {_fmt(_get(cfg, path))}"
        out.append(line.ljust(30) + f"  # {note}" if note else line)
    return "\n".join(out) + "\n"


def _split_lines(cfg, bybit: bool):
    unit = "qty" if bybit else "lot"
    rows = ["  bagi:                       # partial: persen " + unit + " per TP, dari TP1; 0 = tanpa " + unit]
    for n in range(1, 6):
        rows.append(f"    {n}: {_fmt(list(_get(cfg, ('tp', 'bagi', n)) or []))}")
    return rows


def source_text(cfg: dict, ke: str, kode: str = "", nama: str = "") -> str:
    """Isi file sumber/<kode>.yaml (dengan komentar) dari nilai di cfg."""
    bybit = ke == "bybit"
    cfg = with_defaults(cfg)
    unit = "USDT" if bybit else "mata uang akun; akun cent otomatis x100"
    spec = [f"# SOP sumber {kode} ({nama}) -> {'Bybit' if bybit else 'MT5'}.",
            "# Ubah lewat MENU > Sumber sinyal. Bot membaca ulang file ini sendiri (tanpa restart).",
            "",
            "risiko:",
            (("risiko", "mode"), "persen = % dari saldo | dolar = nominal tetap per setup"),
            (("risiko", "persen"), "dipakai kalau mode: persen"),
            (("risiko", "dolar"), f"dipakai kalau mode: dolar ({unit})")]
    if not bybit:
        spec.append((("risiko", "lot_maks"), "batas atas total lot satu setup"))
    if bybit:
        spec += ["",
                 "bybit:",
                 (("bybit", "leverage_mode"), "maks = tertinggi koin itu | tetap = angka di bawah"),
                 (("bybit", "leverage"), "dipakai kalau leverage_mode: tetap")]
    spec += ["",
             "tp:",
             (("tp", "mode"), f"partial = {'qty' if bybit else 'lot'} dibagi ke tiap TP | satu = semua di satu TP"),
             (("tp", "satu_ke"), "mode satu: semua ke TP ke-berapa")]
    spec += _split_lines(cfg, bybit)
    spec += ["",
             "sop:",
             (("sop", "be_setelah_tp"), "SL ke BE setelah TP ke-berapa kena (0 = mati)"),
             (("sop", "be_jarak_aman_r"), "0.1 = BE dikunci 10% jarak SL di atas entry"),
             (("sop", "trailing"), "TP2 kena -> SL ke TP1, TP3 kena -> SL ke TP2, dst."),
             (("sop", "tutup_jika_sl_baru_terlewat"), "harga balik melewati SL baru -> tutup"),
             "",
             "entry:",
             (("entry", "kedaluwarsa"), "manual = jam SOP | sinyal_waktu = waktu sinyal | sinyal = waktu dan harga sinyal"),
             (("entry", "limit_kedaluwarsa_jam"), "mode manual atau sinyal tanpa batas waktu"),
             (("entry", "jika_harga_sudah_lewat"), "market | tolak"),
             "",
             "gerbang:",
             (("gerbang", "umur_maks_setup_menit"), "postingan lebih tua dari ini ditolak (basi)"),
             (("gerbang", "jarak_maks_dari_harga_persen"), "entry lebih jauh dari ini ditolak"),
             (("gerbang", "konfirmasi_wajib"), "tanya Ya/Tidak di Telegram sebelum eksekusi"),
             (("gerbang", "konfirmasi_peringatan"), "dalam mode otomatis, peringatan tetap minta Ya/Tidak"),
             (("gerbang", "izinkan_tebakan_otomatis"), "mode otomatis boleh jalan saat satu-satunya peringatan adalah Ragu"),
             (("gerbang", "konfirmasi_kedaluwarsa_menit"), "tidak dijawab -> batal"),
             (("gerbang", "dobel_jendela_jam"), "setup sama persis dalam jendela ini = dobel"),
             (("gerbang", "mirip_jarak_entry_persen"), "entry sedekat ini -> tanda mirip"),
             "",
             "pengaman:                     # dihitung untuk sumber ini saja",
             (("pengaman", "maks_setup_aktif"), "setup sumber ini yang jalan/menunggu fill"),
             (("pengaman", "loss_harian_persen"), "stop terima setup sumber ini kalau loss hari ini segini"),
             (("pengaman", "loss_harian_dolar"), "isi angka kalau mau batas nominal juga"),
             "",
             "claude:                       # dipakai kalau sumber diolah Claude",
             (("claude", "model_gambar"), "pesan bergambar: sonnet / opus / haiku"),
             (("claude", "model_teks"), "pesan teks saja: haiku paling hemat")]
    return _render(spec, cfg)


def _symbol_lines(cfg) -> List[str]:
    mapping = cfg.get("simbol") or {}
    if not mapping:
        return ["simbol: {}"]
    return ["simbol:                       # nama di setup -> nama di broker"] + \
        [f"  {_fmt(k)}: {_fmt(v)}" for k, v in mapping.items()]


def universal_text(cfg: dict, ke: str) -> str:
    if ke == "bybit" and not (cfg.get("bot") or {}).get("jurnal"):   # jangan sampai berbagi jurnal MT5
        cfg = _merge(cfg, {"bot": {"jurnal": "data/jurnal-bybit.db"}})
    cfg = with_defaults(cfg)
    if ke == "bybit":
        spec = ["# Setelan UNIVERSAL Bybit: berlaku untuk semua sumber yang ke Bybit.",
                "# SOP (risiko, leverage, TP, BE, pengaman) ada per sumber: MENU > Sumber sinyal.",
                "# Akun dan API key ada di akun-bybit.env (MENU > Setelan universal > Bybit).",
                "# Tanpa daftar pair: koin dicari dan dicocokkan sendiri di Bybit USDT Perpetual.",
                "",
                "akun:",
                (("akun", "hanya_demo"), "bot menolak akun Bybit mode live"),
                "",
                "bybit:",
                (("bybit", "nilai_posisi_maks_usdt"), "batas nilai satu posisi (qty x harga)"),
                "", "bot:",
                (("bot", "interval_detik"), "seberapa sering posisi dicek"),
                (("bot", "jurnal"), "")]
        return _render(spec, cfg)
    p = cfg["pembaca"]
    ranges = [f"    - {{simbol: {r['simbol']}, min: {r['min']}, max: {r['max']}, "
              f"alias: {_fmt(list(r.get('alias', [])))}}}" for r in p["simbol_dari_harga"]]
    spec = ["# Setelan UNIVERSAL MT5: berlaku untuk semua sumber yang ke MT5, plus bagian pembaca.",
            "# SOP (risiko, TP, BE, pengaman) ada per sumber: MENU > Sumber sinyal.",
            "",
            "akun:",
            (("akun", "hanya_demo"), "bot menolak jalan kalau MT5 login ke akun real"),
            ""] + _symbol_lines(cfg) + \
           ["", "mt5:",
            (("mt5", "magic"), "penanda order milik bot"),
            (("mt5", "deviasi_poin"), "toleransi slippage order market"),
            "",
            "bot:",
            (("bot", "interval_detik"), "seberapa sering posisi dicek"),
            (("bot", "jurnal"), ""),
            "",
            "pembaca:",
            "  simbol_dari_harga:           # setup tanpa nama simbol: ditebak dari besar harganya"] + ranges + \
           [(("pembaca", "kata_highrisk"), "kata yang membuat setup ditandai highrisk")]
    return _render(spec, cfg)


# --- membaca ---------------------------------------------------------------------
def sop_path(code: str, root: str = ".") -> str:
    return os.path.join(root, SOP_DIR, f"{code}.yaml")


def _yaml(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def effective(universal_path: str, code: str, root: str = ".") -> dict:
    """Config lengkap untuk satu sumber: bawaan + universal + SOP sumber (SOP menang)."""
    user = _merge(_yaml(universal_path), {k: v for k, v in _yaml(sop_path(code, root)).items()
                                          if k in SOP_KEYS or k == "bybit"})
    return with_defaults(user)


def signature(paths) -> tuple:
    """Penanda perubahan file (waktu ubah + ukuran), untuk membaca ulang SOP tanpa restart."""
    out = []
    for p in paths:
        try:
            st = os.stat(p)
            out.append((p, st.st_mtime_ns, st.st_size))
        except OSError:
            out.append((p, 0, 0))
    return tuple(out)


# --- membuat / menghapus -----------------------------------------------------------
def write_source(code: str, ke: str, nama: str, values: Optional[dict] = None, root: str = "."):
    os.makedirs(os.path.join(root, SOP_DIR), exist_ok=True)
    with open(sop_path(code, root), "w", encoding="utf-8") as f:
        f.write(source_text(values or {}, ke, code, nama))


def archive_source(code: str, root: str = "."):
    """SOP sumber yang dihapus dipindah ke sumber/terhapus/, bukan dibuang."""
    src = sop_path(code, root)
    if os.path.exists(src):
        os.makedirs(os.path.join(root, ARCHIVE_DIR), exist_ok=True)
        shutil.move(src, os.path.join(root, ARCHIVE_DIR, f"{code}-{time.strftime('%Y%m%d-%H%M%S')}.yaml"))


def new_code(items, root: str = ".") -> str:
    """Kode yang belum pernah dipakai, termasuk oleh sumber yang sudah dihapus (supaya setup
    lama di jurnal tidak ikut SOP sumber baru)."""
    from reader import sources
    used = [s.kode for s in items]
    for folder in (os.path.join(root, SOP_DIR), os.path.join(root, ARCHIVE_DIR)):
        if os.path.isdir(folder):
            used += [f.split(".")[0].split("-")[0].upper() for f in os.listdir(folder)]
    return sources.next_code([sources.Source(nama="x", channel="", kode=k) for k in used])


def sop_values(cfg: dict) -> dict:
    return {k: copy.deepcopy(cfg[k]) for k in SOP_KEYS + ("bybit",) if k in cfg}


# --- migrasi dari susunan lama -------------------------------------------------------
def _is_old(path: str, ke: str = "mt5") -> bool:
    data = _yaml(path)
    return any(k in data for k in ("risiko", "tp", "sop", "pengaman", "gerbang")) or \
        bool((data.get("pembaca") or {}).get("channel_sumber")) or (ke == "bybit" and "simbol" in data)


def migrate(root: str = ".", env_folder: Optional[str] = None) -> List[str]:
    """Ubah susunan lama ke SOP per sumber. Aman dijalankan berkali-kali. Mengembalikan
    daftar yang diubah (kosong kalau sudah rapi)."""
    from reader import sources

    done = []
    p = lambda name: os.path.join(root, name)
    if accounts.migrate_from_env(env_folder):
        done.append("akun Bybit dari .env dipindah ke akun-bybit.env (akun 'Utama')")
    acc = accounts.load(env_folder)

    old = {ke: load_config(p(f)) if os.path.exists(p(f)) else with_defaults({})
           for ke, f in UNIVERSAL.items()}
    for ke in old:   # model Claude lama ikut pindah ke SOP sumber
        old[ke]["claude"]["model_gambar"] = old["mt5"]["pembaca"].get("claude_model",
                                                                       DEFAULTS["claude"]["model_gambar"])

    path = p(sources.PATH)
    items = sources.load(path, old["mt5"])
    changed = not os.path.exists(path) and bool(items)
    for s in items:
        if not s.kode:
            s.kode = new_code(items, root)
            changed = True
        if s.ke == "bybit" and s.akun_tujuan is None and not s.akun and acc:
            s.akun = acc[0].nama
            changed = True
        if not os.path.exists(sop_path(s.kode, root)):
            write_source(s.kode, s.ke, s.nama, sop_values(old[s.ke]), root)
            done.append(f"SOP {s.kode} ({s.nama}, {sources.TUJUAN[s.ke]}) dibuat dari setelan lama")
    if changed:
        sources.save(items, path)
        done.append("sumber.yaml diberi kode sumber")

    backup = None
    for ke, f in UNIVERSAL.items():
        if os.path.exists(p(f)) and _is_old(p(f), ke):
            if backup is None:
                backup = p(os.path.join("data", f"cadangan-setelan-{time.strftime('%Y%m%d-%H%M%S')}"))
                os.makedirs(backup, exist_ok=True)
            shutil.copy2(p(f), os.path.join(backup, f))
            text = universal_text(_yaml(p(f)), ke)   # baca dulu, baru tulis
            with open(p(f), "w", encoding="utf-8") as fh:
                fh.write(text)
            done.append(f"{f} jadi setelan universal {'Bybit' if ke == 'bybit' else 'MT5'} "
                        f"(cadangan: {os.path.relpath(backup, root)})")

    for ke in UNIVERSAL:   # setup lama di jurnal: kode dari nama sumbernya
        jpath = p(old[ke]["bot"]["jurnal"])
        names = {s.nama.lower(): s.kode for s in items if s.ke == ke}
        if os.path.exists(jpath) and names:
            from .journal import Journal
            j = Journal(jpath)
            try:
                for nama, kode in names.items():
                    j.db.execute("UPDATE setups SET kode = ? WHERE (kode IS NULL OR kode = '') "
                                 "AND lower(source) = ?", (kode, nama))
                j.db.commit()
            except sqlite3.Error:
                pass
            finally:
                j.db.close()
    return done
