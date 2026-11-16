"""Akun Bybit, disimpan di akun-bybit.env (folder depan, di samping .env; tidak ikut git).

Tiap sumber Bybit memilih akun lewat namanya. Dua sumber boleh memakai akun yang sama
(berbagi saldo dan aturan "satu posisi per koin"). File ini ditulis menu
(MENU > Sumber sinyal > ... > Akun Bybit, atau Setelan universal > Bybit), bukan diedit tangan:

    BYBIT_AKUN_1_NAMA=Utama
    BYBIT_AKUN_1_MODE=demo
    BYBIT_AKUN_1_KEY=...
    BYBIT_AKUN_1_SECRET=...

Susunan lama (BYBIT_API_KEY / BYBIT_API_SECRET / BYBIT_MODE di .env) dibaca sebagai akun
"Utama" selama akun-bybit.env belum ada.
"""
import os
import re
from dataclasses import dataclass
from typing import List, Optional

from . import envfile

FILE = "akun-bybit.env"
MODES = ("demo", "testnet", "live")
_LINE = re.compile(r"^BYBIT_AKUN_(\d+)_(NAMA|MODE|KEY|SECRET)$")
_HEADER = ("# Akun Bybit untuk bot Bybit. Ditulis lewat MENU, jangan dibagikan ke siapa pun.\n"
           "# File ini tidak ikut ke git. Izin API key: hanya Contract (Orders + Positions), TANPA Withdraw.\n")


@dataclass
class Account:
    nama: str
    mode: str
    key: str
    secret: str

    def masked(self) -> str:
        k = self.key or ""
        return f"{k[:4]}…{k[-4:]}" if len(k) > 10 else ("(kosong)" if not k else "…")

    def describe(self) -> str:
        return f"{self.nama} ({self.mode}, key {self.masked()})"


def path(folder: Optional[str] = None) -> str:
    return os.path.join(folder or envfile.FRONT, FILE)


def _values(file: str) -> dict:
    from dotenv import dotenv_values
    return {k: (v or "").strip() for k, v in dotenv_values(file).items()} if os.path.exists(file) else {}


def load(folder: Optional[str] = None, environ=os.environ) -> List[Account]:
    file = path(folder)
    if os.path.exists(file):
        rows = {}
        for k, v in _values(file).items():
            m = _LINE.match(k)
            if m:
                rows.setdefault(int(m.group(1)), {})[m.group(2)] = v
        out = []
        for n in sorted(rows):
            r = rows[n]
            if r.get("NAMA"):
                out.append(Account(r["NAMA"], (r.get("MODE") or "demo").lower(), r.get("KEY", ""),
                                   r.get("SECRET", "")))
        return out
    key, secret = (environ.get("BYBIT_API_KEY") or "").strip(), (environ.get("BYBIT_API_SECRET") or "").strip()
    if key and secret:   # susunan lama
        return [Account("Utama", (environ.get("BYBIT_MODE") or "demo").strip().lower(), key, secret)]
    return []


def save(accounts: List[Account], folder: Optional[str] = None):
    lines = [_HEADER]
    for n, a in enumerate(accounts, 1):
        lines.append(f"\nBYBIT_AKUN_{n}_NAMA={a.nama}\nBYBIT_AKUN_{n}_MODE={a.mode}\n"
                     f"BYBIT_AKUN_{n}_KEY={a.key}\nBYBIT_AKUN_{n}_SECRET={a.secret}\n")
    with open(path(folder), "w", encoding="utf-8") as f:
        f.write("".join(lines))


def by_name(accounts: List[Account], name: str) -> Optional[Account]:
    name = (name or "").strip().lower()
    return next((a for a in accounts if a.nama.lower() == name), None)


def migrate_from_env(folder: Optional[str] = None) -> bool:
    """akun-bybit.env belum ada tapi .env lama punya BYBIT_API_KEY: pindahkan jadi akun "Utama".
    .env tidak diubah (boleh dikosongkan sendiri nanti). True kalau file baru dibuat."""
    if os.path.exists(path(folder)):
        return False
    env = _values(os.path.join(folder or envfile.FRONT, ".env"))
    old = load(folder, environ=env)
    if not old:
        return False
    save(old, folder)
    return True


def valid_name(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9 _\-]{1,30}", name or ""))
