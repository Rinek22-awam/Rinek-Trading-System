"""Menu pieces that do not need a console: config editing and input checking."""
import shutil

import pytest
import yaml

from copier import sopfiles
from panel.settings import SECTIONS, SOURCE_SECTIONS, SOURCE_SECTIONS_BYBIT, Settings, _get, parse
from panel.yamledit import add_value, fmt, remove_value, set_value


def test_source_settings_offer_and_save_three_cancellation_modes(tmp_path):
    path = tmp_path / "sop.yaml"
    path.write_text("entry:\n  limit_kedaluwarsa_jam: 12\n", encoding="utf-8")
    for sections in (SOURCE_SECTIONS, SOURCE_SECTIONS_BYBIT):
        setting = Settings(path, sections=sections)
        field = next(item for item in dict(sections)["Entry"] if item[0] == ("entry", "kedaluwarsa"))
        key, label, kind, options, _ = field
        assert label == "Mode pembatalan setup"
        for index, mode in enumerate(("manual", "sinyal_waktu", "sinyal"), 1):
            setting.set(key, parse(kind, options, str(index)))
            assert setting.cfg()["entry"]["kedaluwarsa"] == mode


@pytest.fixture
def cfg_file(tmp_path):
    """SOP satu sumber (dulu: config.yaml), dibuat dari template."""
    p = tmp_path / "S01.yaml"
    p.write_text(sopfiles.source_text({}, "mt5", "S01", "tes"), encoding="utf-8")
    return p


@pytest.fixture
def universal_file(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(sopfiles.universal_text({"simbol": {"XAUUSD": "XAUUSDm", "EURUSD": "EURUSDm"}}, "mt5"),
                 encoding="utf-8")
    return p


def comments(text):
    return [l[l.index("#"):] for l in text.splitlines() if "#" in l]


def test_every_setting_path_exists_in_config(cfg_file):
    cfg = Settings(str(cfg_file), sections=SOURCE_SECTIONS).cfg()
    for _, items in SOURCE_SECTIONS:
        for path, *_ in items:
            assert _get(cfg, path) is not None or path == ("pengaman", "loss_harian_dolar"), path
    old = Settings(str(cfg_file)).cfg()          # daftar lama (dashboard) tetap terbaca
    for _, items in SECTIONS:
        for path, *_ in items:
            assert _get(old, path) is not None or path == ("pengaman", "loss_harian_dolar"), path


def test_set_keeps_comments_and_reloads(cfg_file):
    s = Settings(str(cfg_file))
    before = cfg_file.read_text(encoding="utf-8")
    s.set(("risiko", "mode"), "dolar")
    s.set(("risiko", "dolar"), 25.0)
    s.set(("tp", "bagi", "3"), [60, 25, 15])
    s.set(("pengaman", "loss_harian_dolar"), None)
    after = cfg_file.read_text(encoding="utf-8")
    assert comments(after) == comments(before)
    c = s.cfg()
    assert (c["risiko"]["mode"], c["risiko"]["dolar"], c["tp"]["bagi"][3]) == ("dolar", 25.0, [60, 25, 15])


def test_save_claude_guess_option_to_older_source_file(tmp_path):
    source = tmp_path / "S03.yaml"
    source.write_text("gerbang:\n  konfirmasi_wajib: false  # mode otomatis\n", encoding="utf-8")
    settings = Settings(str(source), sections=SOURCE_SECTIONS)
    path = ("gerbang", "izinkan_tebakan_otomatis")
    assert settings.cfg()["gerbang"][path[-1]] is False
    settings.set(path, True)
    assert settings.cfg()["gerbang"][path[-1]] is True
    assert "konfirmasi_wajib: false  # mode otomatis" in source.read_text(encoding="utf-8")


def test_refuses_to_write_a_broken_config(cfg_file):
    s = Settings(str(cfg_file))
    before = cfg_file.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        s.set(("risiko", "mode"), "bebas")       # the bot would refuse to start
    assert cfg_file.read_text(encoding="utf-8") == before


def test_symbols_add_edit_remove(universal_file):
    s = Settings(str(universal_file))
    s.save(add_value(s.load_text(), ("simbol",), "ETHUSD", "ETHUSDc"))
    s.set(("simbol", "XAUUSD"), "XAUUSDc")
    s.save(remove_value(s.load_text(), ("simbol", "EURUSD")))
    m = s.cfg()["simbol"]
    assert m["ETHUSD"] == "ETHUSDc" and m["XAUUSD"] == "XAUUSDc" and "EURUSD" not in m


@pytest.mark.parametrize("kind,options,raw,value", [
    ("bool", None, "y", True), ("bool", None, "tidak", False),
    ("choice", ["persen", "dolar"], "2", "dolar"), ("choice", ["persen", "dolar"], "PERSEN", "persen"),
    ("float", (0.01, 20), "1,5", 1.5), ("int", (0, 10), "2", 2),
    ("float_or_null", (0.01, 100), "", None), ("float_or_null", (0.01, 100), "25", 25.0),
    ("split", 3, "50 30 20", [50, 30, 20]), ("split", 3, "50/30/20", [50, 30, 20]),
    ("split", 3, "0 40 60", [0, 40, 60]),
])
def test_parse_accepts(kind, options, raw, value):
    assert parse(kind, options, raw) == value


@pytest.mark.parametrize("kind,options,raw", [
    ("bool", None, "mungkin"), ("choice", ["persen", "dolar"], "3"), ("float", (0.01, 20), "50"),
    ("float", (0.01, 20), "abc"), ("split", 3, "50 50"), ("split", 3, "50 -30 20"), ("split", 2, "0 0"), ("text", None, ""),
])
def test_parse_rejects(kind, options, raw):
    with pytest.raises(ValueError):
        parse(kind, options, raw)


def test_fmt_quotes_what_yaml_would_misread():
    assert fmt("-1001234567890") == '"-1001234567890"'
    assert fmt("sonnet") == "sonnet" and fmt("yes") == '"yes"'
    assert yaml.safe_load(f"a: {fmt('-1001234567890')}")["a"] == "-1001234567890"


def test_set_value_unknown_key():
    with pytest.raises(KeyError):
        set_value("a:\n  b: 1\n", ("a", "c"), 2)


def test_mt5_use_current_login_preserves_symbols_and_requires_same_preview(monkeypatch, tmp_path):
    from contextlib import nullcontext
    from types import SimpleNamespace

    from copier import accounts_mt5
    from panel import mt5_accounts

    account = accounts_mt5.Account("AKUN 1", "terminal64.exe")
    monkeypatch.setattr(accounts_mt5, "METADATA", tmp_path / "akun-mt5.json")
    monkeypatch.setattr(accounts_mt5, "load", lambda: [account])
    monkeypatch.setattr(mt5_accounts, "claim", lambda _: nullcontext())
    monkeypatch.setattr(mt5_accounts, "_probe",
                        lambda *_: (SimpleNamespace(login=456, server="ServerBaru"), []))
    settings = SimpleNamespace(cfg=lambda: {"bot": {"jurnal": str(tmp_path / "jurnal.db")}})
    accounts_mt5.set_metadata("AKUN 1", identity=["123", "ServerLama"], simbol={"XAUUSD": "XAUUSDc"})

    with pytest.raises(ValueError, match="Login terminal berubah"):
        mt5_accounts.use_current_login(settings, account, ["789", "ServerBaru"])
    assert accounts_mt5.metadata()["akun 1"]["identity"] == ["123", "ServerLama"]

    preview = mt5_accounts.current_identity(settings, account)
    mt5_accounts.use_current_login(settings, account, preview)
    assert accounts_mt5.metadata()["akun 1"] == {
        "identity": ["456", "ServerBaru"], "simbol": {"XAUUSD": "XAUUSDc"},
    }


def test_reset_journal_menu(monkeypatch, tmp_path):
    from contextlib import nullcontext

    from copier.journal import Journal
    from copier.models import Setup
    from panel import main
    db = tmp_path / "jurnal.db"
    j = Journal(str(db))
    j.add_setup(Setup("XAUUSD", "BUY", 3990, 3980, [4000]), "running")
    j.db.close()
    cfg = {"bot": {"jurnal": str(db)}}
    answers = iter(["batal", "RESET"])
    monkeypatch.setattr(main, "ask", lambda _: next(answers))
    monkeypatch.setattr(main, "pause", lambda: None)
    monkeypatch.setattr(main, "claim", lambda _: nullcontext())

    main.reset_journal(cfg)
    j = Journal(str(db))
    assert len(j.by_status("running")) == 1
    j.db.close()
    assert not list(tmp_path.glob("jurnal-cadangan-*.db"))

    main.reset_journal(cfg)
    j = Journal(str(db))
    assert not j.since(0)
    j.db.close()
    (backup,) = tmp_path.glob("jurnal-cadangan-*.db")
    j = Journal(str(backup))
    assert len(j.by_status("running")) == 1
    j.db.close()


def test_reset_bybit_journal_menu_ignores_stale_active_status(monkeypatch, tmp_path):
    from contextlib import nullcontext

    from copier.journal import Journal
    from copier.models import Setup
    from panel import main

    path = tmp_path / "bybit.db"
    journal = Journal(str(path))
    setup = Setup("PEPE", "BUY", 1, 0.9, [1.1])
    journal.add_setup(setup, "running")
    journal.set_kv("tg_offset", 123)
    journal.db.close()

    monkeypatch.setattr(main, "claim", lambda port: nullcontext())
    monkeypatch.setattr(main, "load_config", lambda _: {"bot": {"jurnal": str(path)}})
    monkeypatch.setattr(main, "ask", lambda _: "RESET")
    monkeypatch.setattr(main, "pause", lambda: None)
    main.reset_bybit_journal()

    journal = Journal(str(path))
    assert journal.since(0) == []
    assert journal.get_kv("tg_offset") == 123
    assert journal.add_setup(setup, "awaiting_confirm") == 1
    assert len(list(tmp_path.glob("bybit-cadangan-*.db"))) == 1
    journal.db.close()


def test_split_reset_to_presets(monkeypatch, cfg_file):
    from panel import settings
    s = Settings(str(cfg_file))
    s.set(("tp", "bagi", "2"), [30, 70])
    s.set(("tp", "bagi", "3"), [20, 30, 50])
    answers = iter(["bawaan"])
    monkeypatch.setattr(settings, "ask", lambda _: next(answers))
    monkeypatch.setattr(settings, "pause", lambda: None)
    s.edit(("tp", "bagi", "2"), "Setup dengan 2 TP", "split", 2, "")       # one row back to preset
    assert s.cfg()["tp"]["bagi"][2] == [50, 50] and s.cfg()["tp"]["bagi"][3] == [20, 30, 50]
    s.set(("tp", "bagi", "2"), [30, 70])
    split_items = [it for it in dict(SECTIONS)[settings.SPLIT_SECTION] if it[2] == "split"]
    answers = iter(["t", "y"])
    s.reset_all(split_items)                                               # "t" changes nothing
    assert s.cfg()["tp"]["bagi"][2] == [30, 70]
    s.reset_all(split_items)
    assert s.cfg()["tp"]["bagi"] == {1: [100], 2: [50, 50], 3: [50, 30, 20],
                                     4: [40, 30, 20, 10], 5: [30, 25, 20, 15, 10]}


def test_bybit_settings_cover_every_item_of_config_bybit(tmp_path):
    path = tmp_path / "S02.yaml"
    path.write_text(sopfiles.source_text({}, "bybit", "S02", "tes"), encoding="utf-8")
    s = Settings(str(path), bybit=True, sections=SOURCE_SECTIONS_BYBIT)
    assert s.name == "Bybit" and s.sections is SOURCE_SECTIONS_BYBIT
    labels = [label for _, items in SOURCE_SECTIONS_BYBIT for _, label, *_ in items]
    assert not any("lot" in l.lower() for l in labels) and "Leverage" in labels
    for _, items in SOURCE_SECTIONS_BYBIT:
        for p, label, kind, *_ in items:
            if kind != "float_or_null":
                assert _get(s.cfg(), p) is not None, p
            s.set(p, _get(s.cfg(), p))                # setiap kunci ada di file dan bisa ditulis
    s.set(("entry", "kedaluwarsa"), "manual")
    s.set(("bybit", "leverage"), 5)
    cfg = s.cfg()
    assert cfg["entry"]["kedaluwarsa"] == "manual" and cfg["bybit"]["leverage"] == 5
    assert "# manual = jam SOP | sinyal_waktu = waktu sinyal" in path.read_text(encoding="utf-8")


def test_run_menu_has_four_programs_and_checks_before_launch(monkeypatch, tmp_path):
    import panel.main as pm
    assert [p[0] for p in pm.PROGRAMS] == ["Pembaca sinyal", "Bot MT5 (Exness)", "Bot Bybit", "Bot Pengawas"]
    assert [p[2] for p in pm.PROGRAMS] == ["reader.main", "copier.main", "copier.main_bybit", "copier.supervisor"]
    for k in ("BYBIT_TG_BOT_TOKEN", "BYBIT_API_KEY", "BYBIT_API_SECRET", "TG_INBOX_CHANNEL_ID",
              "TG_ADMIN_USER_ID", "TG_BOT_TOKEN", "TG_API_ID", "TG_API_HASH", "SUPERVISOR_TG_BOT_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    assert "BYBIT_TG_BOT_TOKEN" in pm._can_start_bybit({})
    assert "TG_BOT_TOKEN" in pm._can_start_mt5({})
    assert "SUPERVISOR_TG_BOT_TOKEN" in pm._can_start_supervisor({})
    monkeypatch.setattr(pm, "ROOT", str(tmp_path))                  # tanpa sumber.yaml
    assert "belum ada sumber" in pm._can_start_reader({"pembaca": {"channel_sumber": ""}})
    for k in ("BYBIT_TG_BOT_TOKEN", "TG_INBOX_CHANNEL_ID", "TG_ADMIN_USER_ID", "TG_API_ID", "TG_API_HASH"):
        monkeypatch.setenv(k, "x")
    monkeypatch.setattr(pm.accounts, "load", lambda *a, **k: [])
    assert "belum ada akun Bybit" in pm._can_start_bybit({})
    monkeypatch.setattr(pm.accounts, "load", lambda *a, **k: ["akun"])
    assert pm._can_start_bybit({}) == ""

    launched = []
    monkeypatch.setenv("SUPERVISOR_TG_BOT_TOKEN", "123:fake")
    picks = iter([2, 3, None])                                       # pilih Bybit dan Pengawas
    monkeypatch.setattr(pm, "header", lambda: {})
    monkeypatch.setattr(pm, "choose", lambda items, **k: next(picks))
    monkeypatch.setattr(pm, "pause", lambda: None)
    monkeypatch.setattr(pm, "launch", lambda title, module, **kwargs: launched.append(module))
    monkeypatch.setattr(pm, "PROGRAMS", [(n, t, m, (lambda: False), c, note)
                                         for n, t, m, _, c, note in pm.PROGRAMS])
    pm.start_menu()
    assert launched == ["copier.main_bybit", "copier.supervisor"]


def test_readiness_bybit_section(monkeypatch, capsys):
    from panel import check
    from copier.broker_bybit import BybitBroker
    from tests.fake_bybit import FakeBybit
    from copier import accounts
    monkeypatch.setenv("BYBIT_TG_BOT_TOKEN", "x")
    monkeypatch.setattr(accounts, "load", lambda *a, **k: [accounts.Account("Utama", "demo", "k", "s")])
    monkeypatch.setattr("copier.telegram.TelegramBot.call", lambda self, m, **k: {"username": "bybitbot"})
    check.bybit(make_broker=lambda mode: BybitBroker(mode, session=FakeBybit(lambda: 1.7e9)))
    out = capsys.readouterr().out
    assert "@bybitbot" in out and "Akun Utama" in out and "terhubung ke Bybit DEMO" in out
    assert "one-way" in out and "margin cross" in out
    check.bybit(make_broker=lambda mode: BybitBroker(mode, session=FakeBybit(lambda: 1.7e9, hedge=True)))
    assert "harus one-way" in capsys.readouterr().out
    monkeypatch.setattr(accounts, "load", lambda *a, **k: [])
    check.bybit()
    assert "belum ada akun Bybit" in capsys.readouterr().out
