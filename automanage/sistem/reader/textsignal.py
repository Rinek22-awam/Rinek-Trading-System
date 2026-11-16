"""Postingan sinyal TEKS (format TraderAwam) -> SETUP. Tanpa Claude: aturan kode saja.

Contoh yang dikenali:

    🟢 LONG · STXUSDT (https://...)
    ...
    📍 Entry   0.3314   (limit)
    🎯 TP1   0.3518   +6.16% · 1.0R
    🎯 TP2   0.3722   +12.31% · 2.0R
    🛑 SL    0.311   -6.16%
    ⚡ Risk Medium

Format lama juga dikenali ("NEW CALL · LONG" lalu "ZROUSDT · ..." di baris berikutnya,
"ENTRY" dengan angka di baris bawahnya, "TP1 · 2.1269", "SL · 1.69"), juga koin dengan
tanda dolar tanpa USDT ("🟢 LONG $PEPE (https://...symbol=BYBIT:1000PEPEUSDT.P)").
Kalau ada link TradingView, simbol diambil dari link itu (paling tepat, termasuk koin
berkelipatan seperti 1000PEPEUSDT). Masa berlaku: "Valid s/d", "Berlaku sampai", atau "⏳ s/d".

Yang WAJIB ada: arah + pair ("LONG · XXXUSDT" / "SHORT · XXXUSDT"), Entry,
SL, dan minimal satu TP. Untuk tiap TP/SL/Entry dipakai kemunculan PERTAMA, karena teks
analisa di bawahnya juga menyebut angka-angka yang sama ("TP1 2.1269: high impuls"). Postingan lain dari sumber yang sama (rekap/watcher, "Entry
filled", obrolan) tidak memenuhi syarat itu, jadi diam.
Manajemen dari sumber (partial, BE, trailing) sengaja tidak dibaca: bot memakai SOP sendiri.
"""
import re
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, Tuple

from copier.setup_format import WIB, parse_number, parse_price_limit

_NUM = r"(\d[\d.,]*)"
_SEP = r"[ \t]*[:=·•]?[ \t]*"
# Arah lalu pair, satu baris ("LONG · STXUSDT") atau baris berikutnya ("NEW CALL · LONG\nZROUSDT")
_HEAD = re.compile(r"\b(LONG|SHORT)\b[\s·•|\-–*\[]*(?:([A-Z0-9]{2,20}USDT)|\$([A-Z0-9]{2,20}?))(?:USDT)?(?:\.P)?\b",
                   re.I)
# Link TradingView di sinyal: "...?symbol=BYBIT:1000PEPEUSDT.P" -> 1000PEPEUSDT
_LINK = re.compile(r"symbol=(?:[A-Z]+:)?([A-Z0-9]{2,30}USDT)(?:\.P)?\b", re.I)
# "Entry 0.3314 (limit)"  atau  "ENTRY\n1.9 · LIMIT di POI"
_ENTRY = re.compile(r"^[^\w\n]*ENTRY\b[ \t]*[:=·•]?\s*" + _NUM +
                    r"(?:[ \t]*[·•(]?[ \t]*(LIMIT|MARKET|STOP)\b)?", re.I | re.M)
_SL = re.compile(r"^[^\w\n]*SL\b" + _SEP + _NUM, re.I | re.M)                 # "SL 0.311" / "SL · 1.69"
_TP = re.compile(r"^[^\w\n]*TP[ \t]*(\d)\b" + _SEP + _NUM, re.I | re.M)      # "TP1 0.35" / "TP1 · 2.12"
_HIGH = re.compile(r"\bRISK\s+(HIGH|TINGGI)\b", re.I)
# Notifikasi per koin: "TraderAwam · STXUSDT · LONG ... Setup dibatalkan"
_NOTICE = re.compile(r"TraderAwam\s*·\s*\$?([A-Z0-9]{2,20}USDT)\s*·\s*(LONG|SHORT)", re.I)
_CANCEL_WORDS = ("dibatalkan", "waktu entry habis", "batal")
_SL_HIT = re.compile(r"^[^\w\n]*SL[ \t]+hit\b", re.I | re.M)
# Masa berlaku: "Valid s/d 11 Nov 01:08 WIB" (tanpa tahun) / "Berlaku sampai 05 Oct 2026 00:13 WIB"
_VALID = re.compile(r"(?:VALID\s+S/D|BERLAKU\s+SAMPAI|(?<![A-Z])S/D)\s+(\d{1,2})\s+([A-Z]{3,9})\.?\s+(?:(\d{4})\s+)?"
                    r"(\d{1,2})[:.](\d{2})", re.I)
_VALID_LABEL = re.compile(r"⏳[ \t]*S/D\b|\b(?:VALID[ \t]+S/D|BERLAKU[ \t]+SAMPAI)\b", re.I)
_BEFORE_ENTRY = re.compile(r"\bbatal[ \t]+jika[ \t]+([^\n]+?)[ \t]+sebelum[ \t]+entry\b", re.I)
_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "mei": 5, "jun": 6, "jul": 7,
           "aug": 8, "agu": 8, "agt": 8, "sep": 9, "oct": 10, "okt": 10, "nov": 11, "dec": 12, "des": 12}


def parse_valid_until(text: str, now: Optional[datetime] = None) -> Optional[datetime]:
    """Masa berlaku sinyal (jam WIB). Tahun yang tidak ditulis = tahun ini, atau tahun depan
    kalau tanggalnya sudah lewat jauh (misal sinyal 30 Des berlaku s/d 2 Jan)."""
    m = _VALID.search(text or "")
    if not m:
        return None
    day, mon, year, hour, minute = m.groups()
    month = _MONTHS.get(mon[:3].lower())
    if not month:
        return None
    now = now or datetime.now(WIB)
    y = int(year) if year else now.year
    try:
        dt = datetime(y, month, int(day), int(hour), int(minute), tzinfo=WIB)
    except ValueError:
        return None
    if not year and dt < now - timedelta(days=180):
        dt = dt.replace(year=y + 1)
    return dt


def _fmt(v: float) -> str:
    return f"{v:.8f}".rstrip("0").rstrip(".")


def parse_text_signal(text: str) -> Tuple[Optional[dict], str]:
    """Hasil: (sinyal, "") atau (None, alasan). Alasan kosong = memang bukan sinyal."""
    text = (text or "").replace("`", "")
    head = _HEAD.search(text)
    entry_m, sl_m = _ENTRY.search(text), _SL.search(text)
    tps_m = _TP.findall(text)
    if not head:
        return None, ""
    missing = [label for label, match in (("Entry", entry_m), ("SL", sl_m), ("TP", tps_m)) if not match]
    if missing:
        if len(missing) == 1:
            return None, f"{missing[0]} hilang atau formatnya tidak terbaca"
        return None, ""
    side = "BUY" if head.group(1).upper() == "LONG" else "SELL"
    entry, sl = parse_number(entry_m.group(1)), parse_number(sl_m.group(1))
    first = {}   # hanya kemunculan pertama tiap TP; baris statistik "TP1 3 · TP2 0" di bawah diabaikan
    for idx, v in tps_m:
        first.setdefault(int(idx), v)
    tps = [parse_number(first[i]) for i in sorted(first)]
    if not entry or not sl or not all(tps):
        return None, "angka entry/SL/TP tidak terbaca"
    buy = side == "BUY"
    if (sl >= entry) if buy else (sl <= entry):
        return None, "SL ada di sisi yang salah dari entry"
    if any((t <= entry) if buy else (t >= entry) for t in tps):
        return None, "ada TP di sisi yang salah dari entry"
    link = _LINK.search(text)
    symbol = (link.group(1) if link else head.group(2) or head.group(3)).upper()
    valid_until = parse_valid_until(text)
    if valid_until is None and _VALID_LABEL.search(text):
        return None, "batas waktu sinyal tidak terbaca"
    before_entry = _BEFORE_ENTRY.search(text)
    cancel_before_entry = parse_price_limit(before_entry.group(1)) if before_entry else None
    if before_entry and cancel_before_entry is None:
        return None, "batas harga sebelum entry harus seperti < 0.71 atau > 0.90"
    return {"symbol": symbol, "side": side, "entry": entry, "sl": sl,
            "tps": sorted(set(tps), reverse=not buy),
            "order": (entry_m.group(2) or "LIMIT").upper(),
            "highrisk": bool(_HIGH.search(text)),
            "valid_until": valid_until, "cancel_before_entry": cancel_before_entry}, ""


def parse_notice(text: str) -> Optional[dict]:
    """Notifikasi sumber untuk satu koin -> {"symbol", "side", "action"}; None kalau bukan
    notifikasi yang perlu tombol. CANCEL: setup dibatalkan / waktu entry habis.
    CLOSE: SL tersentuh / SL hit. "Entry filled" dan lainnya: None (bot tahu sendiri dari bursa)."""
    m = _NOTICE.search(text or "")
    if not m:
        return None
    low = text.lower()
    if "sl tersentuh" in low or _SL_HIT.search(text):
        action = "CLOSE"
    elif any(w in low for w in _CANCEL_WORDS):
        action = "CANCEL"
    else:
        return None
    return {"symbol": m.group(1).upper(), "side": "BUY" if m.group(2).upper() == "LONG" else "SELL",
            "action": action}


def build_notice_update(text: str, source: str, dest: str) -> Optional[str]:
    n = parse_notice(text)
    if n is None:
        return None
    flat = " ".join(text.split())
    return (f"UPDATE\nSumber: {source}\nKe: {dest}\nPair: {n['symbol']}\nAksi: {n['action']}\n"
            f"Teks: {flat[:500]}")


def build_text_setup(text: str, source: str, dest: str) -> Tuple[Optional[str], str]:
    """Postingan teks -> teks SETUP berlabel tujuan. (None, alasan) kalau tidak jadi."""
    sig, why = parse_text_signal(text)
    if sig is None:
        return None, why
    lines = ["SETUP", f"Pair: {sig['symbol']}", f"Arah: {sig['side']}", f"Order: {sig['order']}",
             f"Entry: {_fmt(sig['entry'])}", f"SL: {_fmt(sig['sl'])}"]
    lines += [f"TP{i + 1}: {_fmt(t)}" for i, t in enumerate(sig["tps"])]
    if sig["highrisk"]:
        lines.append("Highrisk: ya")
    if sig["valid_until"]:
        lines.append(f"Berlaku: {sig['valid_until']:%Y-%m-%d %H:%M} WIB")
    if sig["cancel_before_entry"]:
        op, level = sig["cancel_before_entry"]
        lines.append(f"Batal sebelum entry: {op} {Decimal(str(level)):f}")
    lines += [f"Ke: {dest}", f"Sumber: {source}"]
    return "\n".join(lines), ""
