"""Skenario ujung-ke-ujung: postingan ASLI TraderAwam -> pembaca -> kotak masuk -> bot Bybit
-> bursa tiruan. Harga digerakkan seperti pasar sungguhan, lalu dicek apa yang dilakukan bot."""
from copier.app_bybit import BybitAdapter, BybitApp
from copier.broker_bybit import BybitBroker
from copier.config import with_defaults
from copier.journal import Journal
from reader.textsignal import build_notice_update, build_text_setup

from .fake_bybit import FakeBybit
from .test_flow import ADMIN, INBOX, Clock, FakeBot

# Sinyal STX asli (dipendekkan bagian statistiknya). Entry 0.3314, SL 0.311, TP 0.3518/0.3722/0.3875/0.4079
STX = """🟢 LONG · STXUSDT (https://www.tradingview.com/chart/?symbol=BYBIT:STXUSDT.P)
Swing · Bybit Perp 4h
⚡ Risk Medium

🧩 Setup
Manual · Retest level 0.3314 · SL di bawah low 0.3145

📍 Entry   0.3314   (limit)

🎯 TP1   0.3518   +6.16% · 1.0R
🎯 TP2   0.3722   +12.31% · 2.0R
🎯 TP3   0.3875   +16.93% · 2.8R
🎯 TP4   0.4079   +23.08% · 3.8R

🛑 SL    0.311   -6.16%

⏳ Valid s/d {valid} WIB
❌ Batal jika > 0.365 sebelum entry

📊 Swing 90d live
TP1 3 · TP2 0 · TP3 0 · TP4 2 · SL 7
"""


class World:
    """Satu dunia uji: bursa, bot Bybit, jam, dan Telegram tiruan."""

    def __init__(self, cfg=None, journal=None, clock=None, ex=None):
        if clock is None:      # jam sekarang: "Valid s/d 11 Nov" tanpa tahun dibaca tahun berjalan
            import time
            clock = Clock()
            clock.t = float(int(time.time()))
        self.clock = clock
        base = {"bybit": {"leverage": 10, "nilai_posisi_maks_usdt": 100_000},
                "entry": {"kedaluwarsa": "sinyal", "limit_kedaluwarsa_jam": 12},
                "gerbang": {"jarak_maks_dari_harga_persen": 10.0}}
        base.update(cfg or {})
        self.cfg = with_defaults(base)
        self.ex = ex or FakeBybit(self.clock)
        self.journal = journal or Journal(":memory:", clock=self.clock)
        self.bot = FakeBot()
        self.app = BybitApp(self.cfg, BybitAdapter(self.cfg, BybitBroker("demo", session=self.ex),
                                                   clock=self.clock),
                            self.journal, self.bot, INBOX, ADMIN, clock=self.clock)
        self.msg = 0

    def valid(self, hours=72):
        from datetime import datetime
        from copier.setup_format import WIB
        return datetime.fromtimestamp(self.clock.t + hours * 3600, WIB).strftime("%d %b %H:%M")

    def source_post(self, text):
        """Postingan di channel sumber -> apa yang pembaca kirim ke kotak masuk."""
        setup, _ = build_text_setup(text, "traderawam", "bybit")
        out = setup or build_notice_update(text, "traderawam", "bybit")
        if out:
            self.inbox(out)
        return out

    def inbox(self, text):
        self.msg += 1
        self.app.handle_update({"update_id": self.msg, "channel_post": {
            "chat": {"id": INBOX}, "message_id": self.msg, "date": self.clock(), "text": text}})

    def press(self, data):
        self.app.handle_update({"update_id": 0, "callback_query": {
            "id": "q", "from": {"id": ADMIN}, "data": data, "message": {"message_id": 1}}})

    def command(self, text):
        self.app.handle_update({"update_id": 0, "message": {
            "chat": {"id": ADMIN}, "from": {"id": ADMIN}, "text": text}})

    def price(self, p, sym="STXUSDT", ticks=1):
        self.ex.set_price(p, sym)
        for _ in range(ticks):
            self.clock.t += 2
            self.app.tick()

    def setup(self, sid=None):
        rows = self.journal.since(0)
        return self.journal.get(sid) if sid else rows[-1]

    def legs(self, sid):
        return [l["status"] for l in self.journal.legs(sid)]

    def stx_signal(self, **kw):
        self.source_post(STX.format(valid=self.valid(**kw)))
        return self.setup()["id"]

    def tp_orders(self, sym="STXUSDT"):
        return sorted(float(o["price"]) for o in self.ex.placed(symbol=sym, reduceOnly=True, orderStatus="New"))


# --- 1. alur normal: sinyal -> konfirmasi -> fill -> TP1 -> BE -> TP2 -> trailing -> SL ------------
def test_full_long_journey_from_real_post():
    w = World()
    sid = w.stx_signal()
    row = w.setup(sid)
    assert row["status"] == "awaiting_confirm" and row["broker_symbol"] == "STXUSDT"
    confirm = w.bot.sent[-1]["text"]
    assert "(dari sinyal)" in confirm and "TP4 0.4079" in confirm and "koin" in confirm
    w.press(f"ya:{sid}")
    assert w.setup(sid)["status"] == "pending"

    w.price(0.3400)                                    # belum sampai entry
    assert w.setup(sid)["status"] == "pending" and w.tp_orders() == []

    w.price(0.3313)                                    # limit terisi
    assert w.setup(sid)["status"] == "running"
    assert w.tp_orders() == [0.3518, 0.3722, 0.3875, 0.4079]     # 4 TP parsial terpasang
    assert w.ex.pos["STXUSDT"]["sl"] == 0.311

    w.price(0.3520)                                    # TP1
    assert w.legs(sid)[0] == "closed_tp"
    be = w.ex.pos["STXUSDT"]["sl"]
    assert 0.3314 < be < 0.3340, be                    # BE + jarak aman 10% R

    w.price(0.3725)                                    # TP2 -> trailing ke TP1
    assert w.legs(sid)[:2] == ["closed_tp", "closed_tp"]
    assert w.ex.pos["STXUSDT"]["sl"] == 0.3518

    w.price(0.3500)                                    # balik, kena SL di TP1
    assert w.legs(sid) == ["closed_tp", "closed_tp", "closed_sl", "closed_sl"]
    assert w.setup(sid)["status"] == "done" and w.setup(sid)["pnl"] > 0
    assert w.tp_orders() == [] and "STXUSDT" not in w.ex.pos
    text = w.bot.texts()
    for word in ("ke-fill", "TP1 kena", "SL posisi", "TP2 kena", "Kena SL", "selesai"):
        assert word in text, word
    assert text.count("SL posisi") == 2                 # BE + trailing: dua pesan, bukan per kaki
    assert "Risiko 1" in confirm                        # ~1% dari 10.000, bukan 0.10 (lot_maks MT5)


# --- 2. SHORT: arah kebalikan ----------------------------------------------------------------
def test_short_journey_be_and_all_tps():
    w = World()
    short = (STX.format(valid=w.valid()).replace("LONG", "SHORT").replace("0.3314   (limit)", "0.3340   (limit)")
             .replace("TP1   0.3518", "TP1   0.3200").replace("TP2   0.3722", "TP2   0.3100")
             .replace("TP3   0.3875", "TP3   0.3000").replace("TP4   0.4079", "TP4   0.2900")
             .replace("SL    0.311", "SL    0.3450"))
    w.source_post(short)
    sid = w.setup()["id"]
    w.press(f"ya:{sid}")
    w.price(0.3341)                                    # SELL limit terisi (bid >= 0.334)
    assert w.setup(sid)["status"] == "running" and w.ex.pos["STXUSDT"]["side"] == "Sell"
    w.price(0.3195)                                    # TP1 (ask <= 0.32)
    assert 0.3300 < w.ex.pos["STXUSDT"]["sl"] < 0.3340  # BE di bawah entry untuk SELL
    for p in (0.3095, 0.2995, 0.2890):
        w.price(p)
    assert w.legs(sid) == ["closed_tp"] * 4 and w.setup(sid)["status"] == "done"


# --- 3. sinyal dobel -----------------------------------------------------------------------
def test_same_signal_posted_twice_is_rejected_as_double():
    w = World()
    first = w.stx_signal()
    w.stx_signal()
    second = w.setup()
    assert second["id"] != first and second["status"] == "rejected"
    assert "Dobel" in second["reason"] or "satu posisi" in second["reason"]


def test_same_telegram_message_delivered_twice_is_processed_once():
    w = World()
    setup, _ = build_text_setup(STX.format(valid=w.valid()), "traderawam", "bybit")
    upd = {"update_id": 1, "channel_post": {"chat": {"id": INBOX}, "message_id": 77,
                                            "date": w.clock(), "text": setup}}
    w.app.handle_update(upd)
    w.app.handle_update(upd)
    assert len(w.journal.since(0)) == 1


def test_second_signal_same_coin_while_first_waits_for_confirmation():
    """Dua setup koin yang sama sama-sama menunggu konfirmasi, lalu dua-duanya ditekan Ya.
    Bybit one-way hanya punya SATU posisi per koin: yang kedua harus ditolak."""
    w = World()
    a = w.stx_signal()
    other = STX.format(valid=w.valid()).replace("0.3314   (limit)", "0.3250   (limit)")
    w.source_post(other)
    b = w.setup()["id"]
    w.press(f"ya:{a}")
    w.press(f"ya:{b}")
    entries = w.ex.placed(symbol="STXUSDT", reduceOnly=False)
    assert len(entries) == 1, "dua entry di koin yang sama"
    assert w.setup(b)["status"] in ("rejected", "declined")


# --- 4. pembatalan dari sumber dan dari user -------------------------------------------------
def test_source_cancel_notice_cancels_limit_immediately():
    w = World()
    sid = w.stx_signal()
    w.press(f"ya:{sid}")
    w.source_post("🔔 TraderAwam · STXUSDT · LONG\n07 Nov 2026 20:44 WIB\n\n"
                  "Setup dibatalkan - harga lari melewati invalidasi sebelum entry")
    assert not w.bot.sent[-1]["buttons"]
    assert w.ex.placed(symbol="STXUSDT", reduceOnly=False)[0]["orderStatus"] == "Cancelled"
    w.price(0.3400)
    assert w.setup(sid)["status"] == "cancelled"


def test_source_sl_notice_gives_close_button():
    w = World()
    sid = w.stx_signal()
    w.press(f"ya:{sid}")
    w.price(0.3313)
    w.source_post("🔔 TraderAwam · STXUSDT · LONG\nSWING\n\nSL tersentuh menurut high/low candle 1m.")
    assert f"tutup:{sid}" in str(w.bot.sent[-1]["buttons"])
    assert "STXUSDT" in w.ex.pos                       # bot tidak menutup sendiri
    w.press(f"tutup:{sid}")
    w.price(0.3300)
    assert "STXUSDT" not in w.ex.pos and w.setup(sid)["status"] == "done"


def test_unanswered_confirmation_expires_without_orders():
    w = World()
    sid = w.stx_signal()
    w.clock.t += 31 * 60
    w.app.tick()
    assert w.setup(sid)["status"] == "expired" and not w.ex.placed(symbol="STXUSDT")


# --- 5. kedaluwarsa limit menurut sinyal ------------------------------------------------------
def test_limit_cancelled_when_signal_validity_ends():
    w = World()
    sid = w.stx_signal(hours=24)
    w.press(f"ya:{sid}")
    w.price(0.3400)
    w.clock.t += 13 * 3600
    w.price(0.3400)
    assert w.setup(sid)["status"] == "pending"          # 12 jam lewat, sinyal masih berlaku
    w.clock.t += 12 * 3600
    w.price(0.3400, ticks=2)
    assert w.setup(sid)["status"] in ("expired", "cancelled")


# --- 6. kejadian di luar bot ------------------------------------------------------------------
def test_restart_mid_position_keeps_guarding():
    w = World()
    sid = w.stx_signal()
    w.press(f"ya:{sid}")
    w.price(0.3313)
    w2 = World(journal=w.journal, clock=w.clock, ex=w.ex)       # bot mati lalu nyala lagi
    w2.price(0.3520)
    assert w2.legs(sid)[0] == "closed_tp" and w2.ex.pos["STXUSDT"]["sl"] > 0.3314


def test_position_closed_manually_in_bybit_app():
    w = World()
    sid = w.stx_signal()
    w.press(f"ya:{sid}")
    w.price(0.3313)
    BybitBroker("demo", session=w.ex).close("STXUSDT")       # user menutup di aplikasi Bybit
    w.price(0.3320)
    assert w.legs(sid) == ["closed"] * 4 and w.setup(sid)["status"] == "done"
    assert w.tp_orders() == []


def test_price_gaps_through_tp1_right_after_fill():
    w = World()
    sid = w.stx_signal()
    w.press(f"ya:{sid}")
    w.ex.set_price(0.3313, "STXUSDT")                  # terisi tanpa bot sempat melihat
    w.ex.set_price(0.3530, "STXUSDT")                  # langsung loncat lewat TP1
    w.price(0.3530)                                    # putaran 1: TP dipasang, TP1 langsung terisi
    assert w.ex.pos["STXUSDT"]["sl"] > 0.3314          # BE sudah di putaran yang sama
    w.price(0.3530)                                    # putaran 2: TP1 tercatat
    assert w.legs(sid)[0] == "closed_tp"


def test_bybit_rejects_order_for_balance():
    from .fake_bybit import BybitError
    w = World()
    sid = w.stx_signal()
    w.ex.fail["place_order"] = BybitError(110007, "ab not enough for new order")
    w.press(f"ya:{sid}")
    row = w.setup(sid)
    assert row["status"] == "rejected" and "saldo tidak cukup" in row["reason"]
    assert "saldo tidak cukup" in w.bot.edits[-1]["text"]


def test_network_error_during_guard_does_not_crash():
    w = World()
    sid = w.stx_signal()
    w.press(f"ya:{sid}")
    w.ex.fail["get_open_orders"] = ConnectionError("timeout")
    w.price(0.3313)                                    # satu putaran gagal baca
    w.price(0.3313)                                    # putaran berikutnya normal
    assert w.setup(sid)["status"] == "running"


# --- 7. pengaman ---------------------------------------------------------------------------
def test_max_active_setups_and_stop_command():
    w = World({"pengaman": {"maks_setup_aktif": 1, "loss_harian_persen": 3.0, "loss_harian_dolar": None}})
    sid = w.stx_signal()
    w.press(f"ya:{sid}")
    w.inbox("SETUP\nPair: BTCUSDT\nArah: BUY\nEntry: 64900\nSL: 64000\nTP1: 66000\nKe: bybit\nSumber: x")
    assert "maksimal 1" in w.setup()["reason"]
    w.command("/stop")
    assert w.ex.placed(symbol="STXUSDT", reduceOnly=False)[0]["orderStatus"] == "Cancelled"


def test_bot_off_when_limit_fills_then_tps_placed_on_restart():
    w = World()
    sid = w.stx_signal()
    w.press(f"ya:{sid}")
    w.price(0.3400)
    w.ex.set_price(0.3313, "STXUSDT")                  # bot mati: limit terisi tanpa dilihat
    w.ex.set_price(0.3450, "STXUSDT")
    assert w.tp_orders() == [] and w.ex.pos["STXUSDT"]["sl"] == 0.311   # hanya SL yang ada
    w2 = World(journal=w.journal, clock=w.clock, ex=w.ex)               # bot nyala lagi
    w2.price(0.3450)
    assert w2.tp_orders() == [0.3518, 0.3722, 0.3875, 0.4079]
    assert w2.setup(sid)["status"] == "running" and "ke-fill" in w2.bot.texts()


# --- 8. ticker dicari sendiri --------------------------------------------------------------
def _manual(pair, entry="0.3314", sl="0.311", tp="0.3518"):
    return f"SETUP\nPair: {pair}\nArah: BUY\nEntry: {entry}\nSL: {sl}\nTP1: {tp}\nKe: bybit\nSumber: manual"


def test_ticker_found_automatically_without_list():
    for pair in ("STXUSDT", "STX", "$STX", "STX/USDT", "STXUSDT.P", "STXUSD"):
        w = World()
        w.inbox(_manual(pair))
        row = w.setup()
        assert row["status"] == "awaiting_confirm" and row["broker_symbol"] == "STXUSDT", pair


def test_unknown_coin_stays_silent_on_exchange_but_reports_to_telegram():
    w = World()
    w.inbox(_manual("FAKEUSDT"))
    row = w.setup()
    assert row["status"] == "rejected" and "FAKEUSDT tidak ada di Bybit" in row["reason"]
    assert "ditolak" in w.bot.last and "FAKEUSDT tidak ada" in w.bot.last      # laporan ke chat konfirmasi
    assert not [c for c in w.ex.calls if c[0] == "place_order"]


def test_multiplied_coin_is_hinted_not_guessed():
    w = World()
    w.inbox(_manual("PEPEUSDT", "0.0000090", "0.0000080", "0.0000100"))
    reason = w.setup()["reason"]
    assert "PEPEUSDT tidak ada" in reason and "1000PEPEUSDT" in reason and "tidak dieksekusi" in reason


def test_multiplied_coin_used_when_signal_price_matches_its_scale():
    w = World()
    w.ex.prices["1000PEPEUSDT"] = 0.005287
    w.inbox(_manual("PEPE", "0.004954", "0.004717", "0.005191"))          # angka skala 1000PEPE
    row = w.setup()
    assert row["status"] == "awaiting_confirm" and row["broker_symbol"] == "1000PEPEUSDT"
    assert "Koin PEPE di Bybit adalah 1000PEPEUSDT" in w.bot.last              # ⚠️: selalu ditanya
    w2 = World()
    w2.ex.prices["1000PEPEUSDT"] = 0.005287
    w2.inbox(_manual("PEPEUSDT", "0.0000050", "0.0000047", "0.0000052"))  # angka skala PEPE biasa
    reason = w2.setup()["reason"]
    assert "tidak cocok dengan skala" in reason and "1000PEPEUSDT 0.005287" in reason


# --- 9. leverage mentok per koin -----------------------------------------------------------
def test_bybit_auto_mode_can_report_warnings_without_requesting_confirmation():
    w = World(cfg={"gerbang": {"jarak_maks_dari_harga_persen": 20.0,
                               "konfirmasi_wajib": False, "konfirmasi_peringatan": False}})
    w.ex.prices["1000PEPEUSDT"] = 0.005287
    w.inbox(_manual("PEPE", "0.004954", "0.004717", "0.005191"))
    assert w.setup()["status"] == "pending"
    assert len(w.ex.placed(reduceOnly=False)) == 1
    assert not w.bot.sent[-1]["buttons"]
    assert "Harga sekarang sudah lewat TP1" in w.bot.last
    assert "Koin PEPE di Bybit adalah 1000PEPEUSDT" in w.bot.last


def test_bybit_can_skip_only_the_doubt_warning_in_auto_mode():
    signal = _manual("STXUSDT") + "\nRagu: SL asli diduga salah desimal"
    cases = [
        ({"konfirmasi_wajib": False}, signal, "awaiting_confirm"),
        ({"konfirmasi_wajib": False, "izinkan_tebakan_otomatis": True}, signal, "pending"),
        ({"konfirmasi_wajib": False, "izinkan_tebakan_otomatis": True},
         signal + "\nHighrisk: ya", "awaiting_confirm"),
        ({"konfirmasi_wajib": True, "izinkan_tebakan_otomatis": True}, signal, "awaiting_confirm"),
    ]
    for policy, text, expected in cases:
        w = World(cfg={"gerbang": policy})
        w.inbox(text)
        assert w.setup()["status"] == expected
        assert "Pembaca ragu" in w.setup()["warning"] and "Pembaca ragu" in w.bot.last
        assert bool(w.ex.placed(reduceOnly=False)) == (expected == "pending")


def _confirm_text(w):
    return next(m["text"] for m in w.bot.sent if "perlu konfirmasi" in m["text"])


def test_leverage_is_coin_max_and_risk_still_from_sl():
    w = World()
    sid = w.stx_signal()
    text = _confirm_text(w)
    assert "Leverage 75x (maks koin ini)" in text and "Risiko 1" in text    # risiko 1% tetap
    w.press(f"ya:{sid}")
    lev = [kw for n, kw in w.ex.calls if n == "set_leverage"][-1]
    assert lev["buyLeverage"] == lev["sellLeverage"] == "75"
    assert "leverage 75x" in w.bot.edits[-1]["text"]


def test_bigger_position_uses_lower_tier_leverage():
    w = World({"risiko": {"mode": "persen", "persen": 4.0, "dolar": 10, "lot_maks": 5}})  # ~6.500 USDT
    w.stx_signal()
    assert "Leverage 70x" in _confirm_text(w)                             # tier 7.000 USDT = 70x


def test_isolated_margin_lowers_leverage_so_liquidation_is_beyond_sl():
    w = World(ex=None)
    w.ex.margin = "isolated"
    w.app.broker._margin = None
    w.stx_signal()
    text = _confirm_text(w)
    # SL 6.2% di bawah entry -> leverage aman ~1/(0.062+0.005+0.002) = 14x, bukan 75x
    assert "Leverage 14x (isolated" in text


def test_fixed_leverage_mode():
    w = World({"bybit": {"leverage_mode": "tetap", "leverage": 5, "nilai_posisi_maks_usdt": 100_000}})
    w.stx_signal()
    assert "Leverage 5x (tetap)" in _confirm_text(w)


def test_bot_runs_only_in_cross_margin(caplog):
    w = World()
    assert w.app.startup_checks() and "Mode margin cross" in w.bot.last
    w2 = World()
    w2.ex.margin = "isolated"
    assert not w2.app.startup_checks()
    assert "hanya jalan di CROSS margin" in caplog.text and "isolated" in caplog.text
    assert not w2.bot.sent


def test_timeout_is_retried_and_reported_honestly_not_as_missing_coin():
    import requests
    w = World()
    w.app.broker.b.RETRY_PAUSE = 0
    w.ex.fail["get_instruments_info"] = requests.exceptions.ReadTimeout("Read timed out. (read timeout=10)")
    w.inbox(_manual("STXUSDT"))                         # sekali timeout -> dicoba ulang -> lolos
    assert w.setup()["broker_symbol"] == "STXUSDT"


def test_persistent_timeout_says_bybit_did_not_answer():
    import requests
    from copier.broker_bybit import BybitBroker
    w = World()
    b = BybitBroker("demo", session=w.ex)
    b.RETRY_PAUSE = 0

    class Always(dict):
        def pop(self, k, *d):
            return requests.exceptions.ReadTimeout("Read timed out.")

        def __contains__(self, k):
            return k == "get_instruments_info"

    w.ex.fail = Always()
    sym, why = b.find_symbol("SOLUSDT", 84)
    assert sym is None and "gagal terhubung" in why and "tidak ada di Bybit" not in why
    assert "Kirim ulang" in why
    assert sum(1 for c in w.ex.calls if c[0] == "get_instruments_info") == 1 + b.READ_RETRIES


def test_orders_are_never_retried_on_timeout():
    import requests
    from copier.broker_bybit import BybitBroker
    w = World()
    b = BybitBroker("demo", session=w.ex)
    b.RETRY_PAUSE = 0
    w.ex.fail["place_order"] = requests.exceptions.ReadTimeout("Read timed out.")
    oid, err = b.place("BTCUSDT", "BUY", "LIMIT", 0.001, price=60000, sl=59000)
    assert oid is None and "gagal terhubung" in err
    assert sum(1 for c in w.ex.calls if c[0] == "place_order") == 1
