"""SOP per sumber untuk satu bot (MT5 atau Bybit).

Setiap sumber yang tujuannya bot ini mendapat "profil": config gabungan (bawaan + universal +
sumber/<kode>.yaml) dan gerbang, eksekutor, penjaga posisi sendiri yang hanya mengurus setup
berkode sumber itu. Profil dibangun ulang otomatis kalau file setelan berubah, jadi perubahan
SOP dari menu langsung berlaku tanpa restart bot.

Setup lama tanpa kode (atau kode sumber yang sudah dihapus) tetap dijaga oleh profil
cadangan (config universal + bawaan), tapi setup BARU tanpa kode yang dikenal ditolak.
"""
import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from . import sopfiles
from .config import with_defaults

log = logging.getLogger(__name__)


@dataclass
class Profile:
    code: str
    name: str
    cfg: dict
    broker: object = None
    gate: object = None
    executor: object = None
    guard: object = None
    src: object = None
    blocked: str = ""        # alasan sumber ini tidak bisa menerima setup baru
    extra: dict = field(default_factory=dict)


class SingleProfile:
    """Susunan lama / tes: satu SOP untuk semua setup, kode tidak diperiksa."""

    def __init__(self, profile: Profile):
        self.profile = profile

    def get(self, code) -> Tuple[Optional[Profile], str]:
        return self.profile, ""

    def for_row(self, row) -> Profile:
        return self.profile

    def all(self) -> List[Profile]:
        return [self.profile]

    def sources(self) -> List[Profile]:
        return [self.profile]


class Profiles:
    def __init__(self, dest: str, universal_path: str, make: Callable, root: str = ".",
                 extra_paths: Callable[[], list] = lambda: []):
        """make(code, src|None, cfg, accept) -> Profile. src None = profil cadangan."""
        self.dest, self.universal_path, self.make, self.root = dest, universal_path, make, root
        self.extra_paths = extra_paths
        self._sig = None
        self.by_code: Dict[str, Profile] = {}
        self.others: Dict[str, str] = {}      # kode sumber milik bot lain -> tujuannya
        self.fallback: Optional[Profile] = None

    def _paths(self):
        from reader import sources
        import os
        items = []
        try:
            items = sources.load(os.path.join(self.root, sources.PATH))
        except Exception:
            pass
        return [os.path.join(self.root, sources.PATH), self.universal_path] + \
            [sopfiles.sop_path(s.kode, self.root) for s in items if s.kode] + list(self.extra_paths())

    def refresh(self):
        sig = sopfiles.signature(self._paths())
        if sig == self._sig:
            return
        self._sig = sig
        self._build()

    def _build(self):
        import os
        from reader import sources
        try:
            items = sources.load(os.path.join(self.root, sources.PATH))
        except Exception as e:
            log.error("sumber.yaml tidak terbaca: %s", e)
            items = []
        by_code, others = {}, {}
        for s in items:
            if not s.kode:
                continue
            if s.ke != self.dest:
                others[s.kode] = s.ke
                continue
            code = s.kode
            try:
                cfg = sopfiles.effective(self.universal_path, code, self.root)
                prof = self.make(code, s, cfg, lambda r, c=code: (r.get("kode") or "") == c)
            except Exception as e:   # SOP rusak: sumber ini menolak setup baru, yang lain jalan terus
                log.exception("SOP %s tidak bisa dipakai", code)
                prof = Profile(code, f"{s.nama} ({code})", with_defaults({}), src=s,
                               blocked=f"SOP sumber {code} tidak bisa dibaca: {e}")
            by_code[code] = prof
        self.by_code, self.others = by_code, others
        try:
            base = with_defaults(sopfiles._yaml(self.universal_path))
        except Exception:
            base = with_defaults({})
        known = {c for c, p in by_code.items() if not p.blocked and p.guard is not None}
        self.fallback = self.make("", None, base, lambda r: (r.get("kode") or "") not in known)
        log.info("SOP per sumber dimuat: %s", ", ".join(f"{p.name}" for p in by_code.values()) or "-")

    # --- dipakai App --------------------------------------------------------
    def get(self, code: str) -> Tuple[Optional[Profile], str]:
        """Profil untuk setup BARU. (None, alasan) kalau tidak ada SOP yang jelas."""
        self.refresh()
        code = (code or "").strip().upper()
        if not code:
            return None, "Setup tanpa kode sumber (baris Kode:). Tidak ada SOP yang jelas, ditolak."
        prof = self.by_code.get(code)
        if prof is None:
            if code in self.others:
                return None, f"Sumber {code} untuk bot {self.others[code].upper()}, bukan bot ini."
            return None, (f"Kode sumber {code} tidak dikenal (belum ada atau sudah dihapus di "
                          f"MENU > Sumber sinyal). Tidak ada SOP yang jelas, ditolak.")
        if prof.blocked:
            return None, prof.blocked
        return prof, ""

    def for_row(self, row: dict) -> Profile:
        """Profil yang menjaga setup yang sudah ada (cadangan kalau sumbernya hilang)."""
        self.refresh()
        prof = self.by_code.get((row.get("kode") or "").upper())
        return prof if prof is not None and not prof.blocked and prof.guard is not None else self.fallback

    def sources(self) -> List[Profile]:
        self.refresh()
        return [p for p in self.by_code.values() if p.guard is not None]

    def all(self) -> List[Profile]:
        """Semua profil yang menjaga posisi: per sumber + cadangan (setiap setup tepat satu)."""
        return self.sources() + [self.fallback]
