"""SOP per sumber: file setelan, migrasi dari susunan lama, bot MT5 dan bot Bybit (akun per
sumber), akun-bybit.env. Tanpa MT5, Bybit, atau Telegram sungguhan."""
import os
import sqlite3

import yaml

from copier import accounts, sopfiles
from copier.app import App
from copier.app_bybit import BybitAdapter, BybitApp
from copier.broker import Broker
from copier.broker_bybit import BybitBroker
from copier.config import load_config, with_defaults
from copier.journal import Journal
from copier.models import Setup
from reader import sources

from .fake_bybit import FakeBybit
from .fake_mt5 import FakeMT5
from .test_flow import ADMIN, INBOX, Clock, FakeBot

SETUP_GOLD = "SETUP\nPair: XAUUSD\nArah: BUY\nEntry: 3990\nSL: 3980\nTP1: 4000\nTP2: 4010\nKe: mt5"
SETUP_BTC = "SETUP\nPair: BTCUSDT\nArah: BUY\nEntry: 64900\nSL: 64000\nTP1: 66000\nTP2: 67000\nKe: bybit"


def write(path, text):
    os.makedirs(os.path.dirname(str(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def make_root(tmp_path, items, sops=None, mt5_universal=None, bybit_universal=None):
    """Folder sistem/ tiruan: config universal, sumber.yaml, dan sumber/<kode>.yaml."""
    root = str(tmp_path)
    write(tmp_path / "config.yaml", sopfiles.universal_text(
        mt5_universal or {"simbol": {"XAUUSD": "XAUUSDm"}, "bot": {"jurnal": "data/jurnal.db"}}, "mt5"))
    write(tmp_path / "config-bybit.yaml", sopfiles.universal_text(
        bybit_universal or {"simbol": {"BTCUSD": "BTCUSDT"}, "bot": {"jurnal": "data/jurnal-bybit.db"},
                            "bybit": {"nilai_posisi_maks_usdt": 100_000}}, "bybit"))
    sources.save(items, os.path.join(root, sources.PATH))
    for s in items:
        sopfiles.write_source(s.kode, s.ke, s.nama, (sops or {}).get(s.kode), root)
    return root


def src(kode, nama, ke="mt5", akun=""):
    return sources.Source(nama=nama, channel="-100" + kode[1:], kode=kode, ke=ke, akun=akun)


# --- file setelan ------------------------------------------------------------------
def test_templates_hold_every_menu_key_and_load_back():
    from panel.settings import (SOURCE_SECTIONS, SOURCE_SECTIONS_BYBIT, UNIVERSAL_SECTIONS,
                                UNIVERSAL_SECTIONS_BYBIT)
    from panel.yamledit import set_value
    for text, sections in ((sopfiles.source_text({}, "mt5", "S01", "a"), SOURCE_SECTIONS),
                           (sopfiles.source_text({}, "bybit", "S02", "b"), SOURCE_SECTIONS_BYBIT),
                           (sopfiles.universal_text({}, "mt5"), UNIVERSAL_SECTIONS),
                           (sopfiles.universal_text({}, "bybit"), UNIVERSAL_SECTIONS_BYBIT)):
        with_defaults(yaml.safe_load(text))
        for _, items in sections:
            for path, *_ in items:
                set_value(text, path, None)          # KeyError kalau kunci menu tidak ada di file
    assert "lot_maks" not in sopfiles.source_text({}, "bybit") and "leverage_mode" in \
        sopfiles.source_text({}, "bybit")


def test_effective_config_source_wins_over_universal(tmp_path):
    root = make_root(tmp_path, [src("S01", "a")], sops={"S01": {"risiko": {"persen": 2.5},
                                                              "claude": {"model_gambar": "opus"}}})
    cfg = sopfiles.effective(os.path.join(root, "config.yaml"), "S01", root)
    assert cfg["risiko"]["persen"] == 2.5 and cfg["simbol"] == {"XAUUSD": "XAUUSDm"}
    assert cfg["claude"]["model_gambar"] == "opus" and cfg["claude"]["model_teks"] == "haiku"


def test_new_code_never_reuses_a_deleted_one(tmp_path):
    root = make_root(tmp_path, [src("S01", "a"), src("S02", "b")])
    sopfiles.archive_source("S02", root)
    items = [src("S01", "a")]
    assert sopfiles.new_code(items, root) == "S03"


# --- migrasi dari susunan lama -----------------------------------------------------------
OLD_MT5 = {"akun": {"hanya_demo": True}, "risiko": {"mode": "persen", "persen": 10.0},
           "sop": {"be_jarak_aman_r": 0.01}, "pengaman": {"maks_setup_aktif": 5, "loss_harian_persen": 20.0},
           "simbol": {"XAUUSD": "XAUUSDm"}, "bot": {"jurnal": "data/jurnal.db"},
           "pembaca": {"channel_sumber": "-1001234567891", "topik_sumber": 78395, "nama_sumber": "outlook",
                       "claude_model": "opus"}}
OLD_BYBIT = {"risiko": {"persen": 2.0}, "bybit": {"leverage_mode": "tetap", "leverage": 7,
                                                  "nilai_posisi_maks_usdt": 500},
             "simbol": {"BTCUSD": "BTCUSDT"}, "bot": {"jurnal": "data/jurnal-bybit.db"}}


def test_migrate_old_layout_once(tmp_path):
    root, env = str(tmp_path), str(tmp_path / "depan")
    write(tmp_path / "config.yaml", yaml.safe_dump(OLD_MT5))
    write(tmp_path / "config-bybit.yaml", yaml.safe_dump(OLD_BYBIT))
    write(os.path.join(env, ".env"), "TG_BOT_TOKEN=x\nBYBIT_MODE=demo\nBYBIT_API_KEY=kunci123456789\n"
                                     "BYBIT_API_SECRET=rahasia\n")
    sources.save([sources.Source(nama="outlook", channel="-1001234567891", topik=[78395], olah="claude"),
                  sources.Source(nama="traderawam", channel="-1009", olah="traderawam", ke="bybit")],
                 os.path.join(root, sources.PATH))
    j = Journal(os.path.join(root, "data", "jurnal.db"))
    old_sid = j.add_setup(Setup("XAUUSD", "BUY", 3990, 3980, [4000], source="outlook"), "running")
    j.db.close()

    done = sopfiles.migrate(root, env)
    assert done and sopfiles.migrate(root, env) == []          # kedua kalinya tidak ada yang berubah

    s1, s2 = sources.load(os.path.join(root, sources.PATH))
    assert (s1.kode, s1.ke, s2.kode, s2.ke, s2.akun) == ("S01", "mt5", "S02", "bybit", "Utama")
    c1 = sopfiles.effective(os.path.join(root, "config.yaml"), "S01", root)
    assert (c1["risiko"]["persen"], c1["sop"]["be_jarak_aman_r"], c1["pengaman"]["maks_setup_aktif"],
            c1["pengaman"]["loss_harian_persen"], c1["claude"]["model_gambar"]) == (10.0, 0.01, 5, 20.0, "opus")
    c2 = sopfiles.effective(os.path.join(root, "config-bybit.yaml"), "S02", root)
    assert (c2["risiko"]["persen"], c2["bybit"]["leverage_mode"], c2["bybit"]["leverage"],
            c2["bybit"]["nilai_posisi_maks_usdt"]) == (2.0, "tetap", 7, 500)

    universal = yaml.safe_load(open(tmp_path / "config.yaml", encoding="utf-8"))
    assert "risiko" not in universal and universal["simbol"] == {"XAUUSD": "XAUUSDm"}
    assert "channel_sumber" not in universal["pembaca"]
    assert "risiko" not in yaml.safe_load(open(tmp_path / "config-bybit.yaml", encoding="utf-8"))
    backups = [d for d in os.listdir(tmp_path / "data") if d.startswith("cadangan-setelan-")]
    assert len(backups) == 1 and yaml.safe_load(open(tmp_path / "data" / backups[0] / "config.yaml",
                                                     encoding="utf-8"))["risiko"]["persen"] == 10.0

    (acc,) = accounts.load(env)
    assert (acc.nama, acc.mode, acc.key) == ("Utama", "demo", "kunci123456789")
    assert Journal(os.path.join(root, "data", "jurnal.db")).get(old_sid)["kode"] == "S01"


def test_migrate_source_from_old_config_channel(tmp_path):
    root = str(tmp_path)
    write(tmp_path / "config.yaml", yaml.safe_dump(OLD_MT5))
    sopfiles.migrate(root, str(tmp_path / "depan"))
    (s,) = sources.load(os.path.join(root, sources.PATH))
    assert (s.kode, s.nama, s.channel, s.topik, s.olah, s.ke) == \
        ("S01", "outlook", "-1001234567891", [78395], "claude", "mt5")


# --- bot MT5 dengan SOP per sumber -----------------------------------------------------------
_ids = iter(range(1, 100_000))


def mt5_app(tmp_path, items, sops=None):
    root = make_root(tmp_path, items, sops)
    clock = Clock()
    cfg = load_config(os.path.join(root, "config.yaml"))
    bot = FakeBot()
    app = App(cfg, Broker(cfg, mt5_module=FakeMT5(bid=3995.0)), Journal(":memory:", clock=clock), bot,
              INBOX, ADMIN, clock=clock, sources_root=root, universal_path=os.path.join(root, "config.yaml"))
    return app, bot, root


def post(app, text):
    app.handle_update({"update_id": 1, "channel_post": {
        "chat": {"id": INBOX}, "message_id": next(_ids), "date": app.clock(), "text": text}})


def with_code(text, code):
    head, rest = text.split("\n", 1)
    return f"{head}\nKode: {code}\n{rest}"


def test_mt5_rejects_setups_without_a_known_code(tmp_path):
    app, bot, _ = mt5_app(tmp_path, [src("S01", "trabar"), src("S02", "kripto", ke="bybit", akun="A")])
    post(app, SETUP_GOLD)
    assert "tanpa kode sumber" in bot.last
    post(app, with_code(SETUP_GOLD, "S09"))
    assert "S09 tidak dikenal" in bot.last
    post(app, with_code(SETUP_GOLD, "S02"))
    assert "untuk bot BYBIT" in bot.last
    assert not app.journal.by_status("awaiting_confirm")


def test_mt5_uses_the_sop_of_each_source_and_reloads_it_live(tmp_path):
    app, bot, root = mt5_app(tmp_path, [src("S01", "trabar"), src("S02", "lain")],
                             sops={"S01": {"risiko": {"persen": 1.0}},
                                   "S02": {"risiko": {"persen": 2.0}, "pengaman": {"maks_setup_aktif": 1}}})
    post(app, with_code(SETUP_GOLD, "S01"))
    assert "1.0% saldo" in bot.last and "Sumber: trabar (S01)" in bot.last
    post(app, with_code(SETUP_GOLD.replace("3990", "3991"), "S02"))
    assert "2.0% saldo" in bot.last and "Sumber: lain (S02)" in bot.last
    s01, s02 = app.journal.by_status("awaiting_confirm")
    assert (s01["kode"], s02["kode"]) == ("S01", "S02")

    app.handle_update({"update_id": 2, "callback_query": {"id": "q", "from": {"id": ADMIN},
                                                         "data": f"ya:{s02['id']}", "message": {"message_id": 1}}})
    assert app.journal.get(s02["id"])["status"] == "pending"
    post(app, with_code(SETUP_GOLD.replace("3990", "3993"), "S02"))      # S02 maks 1 setup aktif
    assert "dari sumber lain (S02)" in bot.last and "maksimal 1" in bot.last
    post(app, with_code(SETUP_GOLD.replace("3990", "3994"), "S01"))      # S01 tidak terpengaruh
    assert "perlu konfirmasi" in bot.last

    sop = sopfiles.sop_path("S01", root)
    from panel.yamledit import set_value
    write(sop, set_value(open(sop, encoding="utf-8").read(), ("risiko", "persen"), 3.0))
    st = os.stat(sop)
    os.utime(sop, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
    post(app, with_code(SETUP_GOLD.replace("3990", "3996"), "S01"))
    assert "3.0% saldo" in bot.last                                      # tanpa restart


RANGE_BUY = ("SETUP\nKode: S01\nPair: XAUUSD\nArah: BUY\nEntry: 3990\nEntry2: 3980\nSL: 3970\n"
             "TP1: 4010\nTP2: 4020\nKe: mt5")


def test_range_signal_market_inside_or_better_limit_outside(tmp_path):
    for bid, kind, word in ((3985.0, "MARKET", "di dalam range"),        # di dalam range
                            (3975.0, "MARKET", "lebih bagus dari range"),  # di bawah range (BUY)
                            (3995.0, "LIMIT", "")):                       # di atas range: limit di 3990
        root = make_root(tmp_path / str(int(bid)), [src("S01", "a")])
        clock = Clock()
        cfg = load_config(os.path.join(root, "config.yaml"))
        bot = FakeBot()
        app = App(cfg, Broker(cfg, mt5_module=FakeMT5(bid=bid)), Journal(":memory:", clock=clock), bot,
                  INBOX, ADMIN, clock=clock, sources_root=root,
                  universal_path=os.path.join(root, "config.yaml"))
        post(app, RANGE_BUY)
        assert f"XAUUSDm BUY {kind} @" in bot.last, bot.last
        if word:
            assert f"Harga {word} entry 3980–3990" in bot.last
        else:
            assert "@ 3990" in bot.last and "masuk market" not in bot.last


def test_range_market_runs_without_asking_in_auto_mode_but_doubt_still_asks(tmp_path):
    root = make_root(tmp_path, [src("S01", "a")], sops={"S01": {"gerbang": {"konfirmasi_wajib": False}}})
    clock = Clock()
    cfg = load_config(os.path.join(root, "config.yaml"))
    bot = FakeBot()
    app = App(cfg, Broker(cfg, mt5_module=FakeMT5(bid=3985.0)), Journal(":memory:", clock=clock), bot,
              INBOX, ADMIN, clock=clock, sources_root=root, universal_path=os.path.join(root, "config.yaml"))
    post(app, RANGE_BUY)                                    # harga di dalam range: langsung market
    assert "📌" in bot.last and "masuk market" in bot.last and "perlu konfirmasi" not in bot.last
    post(app, RANGE_BUY.replace("3990", "3991").replace("Ke: mt5", "Ragu: SL ditebak\nKe: mt5"))
    assert "perlu konfirmasi" in bot.last and "Pembaca ragu: SL ditebak" in bot.last


def test_range_signal_on_bybit_sell(tmp_path):
    app, bot, fakes = bybit_app(tmp_path, [src("S01", "a", "bybit", "A")], account_names=("A",))
    sell = ("SETUP\nKode: S01\nPair: BTCUSDT\nArah: SELL\nEntry: {e}\nEntry2: {e2}\nSL: 67500\n"
            "TP1: 63000\nTP2: 62000\nKe: bybit")
    post(app, sell.format(e=64900, e2=65500))       # harga ~65000 di dalam range 64900-65500
    assert "BTCUSDT SELL MARKET @" in bot.last and "di dalam range" in bot.last
    app, bot, fakes = bybit_app(tmp_path / "2", [src("S01", "a", "bybit", "A")], account_names=("A",))
    post(app, sell.format(e=65200, e2=65800))       # harga di bawah range (SELL): limit di 65200
    assert "BTCUSDT SELL LIMIT @ 65200" in bot.last and "masuk market" not in bot.last


def test_every_active_setup_has_exactly_one_guard(tmp_path):
    app, bot, _ = mt5_app(tmp_path, [src("S01", "a"), src("S02", "b")])
    j = app.journal
    ids = [j.add_setup(Setup("XAUUSD", "BUY", 3990, 3980, [4000], code=c), "pending") for c in ("S01", "S02", "", "S77")]
    owners = {sid: [p.code for p in app.profiles.all() if any(r["id"] == sid for r in p.guard.mine("pending"))]
              for sid in ids}
    assert owners == {ids[0]: ["S01"], ids[1]: ["S02"], ids[2]: [""], ids[3]: [""]}   # sisanya: cadangan


def test_source_daily_loss_counts_only_its_own_legs(tmp_path):
    app, bot, _ = mt5_app(tmp_path, [src("S01", "a"), src("S02", "b")],
                          sops={"S01": {"pengaman": {"loss_harian_dolar": 50}},
                                "S02": {"pengaman": {"loss_harian_dolar": 50}}})
    j = app.journal
    sid = j.add_setup(Setup("XAUUSD", "BUY", 3990, 3980, [4000], code="S01"), "done")
    j.add_leg(sid, 0, 111, 0.1, 4000, "closed_sl")
    leg = j.legs(sid)[0]
    j.update_leg(leg["id"], profit=-60.0, close_time=app.clock())
    s1, _ = app.profiles.get("S01")
    s2, _ = app.profiles.get("S02")
    assert "sumber a (S01)" in s1.gate.daily_loss_hit() and s2.gate.daily_loss_hit() == ""


# --- bot Bybit: akun per sumber ------------------------------------------------------------
def bybit_app(tmp_path, items, account_names=("A", "B")):
    root = make_root(tmp_path, items)
    clock = Clock()
    cfg = load_config(os.path.join(root, "config-bybit.yaml"))
    fakes = {n: FakeBybit(clock) for n in account_names}
    brokers = {n: BybitBroker("demo", session=f) for n, f in fakes.items()}
    bot = FakeBot()
    first = next(iter(brokers.values()))
    app = BybitApp(cfg, BybitAdapter(cfg, first, clock=clock), Journal(":memory:", clock=clock), bot,
                   INBOX, ADMIN, clock=clock, sources_root=root,
                   universal_path=os.path.join(root, "config-bybit.yaml"), brokers=brokers)
    return app, bot, fakes


def test_bybit_one_position_per_coin_per_account(tmp_path):
    items = [src("S01", "a", "bybit", "A"), src("S02", "b", "bybit", "A"), src("S03", "c", "bybit", "B")]
    app, bot, fakes = bybit_app(tmp_path, items)
    assert app.startup_checks() and "Akun A (S01, S02)" in bot.last and "Akun B (S03)" in bot.last
    post(app, with_code(SETUP_BTC, "S01"))
    assert "perlu konfirmasi" in bot.last and "Akun Bybit: A" in bot.last
    post(app, with_code(SETUP_BTC, "S02"))                       # akun sama -> bentrok
    assert "dari sumber S01" in bot.last and "per akun" in bot.last
    post(app, with_code(SETUP_BTC, "S03"))                       # akun lain -> boleh
    assert "perlu konfirmasi" in bot.last and "Akun Bybit: B" in bot.last
    s03 = [r for r in app.journal.by_status("awaiting_confirm") if r["kode"] == "S03"][0]
    app.handle_update({"update_id": 2, "callback_query": {"id": "q", "from": {"id": ADMIN},
                                                         "data": f"ya:{s03['id']}", "message": {"message_id": 1}}})
    assert fakes["B"].orders and not fakes["A"].orders           # order masuk ke akun B
    assert app.journal.get(s03["id"])["status"] == "pending"


def test_bybit_has_no_pair_list_and_finds_coins_itself(tmp_path):
    assert "simbol" not in yaml.safe_load(sopfiles.universal_text({"simbol": {"FOO": "BTCUSDT"}}, "bybit"))
    app, bot, _ = bybit_app(tmp_path, [src("S01", "a", "bybit", "A")], account_names=("A",))
    app.cfg["simbol"] = {"FOO": "BTCUSDT"}            # daftar lama, kalau masih ada, tidak dipakai
    p, _ = app.profiles.get("S01")
    p.cfg["simbol"] = {"FOO": "BTCUSDT"}
    post(app, with_code(SETUP_BTC.replace("BTCUSDT", "FOO"), "S01"))
    assert "ditolak" in bot.last and "FOOUSDT" in bot.last
    post(app, with_code(SETUP_BTC.replace("BTCUSDT", "$BTC"), "S01"))
    assert "perlu konfirmasi" in bot.last and "BTCUSDT" in bot.last


def test_migrate_drops_old_bybit_pair_list(tmp_path):
    write(tmp_path / "config-bybit.yaml", "akun:\n  hanya_demo: true\nsimbol:\n  BTCUSD: BTCUSDT\n"
                                          "bybit:\n  nilai_posisi_maks_usdt: 700\n")
    sopfiles.migrate(str(tmp_path), str(tmp_path / "depan"))
    data = yaml.safe_load(open(tmp_path / "config-bybit.yaml", encoding="utf-8"))
    assert "simbol" not in data and data["bybit"]["nilai_posisi_maks_usdt"] == 700
    assert data["bot"]["jurnal"] == "data/jurnal-bybit.db"


def test_bybit_source_with_missing_account_is_refused(tmp_path):
    app, bot, _ = bybit_app(tmp_path, [src("S01", "a", "bybit", "A"), src("S02", "b", "bybit", "Z")])
    post(app, with_code(SETUP_BTC, "S02"))
    assert "akun Bybit 'Z' tidak ada" in bot.last
    assert not app.startup_checks() and "'Z'" in bot.last


# --- akun-bybit.env -------------------------------------------------------------------
def test_accounts_file_roundtrip_and_legacy_env(tmp_path):
    folder = str(tmp_path)
    assert accounts.load(folder, environ={}) == []
    legacy = accounts.load(folder, environ={"BYBIT_API_KEY": "abcdefghijkl", "BYBIT_API_SECRET": "s",
                                            "BYBIT_MODE": "testnet"})
    assert [(a.nama, a.mode) for a in legacy] == [("Utama", "testnet")]
    accs = [accounts.Account("Utama", "demo", "abcdefghijklmn", "s1"), accounts.Account("Sub STX", "live", "k2", "s2")]
    accounts.save(accs, folder)
    assert accounts.load(folder, environ={"BYBIT_API_KEY": "abaikan", "BYBIT_API_SECRET": "x"}) == accs
    assert accs[0].masked() == "abcd…klmn" and "abcdefghijklmn" not in accs[0].describe()
    assert accounts.by_name(accs, "sub stx") is accs[1]
    assert not accounts.valid_name("a=b") and accounts.valid_name("Sub STX")
