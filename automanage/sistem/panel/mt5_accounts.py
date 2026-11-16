"""Account and symbol settings for named MT5 terminal installations."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3

from copier import accounts, accounts_mt5
from copier.models import ACTIVE, AWAITING
from copier.mt5_worker import TerminalBroker
from copier.single import BOT_PORT, claim

from .symbols import candidates, match_screen
from .ui import ask, choose, clear, pause, title


class AccountSymbols:
    def __init__(self, name, settings):
        self.name, self.settings = name, settings

    def cfg(self):
        base = self.settings.cfg()
        mapping = accounts_mt5.metadata().get(self.name.casefold(), {}).get("simbol", base["simbol"])
        return {**base, "simbol": dict(mapping)}

    def set(self, path, value):
        mapping = self.cfg()["simbol"]
        mapping[path[-1]] = value
        accounts_mt5.set_metadata(self.name, simbol=mapping)

    def bot_running(self):
        return False  # the named-account bot reloads symbol settings automatically


def terminal_path(items, skip=None):
    raw = ask("  Folder instalasi MT5 (Enter = batal): ").strip('"')
    if not raw:
        return None
    try:
        return validate_terminal_path(raw, items, skip)
    except ValueError as error:
        print(f"  {error}")
        return None


def validate_terminal_path(raw, items, skip=None):
    path = Path(raw).expanduser().resolve() / "terminal64.exe"
    if not path.is_file():
        raise ValueError("terminal64.exe tidak ditemukan di folder tersebut.")
    if any(os.path.normcase(str(Path(a.path).resolve())) == os.path.normcase(str(path))
           for a in items if a != skip):
        raise ValueError("Terminal ini sudah terdaftar untuk akun lain.")
    return str(path)


def _probe(settings, path, identity=None, symbols=False):
    broker = TerminalBroker(settings.cfg(), path, identity)
    try:
        info = broker.account()
        return info, broker.symbol_catalog() if symbols else []
    finally:
        broker.shutdown()


def current_identity(settings, account):
    info, _ = _probe(settings, account.path)
    if not getattr(info, "login", None) or not getattr(info, "server", None):
        raise ValueError("Terminal MT5 belum login.")
    return [str(info.login), info.server]


def preview_account(settings, account, *, existing=False):
    info, symbols = _probe(settings, account.path, symbols=True)
    if not info.login or not info.server:
        raise ValueError("Terminal MT5 belum login.")
    identity = [str(info.login), info.server]
    mapping = (AccountSymbols(account.nama, settings).cfg() if existing else settings.cfg())["simbol"].copy()
    enabled = {symbol.name for symbol in symbols if symbol.trade_mode != 0}
    choices, missing = {}, []
    for pair, current in mapping.items():
        if current in enabled:
            continue
        names = [symbol.name for symbol in candidates(pair, symbols)]
        if names:
            choices[pair] = names
        else:
            missing.append(pair)
    return identity, mapping, choices, missing


def use_current_login(settings, account, confirmed_identity, mapping=None):
    lock = claim(BOT_PORT)
    if lock is None:
        raise ValueError("Hentikan bot MT5 dulu sebelum mengganti login.")
    with lock:
        if account not in accounts_mt5.load():
            raise ValueError("Setelan terminal sudah berubah. Buka ulang pengaturan akun.")
        identity = current_identity(settings, account)
        if identity != confirmed_identity:
            raise ValueError("Login terminal berubah. Deteksi ulang sebelum menyimpan.")
        name = account.nama.casefold()
        metadata = accounts_mt5.metadata()
        if metadata.get(name, {}).get("identity") == identity:
            if mapping is not None:
                accounts_mt5.set_metadata(name, simbol=mapping)
            return
        if any(entry.get("identity") == identity for key, entry in metadata.items()
               if key not in (name, "@legacy")):
            raise ValueError("Nomor login dan server ini sudah terdaftar untuk akun lain.")
        journal = Path(settings.cfg()["bot"]["jurnal"])
        if journal.exists():
            with closing(sqlite3.connect(journal.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                columns = {row[1] for row in db.execute("PRAGMA table_info(setups)")}
                states = (AWAITING, *ACTIVE)
                placeholders = ", ".join("?" for _ in states)
                query = f"SELECT id FROM setups WHERE status IN ({placeholders})"
                params = states
                if "account" in columns:
                    query += " AND (account = ? OR account = '')"
                    params += (name,)
                if db.execute(query + " LIMIT 1", params).fetchone():
                    raise ValueError("Jurnal masih punya setup aktif atau menunggu konfirmasi untuk akun ini.")
        fields = {"identity": identity}
        if mapping is not None:
            fields["simbol"] = mapping
        accounts_mt5.set_metadata(name, **fields)


def readiness(cfg, account, identity):
    broker = TerminalBroker(cfg, account.path, identity)
    try:
        if not broker.is_hedging():
            raise ValueError("akun bukan hedging")
        if cfg["akun"]["hanya_demo"] and not broker.is_demo():
            raise ValueError("akun real ditolak oleh setelan hanya demo")
        broker.ready()
        return set(broker.symbols())
    finally:
        broker.shutdown()


def _change_path(settings, items, index):
    account = items[index]
    path = terminal_path(items, skip=account)
    if not path:
        return
    change_path(settings, items, index, path)
    print("  Direktori terminal diperbarui.")


def change_path(settings, items, index, path):
    account = items[index]
    identity = accounts_mt5.metadata().get(account.nama.casefold(), {}).get("identity")
    if not identity:
        original, _ = _probe(settings, account.path)
        identity = [str(original.login), original.server]
    _probe(settings, path, identity)
    accounts_mt5.set_metadata(account.nama, identity=identity)
    items[index] = accounts_mt5.Account(account.nama, path)
    accounts_mt5.save(items)


def _add(settings, items):
    name = ask("  Nama akun (Enter = batal): ")
    if not name:
        return
    if not accounts.valid_name(name) or any(a.nama.casefold() == name.casefold() for a in items):
        print("  Nama akun harus valid dan unik.")
        return
    path = terminal_path(items)
    if path:
        account = accounts_mt5.Account(name, path)
        confirmed = _confirm_account(preview_account(settings, account))
        if confirmed:
            add_account(settings, account, *confirmed)
            print(f"  Akun '{name}' tersimpan.")


def add_account(settings, account, confirmed_identity, mapping):
    name, path = account.nama, account.path
    items = accounts_mt5.load()
    if not accounts.valid_name(name) or any(a.nama.casefold() == name.casefold() for a in items):
        raise ValueError("Nama akun harus valid dan unik.")
    if any(os.path.normcase(os.path.realpath(a.path)) == os.path.normcase(os.path.realpath(path)) for a in items):
        raise ValueError("Terminal ini sudah terdaftar untuk akun lain.")
    identity = current_identity(settings, account)
    if identity != confirmed_identity:
        raise ValueError("Login terminal berubah. Deteksi ulang sebelum menyimpan.")
    if any(meta.get("identity") == identity for key, meta in accounts_mt5.metadata().items()
           if key != "@legacy"):
        raise ValueError("Nomor login dan server ini sudah terdaftar.")
    accounts_mt5.set_metadata(name, identity=identity, simbol=mapping)
    accounts_mt5.save(items + [account])


def _confirm_account(preview):
    identity, mapping, choices, missing = preview
    print(f"  Login {identity[0]} · Server {identity[1]}")
    for pair, names in choices.items():
        if len(names) == 1:
            mapping[pair] = names[0]
            print(f"  {pair} → {names[0]}")
        else:
            print(f"  Pilih simbol broker untuk {pair}:")
            pick = choose(names, back="Nanti")
            if pick is not None:
                mapping[pair] = names[pick]
    if missing:
        print("  Simbol belum ditemukan: " + ", ".join(missing))
    if ask("  Simpan akun dan mapping simbol? (y/n): ").lower() == "y":
        return identity, mapping


def _symbols(settings):
    while True:
        mapping = settings.cfg()["simbol"]
        keys = sorted(mapping)
        pick = choose([f"{key} -> {mapping[key]}" for key in keys] + ["+ Tambah simbol"])
        if pick is None:
            return
        key = keys[pick] if pick < len(keys) else ask("  Nama simbol di sinyal: ").upper()
        value = ask("  Nama simbol di broker (Enter = batal): ") if key else ""
        if value:
            settings.set(("simbol", key), value)


def screen(settings):
    while True:
        clear()
        title("AKUN MT5")
        try:
            items = accounts_mt5.load()
            pick = choose([f"{a.nama} · {a.path}" for a in items] + ["+ Tambah akun"])
            if pick is None:
                return
            if pick == len(items):
                _add(settings, items)
            else:
                account = items[pick]
                action = choose(["Ganti direktori terminal", "Nama simbol",
                                 "Cocokkan otomatis dengan broker di MT5", "Pakai akun yang sedang login"])
                scoped = AccountSymbols(account.nama, settings)
                if action == 0:
                    _change_path(settings, items, pick)
                elif action == 1:
                    _symbols(scoped)
                    continue
                elif action == 2:
                    identity = accounts_mt5.metadata().get(account.nama.casefold(), {}).get("identity")
                    info, symbols = _probe(settings, account.path, identity, symbols=True)
                    match_screen(scoped, info, symbols)
                    continue
                elif action == 3:
                    confirmed = _confirm_account(preview_account(settings, account, existing=True))
                    if confirmed:
                        use_current_login(settings, account, *confirmed)
                        print("  Login akun MT5 tersimpan.")
                else:
                    continue
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            print(f"  [X] {error}")
            pause()
            return
        pause()
