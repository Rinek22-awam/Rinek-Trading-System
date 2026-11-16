import sqlite3
from types import SimpleNamespace

from copier import accounts, accounts_mt5, mt5_worker, multi
from copier.app_bybit import BybitAdapter
from copier.broker import Broker
from copier.broker_bybit import BybitBroker
from copier.config import load_config
from copier.journal import Journal, _SCHEMA
from copier.models import AWAITING, PENDING, Setup
from reader import sources

from .fake_bybit import FakeBybit
from .fake_mt5 import FakeMT5
from .test_flow import ADMIN, INBOX, SETUP, Clock, FakeBot, press
from .test_flow_bybit import LIMIT


def test_dashboard_status_write_failure_recovers_without_interrupting_bot(tmp_path, monkeypatch, caplog):
    path = tmp_path / "status.json"
    monkeypatch.setattr(multi, "status_path", lambda _: path)
    profiles = multi.AccountProfiles(SimpleNamespace(DEST="mt5"), "config.yaml")

    def locked(*args):
        raise PermissionError("[WinError 5] Access is denied")

    with caplog.at_level("INFO", logger=multi.__name__):
        with monkeypatch.context() as patch:
            patch.setattr(accounts_mt5, "atomic_write", locked)
            profiles.publish()
            profiles.publish()
        profiles.publish()
        profiles.publish()

    assert path.read_text(encoding="utf-8") == "[]"
    assert [record.levelname for record in caplog.records] == ["WARNING", "INFO"]
    assert "Bot tetap berjalan" in caplog.messages[0]
    assert "berhasil diperbarui kembali" in caplog.messages[1]


def test_bybit_group_confirmation_and_update_keep_original_accounts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(multi, "status_path", lambda _: tmp_path / "status.json")
    names = ["A", "B", "Unused"]
    registered = [accounts.Account(name, "demo", "", "") for name in names]
    monkeypatch.setattr(accounts, "load", lambda: registered)
    source = sources.Source("test", "1", ke="bybit", kode="S01", akun_tujuan=names[:2])
    sources.save([source])
    config = tmp_path / "config-bybit.yaml"
    config.write_text("bybit:\n  nilai_posisi_maks_usdt: 50000\n", encoding="utf-8")
    cfg, clock, bot = load_config(config), Clock(), FakeBot()
    sessions = {name: FakeBybit(clock, balance=5000 * (index + 1)) for index, name in enumerate(names)}
    journal = Journal(str(tmp_path / "journal.db"), clock=clock)
    app = multi.MultiBybit(cfg, BybitAdapter(cfg, None), journal, bot, INBOX, ADMIN, clock=clock)
    app.route(config, lambda a: BybitBroker("demo", session=sessions[a.nama]))
    message = dict(chat={"id": INBOX}, message_id=7, date=clock(), text=LIMIT + "\nKode: S01\nKe: bybit")
    app.on_channel_post(message)
    first, second = journal.by_messages(INBOX, 7)
    assert [first["account"], second["account"]] == ["a", "b"]
    assert first["status"] == second["status"] == AWAITING
    assert len(bot.sent) == 1
    assert "· A" in bot.last and "· B" in bot.last
    button = bot.sent[-1]["buttons"][0][0][1]
    press(app, button)
    press(app, button)
    app.on_channel_post(message)
    assert len(sessions["A"].orders) == len(sessions["B"].orders) == 1
    assert journal.get(first["id"])["status"] == journal.get(second["id"])["status"] == PENDING
    assert float(sessions["A"].placed()[0]["qty"]) < float(sessions["B"].placed()[0]["qty"])
    assert not sessions["Unused"].calls

    source.akun_tujuan = ["A"]
    sources.save([source])
    journal.db.close()
    journal = Journal(str(tmp_path / "journal.db"), clock=clock)
    app = multi.MultiBybit(cfg, BybitAdapter(cfg, None), journal, bot, INBOX, ADMIN, clock=clock)
    app.route(config, lambda a: BybitBroker("demo", session=sessions[a.nama]))
    app.on_channel_post(message)
    app.on_source_update(dict(source="tes", action="CANCEL", ref=7, text="batal"))
    assert all(session.placed()[0]["orderStatus"] == "Cancelled" for session in (sessions["A"], sessions["B"]))


def test_mt5_routes_symbols_per_account_and_skips_failed_account(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(multi, "status_path", lambda _: tmp_path / "status.json")
    registered = [accounts_mt5.Account(name, name) for name in ("A", "B", "Unused")]
    monkeypatch.setattr(accounts_mt5, "load", lambda: registered)
    monkeypatch.setattr(accounts_mt5, "metadata", lambda: {"a": {"simbol": {"XAUUSD": "XAUUSDm"}}})
    config = tmp_path / "config.yaml"
    config.write_text("gerbang:\n  konfirmasi_wajib: false\n", encoding="utf-8")
    cfg, clock, bot = load_config(config), Clock(), FakeBot()
    market = FakeMT5(bid=3995)
    journal = Journal(":memory:", clock=clock)
    calls = []

    def connect(self, name, account, cfg, metadata):
        calls.append(name)
        if name == "b":
            raise ConnectionError("Terminal terputus")
        self.clients[name] = Broker(cfg, mt5_module=market)

    monkeypatch.setattr(multi.AccountProfiles, "_connect", connect)
    sources.save([sources.Source("test", "1", kode="S01", akun_tujuan=["A", "B"])])
    app = multi.MultiMT5(cfg, None, journal, bot, INBOX, ADMIN, clock=clock)
    app.route(config)
    app.profiles.currencies["a"] = "USC"
    assert app.startup_checks()
    assert "A · Cent (USC) · Siap" in bot.last
    assert "B · Terminal terputus" in bot.last
    app.on_channel_post(dict(chat={"id": INBOX}, message_id=7, date=clock(), text=SETUP + "\nKode: S01\nKe: mt5"))
    first, second = journal.by_messages(INBOX, 7)
    assert first["broker_symbol"] == "XAUUSDm" and first["status"] == PENDING
    assert "Terminal terputus" in second["reason"]
    assert calls == ["a", "b"]


def test_legacy_journal_preserves_ticket_and_allows_second_account(tmp_path):
    path = str(tmp_path / "legacy.db")
    with sqlite3.connect(path) as db:
        db.executescript(_SCHEMA.replace(" account TEXT NOT NULL DEFAULT '',", "").replace(", account)", ")"))
        db.execute("INSERT INTO setups (id, msg_chat, msg_id, status) VALUES (19, 1, 2, ?)", (PENDING,))
        db.execute("INSERT INTO legs (setup_id, ticket) VALUES (19, 200)")
    journal = Journal(path)
    assert journal.get(19)["account"] == ""
    assert journal.legs(19)[0]["ticket"] == 200
    sid = journal.add_setup(Setup("XAUUSD", "BUY", 10, 8, [12]), PENDING, msg_chat=1, msg_id=2, account="b")
    assert sid > 19 and len(journal.by_messages(1, 2)) == 2


def test_terminal_process_refuses_order_after_manual_account_switch(monkeypatch):
    class Pipe:
        def __init__(self):
            self.requests = iter([("place", ()), ("cancel", ())])
            self.responses = []

        def recv(self):
            try:
                return next(self.requests)
            except StopIteration as error:
                raise EOFError from error

        def send(self, data):
            self.responses.append(data)

        def close(self):
            pass

    class FakeTerminal:
        def __init__(self, _):
            self.accounts = iter([123, 456])
            self.mt5 = SimpleNamespace(terminal_info=lambda: SimpleNamespace(connected=True, trade_allowed=True,
                                                                            tradeapi_disabled=False),
                                       shutdown=lambda: None)

        def connect(self, *args, **kwargs):
            return True

        def account(self):
            return SimpleNamespace(login=next(self.accounts), server="Server", trade_allowed=True, trade_expert=True)

        def place(self):
            return 1, ""

        def cancel(self):
            raise AssertionError("Order mutasi tidak boleh sampai ke akun yang berbeda")

    monkeypatch.setattr(mt5_worker, "Broker", FakeTerminal)
    pipe = Pipe()
    mt5_worker._serve(pipe, {}, "", ["123", "Server"], ())
    assert pipe.responses[:2] == [(True, None), (True, (1, ""))]
    assert pipe.responses[2][0] is False and "berbeda" in pipe.responses[2][1]
