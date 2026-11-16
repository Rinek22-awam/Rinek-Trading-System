"""Alur lengkap bot Bybit di bursa tiruan: kotak masuk -> gerbang -> konfirmasi -> satu entry
-> TP parsial reduce-only -> BE -> trailing -> SL. Sama dengan alur MT5 di test_flow.py."""
from copier.app_bybit import BybitAdapter, BybitApp, parse_ticket
from copier.broker_bybit import BybitBroker
from copier.config import with_defaults
from copier.journal import Journal

from .fake_bybit import FakeBybit
from .test_flow import ADMIN, INBOX, Clock, FakeBot

MARKET = """SETUP
Pair: BTCUSD
Arah: BUY
Order: MARKET
SL: 64000
TP1: 65500
TP2: 66000
TP3: 66500
Sumber: tes"""

LIMIT = """SETUP
Pair: BTCUSD
Arah: BUY
Entry: 64800
SL: 64000
TP1: 65500
TP2: 66000
Sumber: tes"""


def make(cfg=None, hedge=False):
    clock = Clock()
    cfg = with_defaults({"simbol": {"BTCUSD": "BTCUSDT"},
                         "bybit": {"leverage": 10, "nilai_posisi_maks_usdt": 100_000},
                         **(cfg or {})})
    ex = FakeBybit(clock, hedge=hedge)
    adapter = BybitAdapter(cfg, BybitBroker("demo", session=ex), clock=clock)
    bot = FakeBot()
    app = BybitApp(cfg, adapter, Journal(":memory:", clock=clock), bot, INBOX, ADMIN, clock=clock)
    return app, ex, bot, clock


_ids = iter(range(1, 10_000))


def post(app, text, dest="bybit"):
    """Posting ke kotak masuk. Label "Ke: bybit" ditambahkan kecuali dest=None."""
    if dest and "\nKe:" not in text:
        text = f"{text}\nKe: {dest}"
    app.handle_update({"update_id": 1, "channel_post": {
        "chat": {"id": INBOX}, "message_id": next(_ids), "date": app.clock(), "text": text}})


def press(app, data):
    app.handle_update({"update_id": 2, "callback_query": {
        "id": "q", "from": {"id": ADMIN}, "data": data, "message": {"message_id": 1}}})


def latest(app):
    return app.journal.since(0)[-1]


def run_market(app, ex, clock):
    post(app, MARKET)
    sid = latest(app)["id"]
    press(app, f"ya:{sid}")
    clock.t += 2
    app.tick()            # entry sudah terisi -> TP parsial dipasang
    return sid


def test_market_setup_one_entry_then_partial_tps():
    app, ex, bot, clock = make()
    sid = run_market(app, ex, clock)
    entries = ex.placed(reduceOnly=False)
    assert len(entries) == 1 and entries[0]["stopLoss"] == "64000"
    legs = app.journal.legs(sid)
    assert len(legs) == 3 and all(l["status"] == "open" for l in legs)
    tps = ex.placed(reduceOnly=True, orderStatus="New")
    assert sorted(float(o["price"]) for o in tps) == [65500, 66000, 66500]
    total = float(entries[0]["qty"])
    assert abs(sum(float(o["qty"]) for o in tps) - total) < 1e-9
    assert [parse_ticket(l["ticket"])[3] for l in legs] == [0, 1, 2]
    assert latest(app)["status"] == "running"
    confirm = bot.sent[0]["text"]
    assert "Qty total" in confirm and "koin)" in confirm and " lot" not in confirm
    assert "masuk market, koin" in bot.edits[-1]["text"]
    assert ("set_leverage", {"category": "linear", "symbol": "BTCUSDT", "buyLeverage": "150",
                             "sellLeverage": "150"}) in ex.calls       # leverage maks BTC


def test_tp1_moves_sl_to_be_then_trailing_then_sl_closes_rest():
    app, ex, bot, clock = make()
    sid = run_market(app, ex, clock)
    fill = ex.pos["BTCUSDT"]["avg"]

    ex.set_price(65510)                   # TP1 tersentuh
    clock.t += 2
    app.tick()
    legs = app.journal.legs(sid)
    assert legs[0]["status"] == "closed_tp"
    be = ex.pos["BTCUSDT"]["sl"]
    assert fill < be < fill + 200          # BE dikunci sedikit di atas entry
    assert "SL posisi" in bot.texts()

    ex.set_price(66010)                   # TP2 -> trailing: SL ke TP1
    clock.t += 2
    app.tick()
    assert app.journal.legs(sid)[1]["status"] == "closed_tp"
    assert ex.pos["BTCUSDT"]["sl"] == 65500

    ex.set_price(65400)                   # balik kena SL baru
    clock.t += 2
    app.tick()
    legs = app.journal.legs(sid)
    assert [l["status"] for l in legs] == ["closed_tp", "closed_tp", "closed_sl"]
    row = latest(app)
    assert row["status"] == "done" and row["pnl"] > 0
    assert "BTCUSDT" not in ex.pos
    assert not ex.placed(reduceOnly=True, orderStatus="New")


def test_sl_before_any_tp_closes_all_legs():
    app, ex, bot, clock = make()
    sid = run_market(app, ex, clock)
    ex.set_price(63990)
    clock.t += 2
    app.tick()
    assert [l["status"] for l in app.journal.legs(sid)] == ["closed_sl"] * 3
    row = latest(app)
    assert row["status"] == "done" and row["pnl"] < 0
    assert "Kena SL" in bot.texts()


def test_limit_waits_fills_then_gets_tps():
    app, ex, bot, clock = make()
    post(app, LIMIT)
    sid = latest(app)["id"]
    press(app, f"ya:{sid}")
    assert latest(app)["status"] == "pending"
    clock.t += 2
    app.tick()
    assert not ex.placed(reduceOnly=True)          # belum terisi: belum ada TP
    ex.set_price(64790)
    clock.t += 2
    app.tick()
    assert latest(app)["status"] == "running"
    assert len(ex.placed(reduceOnly=True, orderStatus="New")) == 2
    assert "ke-fill" in bot.texts()


def test_partial_bybit_entry_cancels_remainder_and_sizes_tps_after_final_fill(monkeypatch):
    from .fake_bybit import BybitError

    app, ex, _, clock = make()
    post(app, LIMIT)
    sid = latest(app)["id"]
    press(app, f"ya:{sid}")
    entry = ex.placed(reduceOnly=False)[0]
    ex._fill(entry, 64800, qty=0.03)
    ex.fail["cancel_order"] = BybitError(10001, "cancel unavailable")
    app.tick()
    assert entry["orderStatus"] == "PartiallyFilled"
    assert latest(app)["status"] == "running"
    assert not ex.placed(reduceOnly=True)

    cancel = ex.cancel_order

    def cancel_after_more_fills(**kw):
        ex._fill(entry, 64800, qty=0.01)
        return cancel(**kw)

    monkeypatch.setattr(ex, "cancel_order", cancel_after_more_fills)
    clock.t += 2
    app.tick()
    assert entry["orderStatus"] == "Cancelled"
    monkeypatch.setattr(ex, "cancel_order", cancel)
    tps = ex.placed(reduceOnly=True, orderStatus="New")
    assert [(o["price"], o["qty"]) for o in tps] == [("65500", "0.021"), ("66000", "0.019")]

    # Read from the journal and exchange again instead of depending on in-memory fill state.
    restarted = BybitApp(app.cfg, app.broker, app.journal, FakeBot(), INBOX, ADMIN, clock=clock)
    restarted.tick()
    assert len(ex.placed(reduceOnly=True, orderStatus="New")) == 2
    ex.set_price(65510)
    restarted.tick()
    assert restarted.journal.legs(sid)[0]["status"] == "closed_tp"
    assert ex.pos["BTCUSDT"]["size"] == 0.019
    assert ex.pos["BTCUSDT"]["sl"] > 64800


def test_unfilled_limit_expires():
    app, ex, bot, clock = make()
    post(app, LIMIT)
    sid = latest(app)["id"]
    press(app, f"ya:{sid}")
    clock.t += 13 * 3600
    app.tick()
    clock.t += 2
    app.tick()
    assert ex.placed(reduceOnly=False)[0]["orderStatus"] == "Cancelled"
    assert app.journal.get(sid)["status"] in ("expired", "cancelled")


def test_console_reports_restored_entry_and_tp_without_polling_spam(caplog):
    app, ex, _, clock = make()
    with caplog.at_level("INFO", logger="copier"):
        post(app, LIMIT)
        sid = latest(app)["id"]
        press(app, f"ya:{sid}")
        assert "Sinyal diterima" in caplog.text
        assert "BUY LIMIT diterima broker" in caplog.text

        restarted = BybitApp(app.cfg, app.broker, app.journal, FakeBot(), INBOX, ADMIN, clock=clock)
        restarted.log_startup()
        restarted.tick()
        restarted.tick()
        assert "1 setup aktif dimuat dari jurnal" in caplog.text
        assert caplog.text.count("Order masih menunggu di broker") == 1

        ex.set_price(64790)
        restarted.tick()
        restarted.tick()
        assert "Entry terisi" in caplog.text
        assert caplog.text.count("reduce-only diterima Bybit") == 2


def test_one_active_setup_per_coin():
    app, ex, bot, clock = make()
    run_market(app, ex, clock)
    post(app, LIMIT)
    row = latest(app)
    assert row["status"] == "rejected" and "satu posisi per koin" in row["reason"]


def test_close_setup_button_closes_position():
    app, ex, bot, clock = make()
    sid = run_market(app, ex, clock)
    press(app, f"tutup:{sid}")
    assert "BTCUSDT" not in ex.pos
    clock.t += 2
    app.tick()
    assert latest(app)["status"] == "done"


def test_only_posts_labelled_bybit_are_taken():
    app, ex, bot, clock = make()
    post(app, MARKET, dest="mt5")
    post(app, MARKET, dest=None)
    assert app.journal.since(0) == [] and bot.sent == []
    post(app, MARKET)
    assert latest(app)["status"] == "awaiting_confirm"


def test_usdt_coin_not_in_list_still_accepted():
    app, ex, bot, clock = make()
    post(app, "SETUP\nPair: POPCATUSDT\nArah: BUY\nEntry: 0.0365\nSL: 0.0350\nTP1: 0.0380\nSumber: tes")
    row = latest(app)
    assert row["status"] == "awaiting_confirm" and row["broker_symbol"] == "POPCATUSDT"


def test_startup_refuses_hedge_mode_and_live(caplog):
    app, ex, bot, clock = make(hedge=True)
    assert not app.startup_checks() and "hedge mode" in caplog.text and not bot.sent
    app, ex, bot, clock = make()
    assert app.startup_checks() and "Bot Bybit nyala" in bot.last
    app.broker.b.mode = "live"
    assert not app.startup_checks() and "BYBIT_MODE=live" in caplog.text


def test_source_notice_targets_setup_of_same_coin():
    app, ex, bot, clock = make()
    post(app, LIMIT)                                   # BTC dari sumber "tes"
    sid = latest(app)["id"]
    press(app, f"ya:{sid}")
    post(app, "UPDATE\nSumber: tes\nPair: ETHUSDT\nAksi: CANCEL\nTeks: ETH dibatalkan")
    assert "Tidak ada setup aktif ETHUSDT" in bot.last
    post(app, "UPDATE\nSumber: tes\nPair: BTCUSDT\nAksi: CANCEL\nTeks: BTC dibatalkan")
    assert not bot.sent[-1]["buttons"]
    assert ex.placed(reduceOnly=False, orderStatus="Cancelled")
    app.tick()
    assert app.journal.get(sid)["status"] == "cancelled"


def test_source_cancel_invalidates_pending_confirmation():
    app, ex, bot, clock = make()
    post(app, LIMIT)
    sid = latest(app)["id"]
    post(app, "UPDATE\nSumber: tes\nPair: BTCUSDT\nAksi: CANCEL\nTeks: BTC dibatalkan")
    assert app.journal.get(sid)["status"] == "cancelled"
    assert "dibatalkan oleh sumber sinyal" in bot.edits[-1]["text"]
    press(app, f"ya:{sid}")
    assert not ex.placed()


def _valid(clock, hours):
    from datetime import datetime
    from copier.setup_format import WIB
    return datetime.fromtimestamp(clock.t + hours * 3600, WIB).strftime("%Y-%m-%d %H:%M") + " WIB"


def test_time_only_mode_ignores_price_and_expires_at_signal_deadline():
    app, ex, bot, clock = make({"entry": {"kedaluwarsa": "sinyal_waktu"}})
    post(app, LIMIT + f"\nBerlaku: {_valid(clock, 48)}\nBatal sebelum entry: < 65500")
    sid = latest(app)["id"]
    assert "(dari sinyal)" in bot.sent[-1]["text"]
    assert "Batal sebelum entry" not in bot.sent[-1]["text"]
    app.tick()
    press(app, f"ya:{sid}")
    clock.t += 13 * 3600                       # lewat 12 jam: masih berlaku menurut sinyal
    app.tick()
    assert app.journal.get(sid)["status"] == "pending"
    clock.t += 36 * 3600                       # lewat 48 jam: batal
    app.tick()
    clock.t += 2
    app.tick()
    assert app.journal.get(sid)["status"] in ("expired", "cancelled")
    assert "batas waktu" in bot.texts()


def test_manual_mode_ignores_signal_time_and_price():
    app, ex, bot, clock = make({"entry": {"kedaluwarsa": "manual", "limit_kedaluwarsa_jam": 12}})
    post(app, LIMIT + f"\nBerlaku: {_valid(clock, -1)}\nBatal sebelum entry: < 65500")
    sid = latest(app)["id"]
    assert "(12 jam)" in bot.sent[-1]["text"]
    app.tick()
    press(app, f"ya:{sid}")
    app.tick()
    assert latest(app)["status"] == "pending"
    clock.t += 13 * 3600
    app.tick()
    clock.t += 2
    app.tick()
    assert app.journal.get(sid)["status"] in ("expired", "cancelled")


def test_signal_mode_without_validity_falls_back_to_hours_and_expired_signal_rejected():
    app, ex, bot, clock = make({"entry": {"kedaluwarsa": "sinyal", "limit_kedaluwarsa_jam": 6}})
    post(app, LIMIT)
    assert "(6 jam)" in bot.sent[-1]["text"]
    post(app, LIMIT.replace("Entry: 64800", "Entry: 64700") + f"\nBerlaku: {_valid(clock, -1)}")
    row = latest(app)
    assert row["status"] == "rejected" and "Masa berlaku sinyal sudah lewat" in row["reason"]


def test_price_limit_cancels_pending_and_retries_after_price_recovers(monkeypatch):
    app, ex, bot, clock = make({"entry": {"kedaluwarsa": "sinyal"}})
    post(app, LIMIT + f"\nBerlaku: {_valid(clock, 48)}\nBatal sebelum entry: > 65200")
    sid = latest(app)["id"]
    press(app, f"ya:{sid}")
    assert latest(app)["watch_entry_price"] == 1
    app.profiles.for_row(latest(app)).guard.cfg["entry"]["kedaluwarsa"] = "manual"
    ex.set_price(65200)
    app.tick()
    assert latest(app)["pending_cancel"] is None

    with monkeypatch.context() as patch:
        patch.setattr(app.broker, "cancel", lambda ticket: (False, "sementara gagal"))
        ex.set_price(65201)
        app.tick()
    assert latest(app)["status"] == "pending"
    assert latest(app)["pending_cancel"] == "cancelled"

    ex.set_price(65199)
    app.tick()
    app.tick()
    assert latest(app)["status"] == "cancelled"
    assert "65201 > 65200" in bot.last
    assert not ex.pos


def test_price_limit_cancels_while_waiting_for_confirmation():
    app, ex, _, _ = make({"entry": {"kedaluwarsa": "sinyal"}})
    post(app, LIMIT + "\nBatal sebelum entry: > 65200")
    sid = latest(app)["id"]
    ex.set_price(65201)
    app.tick()
    ex.set_price(65199)
    press(app, f"ya:{sid}")
    assert latest(app)["status"] == "cancelled"
    assert not ex.orders


def test_price_limit_does_not_cancel_a_filled_setup():
    app, ex, _, _ = make({"entry": {"kedaluwarsa": "sinyal"}})
    post(app, LIMIT + "\nBatal sebelum entry: > 65200")
    press(app, f"ya:{latest(app)['id']}")
    ex.set_price(64799)
    app.tick()
    ex.set_price(65201)
    app.tick()
    assert latest(app)["status"] == "running"
    assert ex.pos["BTCUSDT"]["size"] > 0


def test_signal_deadline_is_rechecked_on_confirmation():
    app, ex, _, clock = make({"entry": {"kedaluwarsa": "sinyal"}})
    post(app, LIMIT + f"\nBerlaku: {_valid(clock, 1)}")
    sid = latest(app)["id"]
    clock.t += 3600
    press(app, f"ya:{sid}")
    assert "Masa berlaku sinyal sudah lewat" in latest(app)["reason"]
    assert not ex.orders
