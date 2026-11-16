"""Daftar sumber sinyal. Disimpan di sumber.yaml, diatur lewat MENU > Sumber sinyal.
File ini tidak ikut ke git.

Satu sumber = satu channel/grup + aturan topik + pengolah + tujuan, dengan kode unik
(misalnya S01 atau AYAM). Pembaca menulis kode itu di setiap SETUP/UPDATE ("Kode: S01"), dan bot
memakai SOP milik kode itu (file sumber/S01.yaml). Setup tanpa kode yang dikenal ditolak.

    sumber:
      - kode: S01
        nama: trabar
        channel: "-1001234567890"
        judul: Grup Sinyal            # nama channel, hanya untuk tampilan
        topik: [476]                  # "semua" atau daftar nomor topik
        abaikan: []                   # dipakai kalau topik: semua
        olah: claude                  # claude | traderawam
        ke: mt5                       # mt5 | bybit
        akun: ""                      # Bybit: nama akun di akun-bybit.env
"""
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from dataclasses import asdict, dataclass, field
from typing import List, Optional, Union

import yaml

PATH = "sumber.yaml"
OLAH = {"claude": "Claude CLI (semua chat, gambar, reply)",
        "traderawam": "bot pembaca kode (teks format TraderAwam)"}
TUJUAN = {"mt5": "MT5", "bybit": "Bybit"}
_OLD_JENIS = {"gambar": "claude", "teks": "traderawam"}   # susunan lama
_HEADER = ("# Daftar sumber sinyal yang ditonton pembaca.\n"
           "# Atur lewat MENU > Sumber sinyal. SOP tiap sumber ada di folder sumber/<kode>.yaml.\n")


@dataclass
class Source:
    nama: str
    channel: str
    judul: str = ""
    topik: Union[str, List[int]] = "semua"
    abaikan: List[int] = field(default_factory=list)
    olah: str = "claude"
    ke: str = "mt5"
    kode: str = ""
    akun: str = ""
    akun_tujuan: Optional[List[str]] = None

    def account_names(self) -> List[str]:
        if self.akun_tujuan is not None:
            return list(self.akun_tujuan)
        return [self.akun] if self.akun else []

    @property
    def jenis(self) -> str:
        """Nama lama (gambar/teks), dipakai kode lama yang belum diganti."""
        return "teks" if self.olah == "traderawam" else "gambar"

    def matches(self, topic: int) -> bool:
        """Apakah pesan dari topik ini dibaca? (grup tanpa topik: topik = 1)"""
        if self.topik == "semua":
            return topic not in self.abaikan
        return topic in self.topik

    def topic_text(self) -> str:
        if self.topik == "semua":
            return "semua topik" + (f" kecuali {_join(self.abaikan)}" if self.abaikan else "")
        return f"topik {_join(self.topik)}"

    def describe(self) -> str:
        where = self.judul or self.channel
        names = self.account_names()
        akun = f" (akun {', '.join(names)})" if names else ""
        return (f"{self.kode} {self.nama}: {where}, {self.topic_text()}, {OLAH[self.olah]} "
                f"-> {TUJUAN[self.ke]}{akun}")


def _join(nums) -> str:
    return ", ".join(str(n) for n in nums)


def _ints(value) -> List[int]:
    return [int(v) for v in (value or [])]


def from_dict(d: dict) -> Source:
    topik = d.get("topik", "semua")
    olah = str(d.get("olah") or _OLD_JENIS.get(str(d.get("jenis", "")), "claude"))
    s = Source(nama=str(d["nama"]), channel=str(d["channel"]), judul=str(d.get("judul") or ""),
               topik="semua" if topik in (None, "semua") else _ints(topik),
               abaikan=_ints(d.get("abaikan")), olah=olah, ke=str(d.get("ke", "mt5")),
               kode=str(d.get("kode") or "").upper(), akun=str(d.get("akun") or ""),
               akun_tujuan=d.get("akun_tujuan"))
    if s.akun_tujuan is not None:
        if not isinstance(s.akun_tujuan, list) or any(not isinstance(a, str) or not a.strip()
                                                       for a in s.akun_tujuan):
            raise ValueError(f"sumber {s.nama}: akun_tujuan harus berupa daftar nama akun")
        s.akun_tujuan = list({a.strip().casefold(): a.strip() for a in s.akun_tujuan}.values())
    if s.olah not in OLAH:
        raise ValueError(f"sumber {s.nama}: olah harus {' / '.join(OLAH)}, bukan {s.olah!r}")
    if s.ke not in TUJUAN:
        raise ValueError(f"sumber {s.nama}: ke harus {' / '.join(TUJUAN)}, bukan {s.ke!r}")
    return s


def load(path: str = PATH, cfg: Optional[dict] = None) -> List[Source]:
    """Baca sumber.yaml. Kalau file belum ada tapi config.yaml lama masih punya
    pembaca.channel_sumber, itu dipakai sebagai satu sumber Claude ke MT5."""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return [from_dict(d) for d in data.get("sumber") or []]
    p = (cfg or {}).get("pembaca") or {}
    if p.get("channel_sumber"):
        topic = p.get("topik_sumber")
        return [Source(nama=p.get("nama_sumber") or "sumber", channel=str(p["channel_sumber"]),
                       topik=[int(topic)] if topic else "semua", olah="claude", ke="mt5")]
    return []


def save(sources: List[Source], path: str = PATH):
    data = {"sumber": [asdict(s) for s in sources]}
    target = Path(path)
    file = NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent, delete=False)
    temporary = Path(file.name)
    try:
        with file as f:
            f.write(_HEADER)
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=None)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def by_code(sources: List[Source], code: str) -> Optional[Source]:
    code = (code or "").strip().upper()
    return next((s for s in sources if s.kode == code), None) if code else None


def next_code(sources: List[Source]) -> str:
    """Kode baru yang belum pernah dipakai di daftar: S01, S02, ..."""
    used = {int(m.group(1)) for s in sources for m in [re.fullmatch(r"S(\d+)", s.kode or "")] if m}
    n = 1
    while n in used:
        n += 1
    return f"S{n:02d}"


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return s[:24] or "sumber"
