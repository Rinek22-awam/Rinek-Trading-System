"""Pembaca sinyal teks (format TraderAwam): sinyal jadi SETUP, rekap dan notifikasi diam."""
from copier.setup_format import dest_of, parse_setup
from reader.textsignal import build_text_setup, parse_text_signal

SIGNAL = """🟢 LONG · STXUSDT (https://www.tradingview.com/chart/?symbol=BYBIT:STXUSDT.P)
Swing · Bybit Perp 4h

🏷 Sector: Infrastructure (https://www.coingecko.com/en/categories/infrastructure)
      24h +1.8% · #154/371 sektor
💰 MCap 627.55M · Vol #-/-
⚡ Risk Medium

🧩 Setup
Manual · Retest level 0.3314 · SL di bawah low 0.3145
FR +0.010% · OI24h -1.6% · Skor 0

📍 Entry   0.3314   (limit)

🎯 TP1   0.3518   +6.16% · 1.0R
🎯 TP2   0.3722   +12.31% · 2.0R
🎯 TP3   0.3875   +16.93% · 2.8R
🎯 TP4   0.4079   +23.08% · 3.8R

🛑 SL    0.311   -6.16%
⚖️ Leverage maks ~13x (isolated) · ukuran posisi = risiko ÷ jarak SL

⏳ Valid s/d 11 Nov 01:08 WIB
❌ Batal jika > 0.365 sebelum entry

🧭 Manajemen
Partial TP1 30%
Harga sempat +1.5R → SL ke BE
Setelah TP1, SL trailing 2R dari harga terbaik
TP = level referensi

📊 Swing 90d live
12 closed · WR 33.3% · ΣR -1.9 · Exp -0.16R
TP1 3 · TP2 0 · TP3 0 · TP4 2 · SL 7
#SW45
"""

FILLED = """🔔 TraderAwam · STXUSDT · LONG
07 Nov 2026 19:38 WIB

Entry filled - Good luck buddy
Entry 0.3314 · SL 0.311"""

WATCHER = """📡 TRADERAWAM · RUNNING LIST
⚡️ POSISI BERJALAN

📈 $STX (https://t.me/c/1234567890/1) · Swing · LONG
     💚 0.3314 ➜ 0.3442  (+3.86% · +0.6R)
     🎯 ▱▱▱▱ menuju TP1 0.3518 · 🛡 SL 0.311

🕰 MENUNGGU ENTRY

📉 $ETH (https://t.me/c/1234567890/2) · Swing · SHORT
     📌 Limit 2360.2 · harga 2235.24 (5.59% lagi)
     ⌛ valid s/d 11 Nov 01:08"""

SHORT = """🔴 SHORT · ETHUSDT
⚡ Risk High
📍 Entry   2360.2   (limit)
🎯 TP1   2300
🎯 TP2   2250.5
🛑 SL    2400"""


def test_reader_posts_complete_signal_in_photo_caption(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import telethon
    from telethon.tl.types import PeerChannel

    from reader import main, sources

    chat_id = -1001234567890
    inbox = PeerChannel(2345678901)
    callbacks = []

    async def dispatch():
        for text in ("📈 $STX · Swing · LONG", SIGNAL):
            reply = SimpleNamespace(forum_topic=True, reply_to_top_id=5, reply_to_msg_id=5)
            msg = SimpleNamespace(photo=True, raw_text=text, reply_to=reply)
            await callbacks[0](SimpleNamespace(message=msg, chat_id=chat_id))

    client = SimpleNamespace(
        start=AsyncMock(), disconnect=AsyncMock(),
        get_entity=AsyncMock(side_effect=[inbox, PeerChannel(1234567890)]),
        on=lambda _: callbacks.append, run_until_disconnected=dispatch, send_message=AsyncMock(),
    )
    monkeypatch.setattr(telethon, "TelegramClient", lambda *_: client)
    monkeypatch.setattr(main, "_need", lambda _: "1")
    source = sources.Source("trader_awam", str(chat_id), topik=[5], olah="traderawam", ke="bybit", kode="S02")
    monkeypatch.setattr(sources, "load", lambda *_: [source])

    asyncio.run(main.run({}))
    client.disconnect.assert_awaited_once()

    client.send_message.assert_awaited_once()
    target, text = client.send_message.await_args.args
    setup, errors = parse_setup(text)
    assert target == inbox
    assert not errors and setup.code == "S02" and setup.symbol == "STXUSDT"


def test_long_signal_becomes_labelled_setup():
    text, why = build_text_setup(SIGNAL, "traderawam", "bybit")
    assert why == "" and dest_of(text) == "bybit"
    s, errors = parse_setup(text)
    assert errors == []
    assert (s.symbol, s.side, s.order_type, s.entry, s.sl) == ("STXUSDT", "BUY", "LIMIT", 0.3314, 0.311)
    assert s.tps == [0.3518, 0.3722, 0.3875, 0.4079] and s.source == "traderawam"
    assert not s.highrisk


def test_short_signal_and_highrisk():
    sig, _ = parse_text_signal(SHORT)
    assert sig["side"] == "SELL" and sig["tps"] == [2300, 2250.5] and sig["highrisk"]
    s, errors = parse_setup(build_text_setup(SHORT, "x", "bybit")[0])
    assert errors == [] and s.tps == [2300.0, 2250.5]


def test_watcher_and_fill_notice_stay_silent():
    for text in (WATCHER, FILLED, "gm semua", ""):
        assert build_text_setup(text, "traderawam", "bybit") == (None, "")


def test_wrong_side_numbers_are_reported_not_posted():
    bad = SHORT.replace("🛑 SL    2400", "🛑 SL    2300.1")
    text, why = build_text_setup(bad, "x", "bybit")
    assert text is None and "SL" in why


def test_incomplete_signal_reports_missing_price_field():
    for field, line in (("TP", ["🎯 TP1   2300", "🎯 TP2   2250.5"]),
                        ("Entry", ["📍 Entry   2360.2   (limit)"]),
                        ("SL", ["🛑 SL    2400"])):
        incomplete = "\n".join(row for row in SHORT.splitlines() if row not in line)
        assert build_text_setup(incomplete, "traderawam", "bybit") == (
            None, f"{field} hilang atau formatnya tidak terbaca",
        )


# --- contoh asli dari channel (Swing Rider) ------------------------------------------
OLD_CALL = """📣 NEW CALL · LONG
ZROUSDT · pullback FVG 4H dan retest BOS

Risk High · Grade B — trend 4H; recovery weekly belum penuh
Vol #22/613 kontrak Bybit saat scan · MCap N/A — supply beredar terkini belum diverifikasi
SWING · Sesi swing-1790985600 · Vet 04 Oct 2026 00:13 WIB

ENTRY
1.9 · LIMIT di POI; mungkin tidak terisi dalam 24 jam.

TARGETS
TP1 · 2.1269 · +11.94%
TP2 · 2.3911 · +25.85%
SL · 1.69 · -11.05%

R:R · T1 1.1R · T2 2.3R

LOGIKA LEVEL
• 4H close 2.08 menembus BOS 1.9993. Entry 1.90 di atas BOS lama 1.8838, dalam FVG.
• TP1 2.1269: high impuls sekarang. TP2 2.3911: tepi supply weekly.
• SL 1.69 di bawah FVG 4H 1.7165. Alternatif SL daily 1.59 di bawah base 1.6209.

EXPIRY & INVALIDASI
Berlaku sampai 05 Oct 2026 00:13 WIB.
"""

SUMMARY = """🟢 $STX · Swing · LONG
Entry 0.3314 · SL 0.311 · TP4 0.4079"""

CANCELLED = """🔔 TraderAwam · SPXUSDT · LONG
01 Nov 2026 20:44 WIB

Setup dibatalkan - harga lari melewati invalidasi sebelum entry"""

EXPIRED = """🔔 TraderAwam · LYNUSDT · SHORT
SWING · swing-1791158400
06 Oct 2026 08:37 WIB

Waktu entry habis; candle melewati expiry tidak dianggap fill aktif."""

TP_BEFORE_ENTRY = """🔔 TraderAwam · EDUUSDT · LONG
SWING · swing-1791215257

TP1 (target terakhir) terlewati sebelum entry; LIMIT dibatalkan, setup tidak dianggap terisi."""

SL_HIT = """🔔 TraderAwam · USUSDT · SHORT
SWING · swing-1791215257

SL tersentuh menurut high/low candle 1m. Periksa harga fill dan slippage aktual."""


def test_old_new_call_format():
    s, errors = parse_setup(build_text_setup(OLD_CALL, "traderawam", "bybit")[0])
    assert errors == []
    assert (s.symbol, s.side, s.order_type, s.entry, s.sl) == ("ZROUSDT", "BUY", "LIMIT", 1.9, 1.69)
    assert s.tps == [2.1269, 2.3911] and s.highrisk


def test_old_format_short():
    short = (OLD_CALL.replace("LONG", "SHORT").replace("1.9 · LIMIT", "2.5 · LIMIT")
             .replace("TP1 · 2.1269", "TP1 · 2.3").replace("TP2 · 2.3911", "TP2 · 2.1")
             .replace("SL · 1.69", "SL · 2.7"))
    sig, why = parse_text_signal(short)
    assert why == "" and (sig["side"], sig["entry"], sig["sl"], sig["tps"]) == ("SELL", 2.5, 2.7, [2.3, 2.1])


def test_summary_and_fill_notice_are_silent():
    from reader.textsignal import build_notice_update
    for text in (SUMMARY, FILLED):
        assert build_text_setup(text, "t", "bybit") == (None, "")
        assert build_notice_update(text, "t", "bybit") is None


def test_notices_become_buttons_for_that_coin():
    from copier.setup_format import parse_update
    from reader.textsignal import build_notice_update
    sl_hit = "🔔 TraderAwam · XMRUSDT · LONG\n10 Nov 2026 13:39 WIB\n\nSL hit 🛑 -2.18% (-1R)"
    cases = {CANCELLED: ("SPXUSDT", "CANCEL"), EXPIRED: ("LYNUSDT", "CANCEL"),
             TP_BEFORE_ENTRY: ("EDUUSDT", "CANCEL"), SL_HIT: ("USUSDT", "CLOSE"),
             sl_hit: ("XMRUSDT", "CLOSE")}
    for text, (pair, action) in cases.items():
        upd = build_notice_update(text, "traderawam", "bybit")
        assert dest_of(upd) == "bybit"
        u = parse_update(upd)
        assert (u["pair"], u["action"], u["source"]) == (pair, action, "traderawam")
    assert build_notice_update(sl_hit.replace("SL hit", "Belum SL hit"), "traderawam", "bybit") is None


# --- masa berlaku sinyal ---------------------------------------------------------------
def test_valid_until_copied_to_setup():
    from datetime import datetime
    from copier.setup_format import WIB
    from reader.textsignal import parse_valid_until
    now = datetime(2026, 11, 8, 10, 0, tzinfo=WIB)
    assert parse_valid_until("⏳ Valid s/d 11 Nov 01:08 WIB", now) == datetime(2026, 11, 11, 1, 8, tzinfo=WIB)
    assert parse_valid_until("Berlaku sampai 05 Oct 2026 00:13 WIB.", now) == \
        datetime(2026, 10, 5, 0, 13, tzinfo=WIB)
    late_dec = datetime(2026, 12, 30, 22, 0, tzinfo=WIB)       # sinyal 30 Des, berlaku s/d 2 Jan
    assert parse_valid_until("Valid s/d 02 Jan 01:00 WIB", late_dec).year == 2027
    assert parse_valid_until("tanpa masa berlaku", now) is None

    text, _ = build_text_setup(SIGNAL, "traderawam", "bybit")
    assert "\nBerlaku: " in text and " 01:08 WIB" in text
    s, errors = parse_setup(text)
    assert errors == [] and s.valid_until is not None
    s, _ = parse_setup(build_text_setup(OLD_CALL, "t", "bybit")[0])
    assert s.valid_until == datetime(2026, 10, 5, 0, 13, tzinfo=WIB).timestamp()


def test_swing_markdown_preserves_both_entry_limits():
    text = """🔴 **SHORT [$FIL](https://www.tradingview.com/chart/?symbol=BYBIT:FILUSDT.P)** · Swing 4h
📍 Entry `0.776` (limit)
🎯 TP1 `0.75` (+3.4%)
🎯 TP2 `0.724` (+6.7%)
🎯 TP3 `0.7045` (+9.2%)
🎯 TP4 `0.6785` (+12.6%)
🛑 SL `0.802` (-3.4%)
⏳ s/d 13 Nov 2026 07:23 · batal jika < `0.71` sebelum entry
🧭 30% di TP1 · +1.5R → SL BE · trailing 2R"""
    setup, why = build_text_setup(text, "traderawam", "bybit")
    assert why == ""
    assert "Berlaku: 2026-11-13 07:23 WIB" in setup
    s, errors = parse_setup(setup)
    assert not errors and s.symbol == "FILUSDT" and s.side == "SELL"
    assert s.cancel_before_entry == ("<", 0.71)
    assert build_text_setup(text.replace("< `0.71`", "sekitar `0.71`"), "x", "bybit")[0] is None


PEPE = """🟢 LONG $PEPE (https://www.tradingview.com/chart/?symbol=BYBIT:1000PEPEUSDT.P) · Swing 4h
Q4/5

📍 Entry 0.004954 (limit)
🎯 TP1 0.005191 (+4.8%)
🎯 TP2 0.005427 (+9.5%)
🎯 TP3 0.005605 (+13.1%)
🎯 TP4 0.005841 (+17.9%)
🛑 SL 0.004717 (-4.8%) · lev maks ~17x

⏳ s/d 14 Nov 15:01 · batal jika > 0.005391 sebelum entry
🧭 30% di TP1 · +1.5R → SL BE · trailing 2R

#SW63 · ⚠️ Jaga risk, tanggung jawab masing-masing"""


def test_dollar_coin_with_tradingview_link():
    sig, why = parse_text_signal(PEPE)
    assert why == "" and sig["symbol"] == "1000PEPEUSDT"          # simbol dari link, bukan "$PEPE"
    assert (sig["side"], sig["entry"], sig["sl"]) == ("BUY", 0.004954, 0.004717)
    assert sig["tps"] == [0.005191, 0.005427, 0.005605, 0.005841]
    assert (sig["valid_until"].month, sig["valid_until"].day, sig["valid_until"].hour) == (11, 14, 15)
    no_link = PEPE.replace(" (https://www.tradingview.com/chart/?symbol=BYBIT:1000PEPEUSDT.P)", "")
    assert parse_text_signal(no_link)[0]["symbol"] == "PEPE"        # tanpa link: bot Bybit mencocokkan
    s, err = parse_setup(build_text_setup(PEPE, "trader_awam", "bybit")[0])
    assert err == [] and s.symbol == "1000PEPEUSDT" and s.valid_until
    assert parse_text_signal("LONG · Swing 4h ditutup")[0] is None    # kata biasa bukan koin
