"""Readiness check: is everything in place to run the bot and the reader? Read-only."""
import json
import os
import subprocess

from copier.single import BOT_PORT, BYBIT_BOT_PORT, READER_PORT, is_running
from reader import sources

OK, BAD, WARN = "✅", "❌", "⚠️"


def _line(mark, text):
    print(f"  {mark} {text}")


def env_keys():
    print("File .env")
    keys = {"TG_BOT_TOKEN": "token bot", "TG_INBOX_CHANNEL_ID": "ID kotak masuk",
            "TG_ADMIN_USER_ID": "ID kamu", "TG_API_ID": "API Telegram (pembaca)",
            "TG_API_HASH": "API hash Telegram (pembaca)"}
    for k, label in keys.items():
        _line(OK if os.getenv(k, "").strip() else BAD, f"{label} ({k})")


def telegram_bot():
    print("\nBot Telegram")
    from copier.telegram import TelegramBot
    token = os.getenv("TG_BOT_TOKEN", "").strip()
    if not token:
        _line(BAD, "token belum diisi")
        return
    try:
        me = TelegramBot(token, timeout=10).call("getMe")
        _line(OK, f"tersambung sebagai @{me['username']}")
    except Exception as e:
        _line(BAD, f"tidak tersambung: {e}")


def mt5(cfg):
    print("\nMT5")
    from copier import accounts_mt5

    if accounts_mt5.FILE.exists():
        from .mt5_accounts import AccountSymbols, readiness
        from .settings import Settings

        try:
            items = accounts_mt5.load()
            metadata = accounts_mt5.metadata()
        except (OSError, ValueError) as error:
            _line(BAD, str(error))
            return
        if not items:
            _line(BAD, "belum ada akun MT5 terdaftar")
        settings = Settings("config.yaml")
        for account in items:
            try:
                identity = metadata.get(account.nama.casefold(), {}).get("identity")
                available = readiness(cfg, account, identity)
                _line(OK, f"{account.nama}: terminal terhubung, Algo Trading aktif")
                mapping = AccountSymbols(account.nama, settings).cfg()["simbol"]
                missing = sorted(set(mapping.values()) - available)
                _line(WARN if missing else OK, "simbol terdaftar tersedia" if not missing
                      else "simbol tidak tersedia: " + ", ".join(missing))
            except (OSError, ValueError, RuntimeError) as error:
                _line(BAD, f"{account.nama}: {error}")
        return
    try:
        import MetaTrader5 as m
    except ImportError:
        _line(BAD, "paket MetaTrader5 belum terpasang")
        return
    if not m.initialize():
        _line(BAD, f"tidak tersambung ke MT5 desktop: {m.last_error()}")
        return
    a, t = m.account_info(), m.terminal_info()
    if a is None or t is None:
        _line(BAD, "terminal belum login atau terputus")
        m.shutdown()
        return
    demo = a.trade_mode == m.ACCOUNT_TRADE_MODE_DEMO
    _line(OK if demo or not cfg["akun"]["hanya_demo"] else BAD,
          f"akun {a.login} {a.server}: {'DEMO' if demo else 'REAL'}, saldo {a.balance:.2f} {a.currency}"
          + ("" if demo or not cfg["akun"]["hanya_demo"] else " (config: hanya_demo, bot akan menolak)"))
    _line(OK if a.margin_mode == m.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING else BAD, "tipe akun hedging")
    _line(OK if t.trade_allowed else BAD, "tombol Algo Trading " + ("nyala" if t.trade_allowed else
                                                                 "MATI (klik sampai hijau)"))
    missing = sorted({v for v in cfg["simbol"].values() if m.symbol_info(v) is None})
    _line(WARN if missing else OK, "simbol di config ada di MT5" if not missing
          else "tidak ada di MT5: " + ", ".join(missing))
    m.shutdown()


def claude_cli():
    print("\nClaude (mata pembaca)")
    try:
        p = subprocess.run(["claude", "auth", "status"], capture_output=True, text=True,
                           encoding="utf-8", timeout=30)
        st = json.loads(p.stdout)
    except FileNotFoundError:
        _line(BAD, "Claude Code (perintah claude) belum terpasang")
        return
    except Exception as e:
        _line(BAD, f"tidak bisa cek: {e}")
        return
    if st.get("loggedIn"):
        _line(OK, f"login ({st.get('subscriptionType', '?')})")
    else:
        _line(BAD, "belum login: buka PowerShell, ketik  claude auth login")


def reader(cfg):
    print("\nPembaca")
    p = cfg["pembaca"]
    _line(OK if os.path.exists("pembaca.session") else BAD,
          "login Telegram pembaca" + ("" if os.path.exists("pembaca.session")
                                      else " belum (Sumber sinyal > Tambah)"))
    try:
        items = sources.load(sources.PATH)
    except Exception as e:
        _line(BAD, f"sumber.yaml rusak: {e}")
        return
    if not items:
        _line(BAD, "sumber sinyal belum ada (MENU > Sumber sinyal)")
    for s in items:
        _line(OK, s.describe())


def bybit(path="config-bybit.yaml", make_broker=None):
    """Bybit: bot Telegram kedua dan tiap akun di akun-bybit.env (hanya membaca saldo/mode)."""
    print("\nBybit")
    from copier import accounts
    token = os.getenv("BYBIT_TG_BOT_TOKEN", "").strip()
    _line(OK if token else BAD, "token bot Telegram kedua (BYBIT_TG_BOT_TOKEN)")
    accs = accounts.load()
    if not accs:
        _line(BAD, "belum ada akun Bybit (Setelan universal > Bybit > Akun Bybit)")
        return
    from copier.config import load_config
    from copier.telegram import TelegramBot
    if token:
        try:
            me = TelegramBot(token, timeout=10).call("getMe")
            _line(OK, f"bot Telegram Bybit @{me['username']}")
        except Exception as e:
            _line(BAD, f"bot Telegram Bybit tidak tersambung: {e}")
    try:
        cfg = load_config(path)
    except Exception as e:
        _line(BAD, f"{path} tidak bisa dibaca: {e}")
        return
    try:
        items = [s for s in sources.load(sources.PATH) if s.ke == "bybit"]
    except Exception:
        items = []
    for acc in accs:
        users = [s.kode for s in items if acc.nama.casefold() in {name.casefold() for name in s.account_names()}]
        print(f"  Akun {acc.describe()} · {', '.join(users) or 'belum dipakai sumber'}")
        try:
            if make_broker:
                b = make_broker(acc.mode)
            else:
                from copier.broker_bybit import BybitBroker
                b = BybitBroker(acc.mode, acc.key, acc.secret)
        except Exception as e:
            _line(BAD, f"mode akun salah: {e}")
            continue
        ok, msg = b.connect()
        _line(OK if ok else BAD, msg)
        if not ok:
            continue
        if acc.mode == "live" and cfg["akun"]["hanya_demo"]:
            _line(BAD, "akun mode live tapi Setelan universal Bybit 'hanya demo' = ya: bot akan menolak")
        pmode, err = b.position_mode("BTCUSDT")
        _line(OK if pmode == "oneway" else BAD,
              "mode posisi one-way" if pmode == "oneway" else f"mode posisi {pmode or err} (harus one-way)")
        margin, err = b.margin_mode()
        _line(OK if margin == "cross" else BAD,
              "mode margin cross" if margin == "cross" else
              f"mode margin {margin or err} (harus CROSS: Aset > Akun Unified > Mode margin)")
    for s in items:
        for name in s.account_names() or [""]:
            if not accounts.by_name(accs, name):
                _line(BAD, f"sumber {s.kode} memakai akun '{name or '-'}' yang tidak ada")


def running():
    print("\nSedang jalan")
    for port, name in ((READER_PORT, "pembaca"), (BOT_PORT, "bot MT5"), (BYBIT_BOT_PORT, "bot Bybit")):
        on = is_running(port)
        _line(OK if on else "○", f"{name} " + ("jalan" if on else "mati"))


def run_all(cfg):
    env_keys()
    telegram_bot()
    mt5(cfg)
    bybit()
    claude_cli()
    reader(cfg)
    running()
