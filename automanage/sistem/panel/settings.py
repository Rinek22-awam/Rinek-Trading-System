"""SOP settings screen: every number in config.yaml (MT5) or config-bybit.yaml (Bybit),
editable without opening the file."""
import os

import yaml

from copier.config import (DEFAULTS, EXPIRY_MANUAL, EXPIRY_MODES, EXPIRY_SIGNAL_TIME,
                           EXPIRY_SIGNAL_TIME_PRICE, with_defaults)
from copier.setup_format import parse_number

from .ui import ask, choose, clear, pause, title
from .yamledit import add_value, remove_value, set_value

# (path, label, kind, options, note)
# kind: float | int | bool | choice | text | float_or_null | split
SECTIONS = [
    ("Risiko", [
        (("risiko", "mode"), "Mode risiko", "choice", ["persen", "dolar"],
         "persen = % dari saldo, dolar = nominal tetap per setup"),
        (("risiko", "persen"), "Risiko per setup (% saldo)", "float", (0.01, 20), "dipakai kalau mode persen"),
        (("risiko", "dolar"), "Risiko per setup ($)", "float", (0.01, 1e6),
         "dipakai kalau mode dolar; di akun cent otomatis x100"),
        (("risiko", "lot_maks"), "Lot maksimal per setup", "float", (0.01, 1e4), ""),
    ]),
    ("Pembagian lot per TP", [
        (("tp", "mode"), "Mode TP", "choice", ["partial", "satu"],
         "partial = lot dibagi per TP (baris di bawah), satu = semua lot di satu TP. "
         "BE dan trailing tetap jalan menurut harga"),
        (("tp", "satu_ke"), "Mode satu: semua lot ke TP ke-", "int", (1, 10),
         "setup dengan TP lebih sedikit -> TP terakhir"),
    ] + [
        (("tp", "bagi", str(n)), f"Partial: setup dengan {n} TP", "split", n,
         "persen lot tiap TP, dari TP1; 0 = tanpa lot di TP itu")
        for n in range(1, 6)
    ]),
    ("SOP posisi (BE dan trailing)", [
        (("sop", "be_setelah_tp"), "SL ke BE setelah TP ke-", "int", (0, 10), "0 = BE mati"),
        (("sop", "be_jarak_aman_r"), "Jarak aman BE (x jarak SL)", "float", (0, 1),
         "0.1 = SL dikunci 10% jarak SL di atas entry"),
        (("sop", "trailing"), "Trailing bertingkat", "bool", None, "TP2 kena -> SL ke TP1, dst."),
        (("sop", "tutup_jika_sl_baru_terlewat"), "Tutup kalau harga sudah lewat SL baru", "bool", None,
         "harga balik sebelum SL sempat digeser"),
    ]),
    ("Entry", [
        (("entry", "limit_kedaluwarsa_jam"), "Limit dibatalkan setelah (jam)", "float", (0.1, 720), ""),
        (("entry", "jika_harga_sudah_lewat"), "Harga sudah lebih bagus dari entry", "choice",
         ["market", "tolak"], "market = langsung masuk, tolak = setup ditolak"),
    ]),
    ("Gerbang (cek sebelum eksekusi)", [
        (("gerbang", "konfirmasi_wajib"), "Wajib konfirmasi Ya/Tidak", "bool", None, ""),
        (("gerbang", "konfirmasi_peringatan"), "Konfirmasi jika ada peringatan (mode otomatis)", "bool", None, ""),
        (("gerbang", "izinkan_tebakan_otomatis"), "Izinkan tebakan Claude tanpa konfirmasi", "bool", None,
         "Berlaku saat Wajib konfirmasi mati. Tanda Ragu tetap tampil, termasuk SL hasil tebakan."),
        (("gerbang", "konfirmasi_kedaluwarsa_menit"), "Konfirmasi kedaluwarsa (menit)", "int", (1, 1440), ""),
        (("gerbang", "umur_maks_setup_menit"), "Setup dianggap basi setelah (menit)", "int", (1, 1440), ""),
        (("gerbang", "jarak_maks_dari_harga_persen"), "Entry maks sejauh (% dari harga)", "float", (0.01, 50), ""),
        (("gerbang", "dobel_jendela_jam"), "Cek dobel dalam (jam)", "float", (0, 720), ""),
        (("gerbang", "mirip_jarak_entry_persen"), "Dianggap mirip kalau entry sedekat (%)", "float", (0, 10), ""),
    ]),
    ("Pengaman", [
        (("pengaman", "maks_setup_aktif"), "Maksimal setup aktif", "int", (1, 50), ""),
        (("pengaman", "loss_harian_persen"), "Stop di loss harian (%)", "float", (0, 100), "0 = mati"),
        (("pengaman", "loss_harian_dolar"), "Stop di loss harian ($)", "float_or_null", (0.01, 1e6),
         "kosong = mati"),
        (("akun", "hanya_demo"), "Hanya boleh akun demo", "bool", None,
         "matikan HANYA saat siap live (akun cent)"),
    ]),
    ("Pembaca sinyal", [
        (("pembaca", "nama_sumber"), "Nama sumber (untuk jurnal)", "text", None, ""),
        (("pembaca", "claude_model"), "Model Claude pembaca gambar", "choice", ["sonnet", "opus", "haiku"],
         "sonnet = seimbang, opus = paling teliti, haiku = paling hemat kuota"),
    ]),
]
SPLIT_SECTION = "Pembagian lot per TP"
RESET_WORD = "bawaan"
_EXPIRY_PATH = ("entry", "kedaluwarsa")
_EXPIRY_LABELS = {
    EXPIRY_MANUAL: "Manual (jam SOP)",
    EXPIRY_SIGNAL_TIME: "Ikut sinyal (waktu saja)",
    EXPIRY_SIGNAL_TIME_PRICE: "Ikut sinyal (waktu dan batas harga)",
}
_ENTRY_SIGNAL = (_EXPIRY_PATH, "Mode pembatalan setup", "choice", EXPIRY_MODES,
                 "Mode ikut sinyal memakai jam SOP kalau sinyal tidak mencantumkan batas waktu.")


def _bybit_sections():
    """Pengaturan SOP Bybit: sama dengan MT5, tapi "lot" = koin, ada leverage dan batas nilai
    posisi, mode pembatalan setup, tanpa bagian pembaca."""
    by = {name: list(items) for name, items in SECTIONS}
    risk = [it for it in by["Risiko"] if it[0] != ("risiko", "lot_maks")]
    risk[2] = (("risiko", "dolar"), "Risiko per setup (USDT)", "float", (0.01, 1e6),
               "dipakai kalau mode dolar")
    risk += [(("bybit", "leverage_mode"), "Leverage", "choice", ["maks", "tetap"],
              "maks = tertinggi untuk koin itu (beda tiap koin); risiko tetap dari jarak SL, "
              "leverage hanya mengatur margin"),
             (("bybit", "leverage"), "Leverage tetap (x)", "int", (1, 200), "dipakai kalau mode tetap"),
             (("bybit", "nilai_posisi_maks_usdt"), "Nilai posisi maksimal (USDT)", "float", (1, 1e7),
              "qty x harga satu setup tidak lebih dari ini")]
    split = [(p, label.replace("lot", "qty"), k, o, n.replace("lot", "qty"))
             for p, label, k, o, n in by[SPLIT_SECTION]]
    entry = [_ENTRY_SIGNAL] + by["Entry"]
    guard = [(p, label, k, o, n) for p, label, k, o, n in by["Pengaman"] if p != ("akun", "hanya_demo")]
    guard += [(("akun", "hanya_demo"), "Hanya boleh demo/testnet", "bool", None,
               "matikan HANYA saat siap live (BYBIT_MODE=live)")]
    guard = [(p, label.replace("($)", "(USDT)"), k, o, n) for p, label, k, o, n in guard]
    return [("Risiko dan leverage", risk), ("Pembagian qty per TP", split),
            ("SOP posisi (BE dan trailing)", by["SOP posisi (BE dan trailing)"]),
            ("Entry", entry), ("Gerbang (cek sebelum eksekusi)", by["Gerbang (cek sebelum eksekusi)"]),
            ("Pengaman", guard)]


BYBIT_SECTIONS = _bybit_sections()
SPLIT_SECTIONS = (SPLIT_SECTION, "Pembagian qty per TP")

# --- sejak SOP per sumber -------------------------------------------------------------
_CLAUDE = ("Model Claude (sumber diolah Claude)", [
    (("claude", "model_gambar"), "Model untuk pesan bergambar", "choice", ["sonnet", "opus", "haiku"],
     "sonnet = seimbang, opus = paling teliti, haiku = paling hemat kuota"),
    (("claude", "model_teks"), "Model untuk pesan teks saja", "choice", ["haiku", "sonnet", "opus"],
     "haiku = paling hemat; teks biasanya cukup"),
])
def _source_sections(bybit: bool):
    """SOP satu sumber: semua yang dulu di Pengaturan SOP, tanpa hal-hal akun (universal)."""
    if bybit:
        out = []
        for name, items in BYBIT_SECTIONS:
            items = [it for it in items if it[0] not in (("bybit", "nilai_posisi_maks_usdt"),
                                                         ("akun", "hanya_demo"))]
            out.append((name, items))
    else:
        by = dict(SECTIONS)
        entry = [_ENTRY_SIGNAL] + by["Entry"]
        guard = [it for it in by["Pengaman"] if it[0] != ("akun", "hanya_demo")]
        out = [("Risiko", by["Risiko"]), (SPLIT_SECTION, by[SPLIT_SECTION]),
               ("SOP posisi (BE dan trailing)", by["SOP posisi (BE dan trailing)"]),
               ("Entry", entry), ("Gerbang (cek sebelum eksekusi)", by["Gerbang (cek sebelum eksekusi)"]),
               ("Pengaman", guard)]
    out = [(("Pengaman (untuk sumber ini saja)" if n == "Pengaman" else n), items) for n, items in out]
    return out + [_CLAUDE]


SOURCE_SECTIONS = _source_sections(False)
SOURCE_SECTIONS_BYBIT = _source_sections(True)
UNIVERSAL_SECTIONS = [
    ("Akun", [(("akun", "hanya_demo"), "Hanya boleh akun demo", "bool", None,
               "matikan HANYA saat siap live (akun cent)")]),
    ("MT5", [(("mt5", "magic"), "Magic number (penanda order bot)", "int", (1, 2_000_000_000), ""),
             (("mt5", "deviasi_poin"), "Toleransi slippage market (poin)", "int", (0, 10_000), "")]),
    ("Bot", [(("bot", "interval_detik"), "Posisi dicek tiap (detik)", "float", (0.5, 60), "")]),
]
UNIVERSAL_SECTIONS_BYBIT = [
    ("Akun", [(("akun", "hanya_demo"), "Hanya boleh demo/testnet", "bool", None,
               "matikan HANYA saat siap live (akun Bybit mode live)")]),
    ("Batas posisi", [(("bybit", "nilai_posisi_maks_usdt"), "Nilai posisi maksimal (USDT)", "float",
                       (1, 1e7), "qty x harga satu setup tidak lebih dari ini, untuk semua sumber")]),
    ("Bot", [(("bot", "interval_detik"), "Posisi dicek tiap (detik)", "float", (0.5, 60), "")]),
]


def _get(cfg, path):
    for k in path:
        if isinstance(cfg, dict) and k not in cfg and k.isdigit():
            k = int(k)   # tp.bagi is keyed by number after loading
        cfg = cfg.get(k) if isinstance(cfg, dict) else None
    return cfg


def show(value) -> str:
    if isinstance(value, bool):
        return "ya" if value else "tidak"
    if value is None:
        return "-"
    if isinstance(value, list):
        return " / ".join(f"{v:g}" if isinstance(v, (int, float)) else str(v) for v in value)
    return f"{value:g}" if isinstance(value, float) else str(value)


def parse(kind, options, raw: str):
    """User text -> value. Raises ValueError with a message in Indonesian."""
    raw = raw.strip()
    if kind == "bool":
        if raw.lower() in ("y", "ya", "1", "on", "true"):
            return True
        if raw.lower() in ("t", "tidak", "n", "0", "off", "false"):
            return False
        raise ValueError("jawab y (ya) atau t (tidak)")
    if kind == "choice":
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        if raw.lower() in options:
            return raw.lower()
        raise ValueError("pilih salah satu: " + ", ".join(options))
    if kind == "text":
        if not raw:
            raise ValueError("tidak boleh kosong")
        return raw
    if kind == "split":
        parts = [parse_number(x) for x in raw.replace("/", " ").replace(";", " ").split()]
        if len(parts) != options or any(p is None or p < 0 for p in parts) or not any(parts):
            raise ValueError(f"isi {options} angka (0 boleh, minimal satu di atas 0), misal: " +
                             " ".join(["50", "30", "20", "10", "5"][:options]))
        return [int(p) if p == int(p) else p for p in parts]
    if kind == "float_or_null" and raw.lower() in ("", "0", "-", "mati", "kosong"):
        return None
    v = parse_number(raw)
    if v is None:
        raise ValueError("harus angka")
    lo, hi = options
    if not lo <= v <= hi:
        raise ValueError(f"harus antara {lo:g} dan {hi:g}")
    return int(v) if kind == "int" else v


class Settings:
    def __init__(self, path="config.yaml", bot_running=lambda: False, bybit=False, sections=None,
                 heading=None, symbols=True, live=False, extra=()):
        """sections: daftar bagian yang ditampilkan (bawaan: SOP lama MT5/Bybit). heading: judul
        layar. symbols: tampilkan menu nama simbol. live: bot membaca ulang file ini sendiri.
        extra: [(label, fungsi)] menu tambahan di bawah."""
        self.path, self.bot_running, self.bybit = path, bot_running, bybit
        self.extra = list(extra)
        self.sections = sections or (BYBIT_SECTIONS if bybit else SECTIONS)
        self.name = "Bybit" if bybit else "MT5"
        self.heading = heading or f"PENGATURAN SOP {self.name.upper()}"
        self.show_symbols, self.live = symbols, live

    def _saved_note(self):
        if self.live:
            print("  Bot membaca perubahan ini sendiri dalam beberapa detik (tidak perlu restart).")
        elif self.bot_running():
            print("  Bot sedang jalan: perubahan berlaku setelah bot dinyalakan ulang.")

    def load_text(self):
        with open(self.path, encoding="utf-8") as f:
            return f.read()

    def cfg(self):
        return with_defaults(yaml.safe_load(self.load_text()) or {})

    def save(self, text: str):
        with_defaults(yaml.safe_load(text) or {})   # refuse to write a config the bot cannot load
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def set(self, path, value):
        text = self.load_text()
        try:
            text = set_value(text, path, value)
        except KeyError:
            if tuple(path) not in (_EXPIRY_PATH, ("gerbang", "konfirmasi_peringatan"),
                                   ("gerbang", "izinkan_tebakan_otomatis")):
                raise
            text = add_value(text, path[:-1], path[-1], value)
        self.save(text)

    # --- screens ------------------------------------------------------------
    def run(self):
        while True:
            clear()
            title(self.heading)
            cfg = self.cfg()
            print(f"  File: {os.path.relpath(self.path)}")
            for line in self.summary(cfg):
                print(f"  {line}")
            print()
            names = [s for s, _ in self.sections] + \
                ([f"Nama simbol (pair) di {self.name}"] if self.show_symbols else []) + \
                [label for label, _ in self.extra]
            pick = choose(names, back="Kembali")
            if pick is None:
                return
            n = len(self.sections)
            if pick < n:
                self.section(*self.sections[pick])
            elif self.show_symbols and pick == n:
                self.symbols()
            else:
                self.extra[pick - n - (1 if self.show_symbols else 0)][1]()

    def summary(self, cfg) -> list:
        paths = {p for _, items in self.sections for p, *_ in items}
        lines = []
        if ("risiko", "mode") in paths:
            r = cfg["risiko"]
            unit = "USDT" if self.bybit else "$"
            lines.append(f"Risiko sekarang: {r['persen']}% saldo" if r["mode"] == "persen"
                         else f"Risiko sekarang: {r['dolar']} {unit} per setup")
        if self.bybit and ("bybit", "leverage_mode") in paths:
            by = cfg["bybit"]
            lev = "maks per koin" if by["leverage_mode"] == "maks" else f"tetap {by['leverage']}x"
            lines.append(f"Leverage {lev}")
        if _EXPIRY_PATH in paths:
            e = cfg["entry"]
            lines.append(f"Pembatalan setup: {_EXPIRY_LABELS[e['kedaluwarsa']]} · SOP {e['limit_kedaluwarsa_jam']:g} jam")
        if ("akun", "hanya_demo") in paths:
            lines.append(f"Akun: {'HANYA DEMO' if cfg['akun']['hanya_demo'] else 'demo & REAL boleh'}")
        return lines

    @staticmethod
    def _display_value(path, value):
        return _EXPIRY_LABELS[value] if path == _EXPIRY_PATH else show(value)

    def section(self, name, items):
        while True:
            clear()
            title(name.upper())
            cfg = self.cfg()
            rows = [f"{label:<42} {self._display_value(path, _get(cfg, path))}" for path, label, *_ in items]
            splits = [it for it in items if it[2] == "split"]
            if name in SPLIT_SECTIONS:
                rows.append("↺ Kembalikan semua pembagian partial ke bawaan (" +
                            ", ".join(show(_get(DEFAULTS, p)) for p, *_ in splits) + ")")
            pick = choose(rows, back="Kembali")
            if pick is None:
                return
            if pick == len(items):
                self.reset_all(splits)
            else:
                self.edit(*items[pick])

    def reset_all(self, items):
        if ask("\n  Kembalikan semua pembagian partial ke bawaan? (y/t): ").lower() not in ("y", "ya"):
            return
        for path, *_ in items:
            self.set(path, _get(DEFAULTS, path))
        print("\n  Tersimpan: semua pembagian partial kembali ke bawaan.")
        self._saved_note()
        pause()

    def edit(self, path, label, kind, options, note):
        current = _get(self.cfg(), path)
        print(f"\n  {label}: sekarang {self._display_value(path, current)}")
        if note:
            print(f"  ({note})")
        if kind == "choice":
            for i, o in enumerate(options, 1):
                print(f"    {i}. {self._display_value(path, o)}")
        hint = {"bool": " (y/t)", "choice": " (nomor)", "float_or_null": " (kosong = mati)",
                "split": f" ({RESET_WORD} = {show(_get(DEFAULTS, path))})"}.get(kind, "")
        raw = ask(f"  Nilai baru{hint}, Enter saja = batal: ")
        if raw == "" and kind != "float_or_null":
            return
        if raw == "" and kind == "float_or_null" and current is None:
            return
        try:
            value = _get(DEFAULTS, path) if kind == "split" and raw.lower() == RESET_WORD \
                else parse(kind, options, raw)
        except ValueError as e:
            print(f"\n  [X] {e}. Tidak diubah.")
            pause()
            return
        if path == ("akun", "hanya_demo") and value is False:
            if ask("  Yakin? Bot akan boleh trading di akun REAL. Ketik REAL untuk lanjut: ") != "REAL":
                print("  Dibatalkan.")
                pause()
                return
        self.set(path, value)
        print(f"\n  Tersimpan: {label} = {show(value)}")
        self._saved_note()
        pause()

    def symbols(self):
        while True:
            clear()
            title(f"NAMA SIMBOL DI {self.name.upper()}")
            if self.bybit:
                print("  Nama di setup -> nama di Bybit USDT Perpetual. Nama ...USDT (misal STXUSDT)")
                print("  tidak perlu didaftar: dipakai apa adanya.\n")
            else:
                print("  Nama di setup -> nama di MT5 (cek di Market Watch; akun cent pakai akhiran c)\n")
            mapping = self.cfg()["simbol"]
            keys = list(mapping)
            extra = ["+ Tambah simbol"] + ([] if self.bybit else ["Cocokkan otomatis dengan broker di MT5"])
            pick = choose([f"{k:<10} -> {v}" for k, v in mapping.items()] + extra, back="Kembali")
            if pick is None:
                return
            if pick == len(keys) + 1 and not self.bybit:
                from .symbols import match_screen
                match_screen(self)
                continue
            if pick == len(keys):
                k = ask("  Nama di setup (misal XAUUSD): ").upper()
                v = ask(f"  Nama di {self.name} (misal {'BTCUSDT' if self.bybit else 'XAUUSDc'}): ")
                if k and v:
                    self.save(add_value(self.load_text(), ("simbol",), k, v))
                continue
            k = keys[pick]
            v = ask(f"  {k}: nama baru di {self.name} (Enter = batal, HAPUS = hapus): ")
            if v == "HAPUS":
                self.save(remove_value(self.load_text(), ("simbol", k)))
            elif v:
                self.set(("simbol", k), v)
