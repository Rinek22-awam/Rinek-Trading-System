"""Load config.yaml on top of safe defaults, so a missing key never crashes the bot."""
import copy

import yaml

EXPIRY_MANUAL = "manual"
EXPIRY_SIGNAL_TIME = "sinyal_waktu"
EXPIRY_SIGNAL_TIME_PRICE = "sinyal"  # pertahankan perilaku SOP sinyal yang sudah ada
EXPIRY_MODES = (EXPIRY_MANUAL, EXPIRY_SIGNAL_TIME, EXPIRY_SIGNAL_TIME_PRICE)

DEFAULTS = {
    "akun": {"hanya_demo": True},
    "risiko": {"mode": "persen", "persen": 1.0, "dolar": 10.0, "lot_maks": 5.0},
    "tp": {"mode": "partial", "satu_ke": 1,
           "bagi": {1: [100], 2: [50, 50], 3: [50, 30, 20],
                    4: [40, 30, 20, 10], 5: [30, 25, 20, 15, 10]}},
    "sop": {"be_setelah_tp": 1, "be_jarak_aman_r": 0.1, "trailing": True,
            "tutup_jika_sl_baru_terlewat": True},
    "entry": {"tipe_default": "LIMIT", "kedaluwarsa": EXPIRY_MANUAL, "limit_kedaluwarsa_jam": 12,
              "jika_harga_sudah_lewat": "market"},
    "gerbang": {"umur_maks_setup_menit": 30, "jarak_maks_dari_harga_persen": 1.0,
                 "konfirmasi_wajib": True, "konfirmasi_peringatan": True, "izinkan_tebakan_otomatis": False,
                 "konfirmasi_kedaluwarsa_menit": 30,
                "dobel_jendela_jam": 24, "mirip_jarak_entry_persen": 0.15},
    "pengaman": {"maks_setup_aktif": 3, "loss_harian_persen": 3.0, "loss_harian_dolar": None},
    "simbol": {},
    "mt5": {"magic": 260101, "deviasi_poin": 20},
    "bybit": {"leverage_mode": "maks", "leverage": 10, "nilai_posisi_maks_usdt": 1000},
    "bot": {"interval_detik": 2, "jurnal": "data/jurnal.db"},
    "claude": {"model_gambar": "sonnet", "model_teks": "haiku"},   # per sumber (pengolah Claude)
    "pembaca": {
        "channel_sumber": "", "topik_sumber": None, "nama_sumber": "sumber",
        "claude_model": "sonnet",
        "simbol_dari_harga": [
            {"simbol": "XAUUSD", "min": 1000, "max": 10000, "alias": ["GOLD", "XAU"]},
            {"simbol": "BTCUSD", "min": 10000, "max": 1000000, "alias": ["BTC", "BTCUSDT"]}],
        "kata_highrisk": ["high risk", "highrisk", "hi risk", "risiko tinggi", "resiko tinggi"],
        "kata_cancel": ["cancel", "canceled", "cancelled", "batal", "dibatalkan"],
        "kata_close": ["close", "close now", "tutup", "exit", "cut"],
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "bagi":
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str = "config.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        user = yaml.safe_load(f) or {}
    return with_defaults(user)


def with_defaults(user: dict) -> dict:
    cfg = _merge(DEFAULTS, user)
    cfg["tp"]["bagi"] = {int(k): list(v) for k, v in cfg["tp"]["bagi"].items()}
    cfg["simbol"] = {str(k).upper(): str(v) for k, v in (cfg.get("simbol") or {}).items()}
    mode = cfg["risiko"]["mode"]
    if mode not in ("persen", "dolar"):
        raise ValueError(f"risiko.mode harus 'persen' atau 'dolar', bukan {mode!r}")
    exp = cfg["entry"]["kedaluwarsa"]
    if exp not in EXPIRY_MODES:
        raise ValueError(f"entry.kedaluwarsa harus salah satu dari {', '.join(EXPIRY_MODES)}, bukan {exp!r}")
    lm = cfg["bybit"]["leverage_mode"]
    if lm not in ("maks", "tetap"):
        raise ValueError(f"bybit.leverage_mode harus 'maks' atau 'tetap', bukan {lm!r}")
    tp = cfg["tp"]
    if tp["mode"] not in ("partial", "satu"):
        raise ValueError(f"tp.mode harus 'partial' atau 'satu', bukan {tp['mode']!r}")
    if not isinstance(tp["satu_ke"], int) or tp["satu_ke"] < 1:
        raise ValueError(f"tp.satu_ke harus angka 1 atau lebih, bukan {tp['satu_ke']!r}")
    for key in ("konfirmasi_peringatan", "izinkan_tebakan_otomatis"):
        if not isinstance(cfg["gerbang"][key], bool):
            raise ValueError(f"gerbang.{key} harus true atau false")
    return cfg
