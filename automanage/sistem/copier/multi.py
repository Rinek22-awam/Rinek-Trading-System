"""Route signals and persisted order management by source and account."""
import json
import logging
import os
import time
from functools import partial
from pathlib import Path

import yaml

from reader import sources

from . import accounts, accounts_mt5, envfile, sopfiles
from .app import App
from .app_bybit import BybitAdapter, BybitApp, BybitExecutor, BybitGate, BybitGuard
from .config import load_config
from .executor import Executor
from .gate import Gate
from .guard import Guard
from .models import ACTIVE, AWAITING, DECLINED, EXPIRED, REJECTED
from .mt5_worker import TerminalBroker
from .profiles import Profile
from .setup_format import dest_of, parse_setup, parse_update

log = logging.getLogger(__name__)


def status_path(platform):
    return Path(envfile.SYSTEM) / "data" / f"koneksi-{platform}.json"


class AccountProfiles:
    def __init__(self, app, config, make_bybit=None):
        self.app, self.config, self.make_bybit = app, config, make_bybit
        self.platform = app.DEST
        self.clients, self.status, self.definitions = {}, {}, {}
        self.currencies = {}
        self.current, self.profiles = {}, {}
        self.next_check = 0
        self.broker_settings = None
        self.signature = None
        self.legacy_loaded = False
        self.status_write_failed = False

    def _legacy_rows(self, items, registered):
        old_sources = {src.kode: src.akun for src in items if src.ke == self.platform}
        default = next(iter(registered), "")
        rows = self.app.journal.db.execute("SELECT DISTINCT kode FROM setups WHERE account = ''")
        with self.app.journal.db:
            for (code,) in rows.fetchall():
                if self.platform == "bybit":
                    name = old_sources.get(code) or default
                else:
                    name = "@legacy"
                if name:
                    self.app.journal.db.execute("UPDATE OR IGNORE setups SET account = ? WHERE account = '' AND kode = ?",
                                                (name.casefold(), code))

    def refresh(self):
        items = sources.load()
        mt5 = self.platform == "mt5"
        registered = accounts_mt5.load() if mt5 else accounts.load()
        definitions = {a.nama.casefold(): a for a in registered}
        metadata = accounts_mt5.metadata() if mt5 else {}
        if not self.legacy_loaded:
            self._legacy_rows(items, definitions)
            self.legacy_loaded = True
        live = self.app.journal.by_status(AWAITING, *ACTIVE)
        catalog = {s.kode: s for s in items if s.ke == self.platform}
        current = {code: [name.casefold() for name in src.account_names()] for code, src in catalog.items()}
        if mt5:
            for code, src in catalog.items():
                if src.akun_tujuan is None and not current[code]:
                    current[code] = ["@legacy"]
            definitions["@legacy"] = accounts_mt5.Account("MT5 lama", os.getenv("MT5_PATH", ""))
        keys = {(code, name) for code, names in current.items() for name in names}
        keys.update((row["kode"], row["account"]) for row in live if row["account"])
        needed = {name for _, name in keys}
        sig = (sopfiles.signature([self.config] + [sopfiles.sop_path(c) for c in catalog]),
               items, definitions, metadata, keys)
        now = time.monotonic()
        if sig == self.signature and now < self.next_check:
            return
        self.next_check = now + 15
        base = load_config(self.config)
        self.app.cfg["akun"] = base["akun"]
        for name, client in list(self.clients.items()):
            if (name not in needed or definitions.get(name) != self.definitions.get(name)
                    or mt5 and self.broker_settings != base["mt5"]):
                if mt5 and isinstance(client, TerminalBroker):
                    client.shutdown()
                del self.clients[name]
        self.definitions = definitions
        self.broker_settings = base["mt5"]
        self.status = {}
        self.currencies = {}
        for name in sorted(needed):
            account = definitions.get(name)
            try:
                if account is None:
                    raise ConnectionError("Akun belum terdaftar")
                self._connect(name, account, base, metadata.get(name, {}))
                self.status[name] = ""
            except Exception as error:
                self.status[name] = str(error)
        profiles = {}
        for code, name in sorted(keys):
            src = catalog.get(code)
            blocked = self.status[name]
            try:
                cfg = sopfiles.effective(self.config, code) if src else load_config(self.config)
            except (OSError, ValueError, yaml.YAMLError) as error:
                previous = self.profiles.get((code, name))
                cfg = previous.cfg if previous else load_config(self.config)
                blocked = blocked or f"SOP tidak dapat dibaca: {error}"
            if mt5:
                cfg["simbol"] = metadata.get(name, {}).get("simbol", cfg["simbol"])
            label = definitions[name].nama if name in definitions else name
            profile = Profile(code, f"{src.nama} ({code}) · {label}" if src else label,
                              cfg, src=src, extra={"akun": label, "key": name}, blocked=blocked)
            if not self.status[name]:
                self._configure(profile, name, self.clients[name])
                previous = self.profiles.get((code, name))
                if previous and previous.guard:
                    profile.guard._observed = previous.guard._observed
            profiles[code, name] = profile
        self.current, self.profiles = current, profiles
        self.signature = sig
        self.publish()

    def _connect(self, name, account, cfg, meta):
        client = self.clients.get(name)
        if self.platform == "bybit":
            if client is None:
                client = self.clients[name] = self.make_bybit(account)
                why = self.app.check_account(client, account.nama)
            elif cfg["akun"]["hanya_demo"] and not client.is_demo():
                why = "Akun live ditolak oleh setelan Hanya boleh demo/testnet"
            elif self.status_previous(name):
                why = self.app.check_account(client, account.nama)
            else:
                ok, detail = client.connect()
                why = "" if ok else detail or "Koneksi Bybit gagal"
            if why:
                raise ConnectionError(why)
            return
        credentials = (os.getenv("MT5_LOGIN"), os.getenv("MT5_PASSWORD"), os.getenv("MT5_SERVER")) if name == "@legacy" else ()
        identity = meta.get("identity")
        if client is not None and (client.pipe.closed or not client.process.is_alive() or client.identity != identity):
            client.shutdown()
            client = None
        if client is None:
            client = self.clients[name] = TerminalBroker(cfg, account.path, identity, credentials)
            client.identity = identity
        if not identity:
            info = client.account()
            identity = [str(info.login), info.server]
            if name != "@legacy" and any(entry.get("identity") == identity
                                         for key, entry in accounts_mt5.metadata().items() if key not in (name, "@legacy")):
                raise ConnectionError("Login dan server sudah dipakai akun MT5 lain")
            accounts_mt5.set_metadata(name, identity=identity)
            client.shutdown()
            client = self.clients[name] = TerminalBroker(cfg, account.path, identity, credentials)
            client.identity = identity
        info = client.account()
        if info is not None:
            self.currencies[name] = info.currency or ""
        if info is None or not client.is_hedging():
            raise ConnectionError("MT5 belum login atau akun bukan hedging")
        if cfg["akun"]["hanya_demo"] and not client.is_demo():
            raise ConnectionError("Akun live ditolak oleh setelan Hanya boleh akun demo")
        client.ready()

    def status_previous(self, name):
        return not any(key[1] == name and not p.blocked for key, p in self.profiles.items())

    def _configure(self, profile, name, client):
        cfg, code, journal = profile.cfg, profile.code, self.app.journal
        accept = lambda row: row.get("kode") == code and row.get("account") == name
        activity = partial(self.app.activity, account=profile.extra["akun"])
        if self.platform == "bybit":
            cfg["risiko"]["lot_maks"] = float("inf")
            broker = BybitAdapter(cfg, client, clock=self.app.clock)
            broker.journal, broker.activity = journal, activity
            gate, executor, guard = BybitGate, BybitExecutor, BybitGuard
        else:
            broker = client
            gate, executor, guard = Gate, Executor, Guard
        profile.broker = broker
        profile.gate = gate(cfg, broker, journal, self.app.clock, accept=accept, label=profile.name)
        if self.platform == "mt5":
            identity = getattr(client, "identity", None)
            aliases = {key for key, item in self.clients.items() if getattr(item, "identity", None) == identity}
        else:
            aliases = {name}
        profile.gate.same_account = lambda row: row.get("account") in aliases
        profile.executor = executor(cfg, broker, journal, self.app.clock)
        profile.guard = guard(cfg, broker, journal, self.app.notify, self.app.clock, accept=accept, activity=activity)

    def publish(self):
        rows = []
        for (code, name), profile in self.profiles.items():
            rows.append(dict(code=code, source=profile.src.nama if profile.src else code,
                             name=profile.extra["akun"], error=self.status[name],
                             old=name not in self.current.get(code, [])))
        platform = self.platform.upper()
        path = status_path(self.platform)
        try:
            accounts_mt5.atomic_write(path, json.dumps(rows, ensure_ascii=False))
        except OSError as error:
            if not self.status_write_failed:
                log.warning("Status koneksi %s untuk Dashboard gagal diperbarui. Bot tetap berjalan. "
                            "Akan dicoba lagi pada penyegaran berikutnya (%s).", platform, error)
            self.status_write_failed = True
        else:
            if self.status_write_failed:
                log.info("Status koneksi %s untuk Dashboard berhasil diperbarui kembali.", platform)
                self.status_write_failed = False

    def targets(self, code):
        return [self.profiles[code, name] for name in self.current.get(code, ())]

    def get(self, code):
        targets = self.targets(code)
        return (targets[0], "") if targets and not targets[0].blocked else (None, "Akun tujuan belum siap")

    def for_account(self, row):
        return self.profiles.get((row.get("kode"), row.get("account")))

    def for_row(self, row):
        return self.for_account(row) or Profile("", "", {}, blocked="Akun tujuan tidak tersedia")

    def all(self):
        return [profile for profile in self.profiles.values() if profile.guard is not None]

    sources = all

    def close(self):
        for client in self.clients.values():
            if isinstance(client, TerminalBroker):
                client.shutdown()


class RoutedApp:
    def route(self, config, make_bybit=None):
        self.profiles = AccountProfiles(self, config, make_bybit)
        self.profiles.refresh()

    def startup_checks(self):
        self.notify(f"🤖 Bot {self.DEST.upper()} nyala. Status akun tersedia di dashboard.")
        return True

    def tick(self):
        try:
            self.profiles.refresh()
        except Exception:
            log.exception("Setelan akun belum bisa dimuat. Pengelolaan setup yang sudah berjalan dilanjutkan.")
        try:
            self._check_waiting_confirmations()
        except Exception:
            log.exception("Pemeriksaan setup yang menunggu konfirmasi gagal")
        for profile in self.profiles.all():
            try:
                profile.guard.run_once()
            except Exception:
                log.exception("Gagal memantau %s", profile.name)
        self._watch_daily_loss()

    def _execute(self, sid):
        row = self.journal.get(sid)
        name = self.profiles.for_row(row).extra.get("akun", row.get("account", ""))
        try:
            result = App._execute(self, sid)
            if self.DEST == "bybit" and result.startswith("📌"):
                leverage = getattr(self.profiles.for_row(row).broker, "last_leverage", None)
                if leverage:
                    result += f", leverage {leverage[0]}x"
        except Exception as error:
            log.exception("Eksekusi akun %s gagal", name)
            if self.journal.get(sid)["status"] == AWAITING:
                self.journal.update(sid, status=REJECTED, reason=str(error))
            result = f"#{sid} gagal: {error}"
        return f"{name} · {result}"

    def on_channel_post(self, msg):
        chat, msg_id = msg["chat"]["id"], msg["message_id"]
        text = msg.get("text") or msg.get("caption") or ""
        if chat != self.inbox or dest_of(text) != self.DEST:
            return
        self.profiles.refresh()
        update = parse_update(text)
        if update:
            self.on_source_update(update)
            return
        if self.journal.message_seen(chat, msg_id):
            return
        setup, errors = parse_setup(text)
        if setup is None:
            if errors:
                self.notify("❌ Format setup salah:\n- " + "\n- ".join(errors))
            return
        profiles = self.profiles.targets(setup.code)
        if not profiles:
            self._prepare(setup, dict(msg_chat=chat, msg_id=msg_id, posted=float(msg["date"])), None,
                          f"{setup.code or setup.symbol}: belum ada akun tujuan untuk sumber ini.")
            return
        group = []
        for profile in profiles:
            name = profile.extra["akun"]
            key = profile.extra["key"]
            meta = dict(msg_chat=chat, msg_id=msg_id, posted=float(msg["date"]), account=key)
            try:
                self._prepare(setup, meta, None if profile.blocked else profile,
                              f"{name}: {profile.blocked}", group)
            except Exception as error:
                log.exception("Gagal menyiapkan sinyal untuk %s", name)
                self.notify(f"🚫 {name}: {error}")
        if not group:
            return
        if any(confirm for _, _, confirm in group):
            sid = group[0][0]
            self.notify("\n\n".join(summary for _, summary, _ in group),
                        buttons=[[('✅ Ya, eksekusi', f'group-yes:{sid}'), ('❌ Tidak', f'group-no:{sid}')]])
        else:
            self.notify("\n".join(self._execute(sid) for sid, _, _ in group))

    def on_button(self, query):
        data, _, generation = query.get("data", "").partition("|")
        action, _, sid = data.partition(":")
        if action not in ("group-yes", "group-no"):
            return super().on_button(query)
        if query["from"]["id"] != self.admin or not sid.isdigit():
            return
        try:
            self.bot.answer(query["id"])
        except Exception:
            log.warning("Konfirmasi Telegram terlambat dijawab", exc_info=True)
        self.profiles.refresh()
        msg_id = query.get("message", {}).get("message_id")
        row = self.journal.get(int(sid)) if (generation or "0") == str(self.journal.get_kv("button_generation", 0)) else None
        results = []
        if row:
            for target in self.journal.by_messages(row["msg_chat"], row["msg_id"]):
                if target["status"] != AWAITING:
                    continue
                key = target["id"]
                if target["confirm_deadline"] and self.clock() > target["confirm_deadline"]:
                    self.journal.update(key, status=EXPIRED, reason="Konfirmasi kedaluwarsa")
                elif action == "group-no":
                    self.journal.update(key, status=DECLINED, reason="Ditolak user")
                    results.append(f"#{key} ditolak.")
                else:
                    results.append(self._execute(key))
        self._reply(msg_id, "\n".join(results) or "Setup sudah tidak menunggu konfirmasi.")

    def on_source_update(self, update):
        code = update.get("code")
        if update.get("ref"):
            rows = self.journal.by_messages(self.inbox, update["ref"])
        elif update["action"] != "INFO":
            source, symbol = update["source"].lower(), update.get("pair", "")
            matches = [row for row in self.journal.by_status(AWAITING, *ACTIVE)
                       if (not source or (row["source"] or "").lower() == source)
                       and (not code or row["kode"] == code)
                       and (not symbol or symbol in (row["symbol"], row["broker_symbol"]))]
            latest = matches[-1] if matches else None
            rows = (self.journal.by_messages(latest["msg_chat"], latest["msg_id"]) or [latest]) if latest else []
        else:
            rows = []
        if not rows and code and not update.get("ref") and update["action"] != "INFO":
            self.notify(f"📣 {code}: {update['action']}\nTidak ada setup aktif dari sumber ini.")
            return
        if not rows:
            return super().on_source_update(update)
        for row in rows:
            try:
                super().on_source_update(update, ref=row)
            except Exception:
                log.exception("Update untuk setup #%s gagal", row["id"])

    def _status(self):
        lines = ["⏸ Mode stop" if self.journal.get_kv("paused", False) else "▶️ Menerima setup"]
        for row in self.journal.by_status(AWAITING, *ACTIVE):
            name = self.profiles.for_row(row).extra.get("akun", row.get("account", ""))
            lines.append(f"#{row['id']} {name} · {row['broker_symbol'] or row['symbol']} {row['side']} · {row['status']}")
        return "\n".join(lines)

    def _manage_all(self, method):
        count = 0
        for profile in self.profiles.all():
            try:
                count += getattr(profile.guard, method)()
            except Exception as error:
                self.notify(f"⚠️ {profile.name}: {error}")
        return count

    def _cancel_pending(self, setup_id=None):
        return self._manage_all("cancel_pending") if setup_id is None else super()._cancel_pending(setup_id)

    def _close_all(self):
        return self._manage_all("close_all")


class MultiMT5(RoutedApp, App):
    def startup_checks(self):
        profiles = self.profiles
        lines = []
        for key, error in profiles.status.items():
            account = profiles.definitions.get(key)
            name = account.nama if account else key
            currency = profiles.currencies.get(key, "").upper()
            unit = " · Cent (USC)" if currency == "USC" else f" · Mata uang {currency}" if currency else ""
            status = f"⚠ {name}{unit} · {error}" if error else f"✅ {name}{unit} · Siap"
            lines.append(status)
        for (code, key), profile in profiles.profiles.items():
            if profile.blocked and profile.blocked != profiles.status[key]:
                lines.append(f"⚠ {profile.extra['akun']} · {code} · {profile.blocked}")
        if not lines:
            lines.append("⚠ Belum ada akun tujuan MT5 untuk sumber sinyal.")

        message = "\n".join(["🤖 Bot MT5 nyala", *lines])
        log.info("%s", message)
        sent = self.notify(message)
        if sent is not None:
            try:
                sent.result(timeout=5)
            except Exception as error:
                log.warning("Ringkasan startup MT5 belum terkirim ke Telegram: %s", error)
        return True


class MultiBybit(RoutedApp, BybitApp):
    pass
