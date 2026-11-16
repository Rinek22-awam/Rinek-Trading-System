"""Otak pembaca tanpa Telegram dan tanpa Claude sungguhan: jawaban Claude -> SETUP/UPDATE,
ingatan per channel, reply, paket pesan, dan rujukan Ref/Ragu di bot."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from copier.config import with_defaults
from copier.journal import Journal
from copier.models import DONE, Setup
from copier.setup_format import parse_setup, parse_update
from reader import brain as B
from reader.memory import Memory
from reader.pipeline import Collector, Pipeline, reply_target, worth_reading

from .test_flow import INBOX, make, post, press

P = with_defaults({})["pembaca"]
RANGES = P["simbol_dari_harga"]
SRC = SimpleNamespace(nama="trabar", ke="mt5", kode="S01")
CHAT, TOPIC = -100777, 5
T0 = 1_762_750_000.0


def gold(**kw):
    a = {"jenis": "setup", "simbol": "", "arah": "BUY", "order": "LIMIT",
         "entry": ["3.987,260", "3.985,460"], "sl": "3.981,260",
         "tp": ["3.993,260", "3.999,260"], "highrisk": False, "yakin": "tinggi", "alasan": "jelas"}
    a.update(kw)
    return a


# --- jawaban Claude -> SETUP ----------------------------------------------------------
def test_gold_setup_is_checked_and_formatted_by_code():
    text, why, sym = B.build_setup(gold(), RANGES, "trabar", "mt5")
    assert why == "" and sym == "XAUUSD"
    s, err = parse_setup(text)
    assert err == []
    assert (s.symbol, s.side, s.entry, s.entry2, s.sl, s.tps) == \
        ("XAUUSD", "BUY", 3987.26, 3985.46, 3981.26, [3993.26, 3999.26])
    assert "Ke: mt5" in text and "Sumber: trabar" in text and not s.doubt


def test_direction_from_claude_must_match_the_stop_loss():
    text, why, _ = B.build_setup(gold(arah="SELL"), RANGES, "x", "mt5")
    assert text is None and "tidak cocok" in why


def test_wrong_side_tp_and_missing_numbers_are_refused():
    assert "sisi yang salah" in B.build_setup(gold(tp=["3.970,0"]), RANGES, "x", "mt5")[1]
    assert "tidak lengkap" in B.build_setup(gold(sl=""), RANGES, "x", "mt5")[1]


def test_crypto_symbol_passes_through_known_symbol_must_match_price():
    a = {"simbol": "$STX", "arah": "BUY", "entry": ["1.25"], "sl": "1.10", "tp": ["1.40", "1.60"]}
    text, why, sym = B.build_setup(a, RANGES, "x", "bybit")
    assert sym == "STX" and parse_setup(text)[0].symbol == "STX"
    bad = {"simbol": "XAUUSD", "arah": "BUY", "entry": ["1.25"], "sl": "1.10", "tp": ["1.40"]}
    assert "tidak cocok" in B.build_setup(bad, RANGES, "x", "mt5")[1]
    btc = {"simbol": "BTCUSDT", "entry": ["112,450.5"], "sl": "111,900", "tp": ["113,500"]}
    assert parse_setup(B.build_setup(btc, RANGES, "x", "mt5")[0])[0].symbol == "BTCUSD"


def test_market_order_without_entry_uses_claimed_direction():
    a = {"simbol": "XAUUSD", "arah": "SELL", "order": "MARKET", "entry": [], "sl": "4020",
         "tp": ["4000", "3990"]}
    text, why, _ = B.build_setup(a, RANGES, "x", "mt5")
    s, err = parse_setup(text)
    assert err == [] and s.order_type == "MARKET" and s.side == "SELL" and s.entry is None


def test_low_confidence_becomes_ragu_and_highrisk_and_valid_are_kept():
    text, _, _ = B.build_setup(gold(yakin="rendah", alasan="angka TP buram", highrisk=True,
                                    berlaku="2026-11-11 01:08 WIB"), RANGES, "x", "mt5")
    s, err = parse_setup(text)
    assert err == [] and s.doubt == "angka TP buram" and s.highrisk and s.valid_until


def test_price_ranges_are_split():
    a = {"simbol": "AKE", "arah": "BUY", "entry": ["0.00589-0.0050"], "sl": "0.0044",
         "tp": ["0.0064 - 0.0076"], "yakin": "sedang"}
    text, why, sym = B.build_setup(a, RANGES, "x", "bybit")
    s, err = parse_setup(text)
    assert err == [] and (s.symbol, s.side, s.entry, s.entry2, s.sl, s.tps) == \
        ("AKE", "BUY", 0.00589, 0.005, 0.0044, [0.0064, 0.0076])
    assert B._list(["74680 to 76500", "1~2", 3.5]) == ["74680", "76500", "1", "2", 3.5]


def test_sl_typo_is_guessed_and_always_asked():
    a = {"simbol": "BTC", "arah": "SELL", "entry": ["74680-76500"], "sl": "772000",
         "tp": ["73800-73000"], "yakin": "sedang"}
    s, err = parse_setup(B.build_setup(a, RANGES, "x", "bybit")[0])   # tebakan kode: buang satu digit
    assert err == [] and s.sl == 77200 and s.side == "SELL" and (s.entry, s.entry2) == (74680, 76500)
    assert "SL di sinyal 772000 terlihat typo, ditebak 77200" in s.doubt
    s, _ = parse_setup(B.build_setup({**a, "sl_tebakan": "77000"}, RANGES, "x", "bybit")[0])
    assert s.sl == 77000                                                 # tebakan Claude didahulukan
    s, _ = parse_setup(B.build_setup({**a, "sl_tebakan": "999999"}, RANGES, "x", "bybit")[0])
    assert s.sl == 77200                                                 # tebakan tak masuk akal diabaikan
    s, _ = parse_setup(B.build_setup({**a, "yakin": "rendah", "alasan": "lev 150x"}, RANGES, "x", "bybit")[0])
    assert "ditebak 77200" in s.doubt and "lev 150x" in s.doubt


def test_sl_on_wrong_side_for_the_written_direction_is_guessed():
    a = {"simbol": "XAUUSD", "arah": "BUY", "entry": ["3990"], "sl": "39800", "tp": ["4000", "4010"]}
    s, _ = parse_setup(B.build_setup(a, RANGES, "x", "mt5")[0])          # SL 39800 -> 3980
    assert (s.side, s.sl) == ("BUY", 3980) and "ditebak 3980" in s.doubt
    normal = {"simbol": "AKE", "arah": "BUY", "entry": ["0.00589"], "sl": "0.0044", "tp": ["0.0064"]}
    s, _ = parse_setup(B.build_setup(normal, RANGES, "x", "bybit")[0])   # SL 25% wajar: tidak ditebak
    assert s.sl == 0.0044 and not s.doubt


def test_far_sl_without_a_sensible_guess_is_refused():
    a = {"simbol": "STX", "arah": "BUY", "entry": ["1.25"], "sl": "0.37", "tp": ["1.30"]}
    text, why, _ = B.build_setup(a, RANGES, "x", "bybit")
    assert text is None and "terlalu jauh" in why


def test_parse_answer_is_tolerant():
    a = B.parse_answer('Berikut:\n```json\n{"aksi": [{"jenis": "info", "teks": "hati-hati"}], '
                       '"gaya": "TP pakai garis hijau"}\n```')
    assert a["aksi"][0]["teks"] == "hati-hati" and a["gaya"] == "TP pakai garis hijau"
    assert a["tutup"] == [] and a["catatan"] == []
    with pytest.raises(RuntimeError):
        B.parse_answer("maaf saya tidak bisa")


def test_update_carries_ref_and_pair_of_the_context():
    ctx = {"id": 3, "summary": "XAUUSD BUY LIMIT entry 3987", "symbol": "XAUUSD",
           "inbox_msg": 55, "status": "open"}
    text = B.build_update({"aksi": "CANCEL", "yakin": "tinggi"}, "trabar", "mt5", ctx, "cancel gold")
    u = parse_update(text)
    assert (u["action"], u["ref"], u["pair"]) == ("CANCEL", 55, "XAUUSD")
    tp = parse_update(B.build_update({"aksi": "TP", "nilai": "TP1", "yakin": "rendah"}, "t", "mt5", ctx))
    assert tp["action"] == "INFO" and "TP kena (TP1)" in tp["text"] and "ragu" in tp["text"]
    lost = parse_update(B.build_update({"aksi": "CLOSE"}, "t", "mt5", None))
    assert lost["ref"] is None and "tidak ketemu" in lost["text"]


# --- ingatan -------------------------------------------------------------------------
def test_memory_contexts_messages_and_profile():
    clock = SimpleNamespace(t=T0)
    m = Memory(":memory:", clock=lambda: clock.t)
    k = m.new_context(CHAT, TOPIC, "XAUUSD BUY", symbol="XAUUSD", inbox_msg=10, msg_ids=[100, 101])
    assert m.context_for_message(CHAT, 101)["id"] == k
    assert [c["id"] for c in m.open_contexts(CHAT, TOPIC)] == [k]
    m.set_note(k, "hold sampai TP3")
    m.close(k, "SL kena")
    assert m.open_contexts(CHAT, TOPIC) == [] and m.get(k)["reason"] == "SL kena"
    assert m.context_for_message(CHAT, 100)["status"] == "closed"   # tetap bisa dirujuk lewat reply
    m.set_profile(CHAT, TOPIC, "x" * 2000)
    assert len(m.profile(CHAT, TOPIC)) == 600
    for i in range(205):
        m.add_message(CHAT, TOPIC, i, T0 + i, None, f"pesan {i}", False)
    assert len(m.recent(CHAT, TOPIC, 10)) == 10 and m.message(CHAT, 3) is None   # yang lama dibuang
    k2 = m.new_context(CHAT, TOPIC, "BTC")
    clock.t += 8 * 86400
    assert m.expire() == [k2]


def test_memory_follows_the_bot_journal(tmp_path):
    jpath = str(tmp_path / "jurnal.db")
    j = Journal(jpath)
    sid = j.add_setup(Setup("XAUUSD", "BUY", 3990, 3980, [4000]), "pending", msg_chat=INBOX, msg_id=77)
    m = Memory(":memory:")
    k = m.new_context(CHAT, TOPIC, "XAUUSD BUY", inbox_msg=77)
    other = m.new_context(CHAT, TOPIC, "tanpa postingan")
    assert m.sync_journals(INBOX, [jpath, str(tmp_path / "tidak-ada.db")]) == []
    j.update(sid, status=DONE)
    assert m.sync_journals(INBOX, [jpath]) == [k]
    assert "selesai" in m.get(k)["reason"] and m.get(other)["status"] == "open"


# --- alur paket ----------------------------------------------------------------------
class FakeThink:
    """Claude tiruan: mengembalikan jawaban berikutnya dan menyimpan prompt yang dilihatnya."""

    def __init__(self, *answers):
        self.answers, self.prompts, self.images = list(answers), [], []

    def __call__(self, prompt_for, images, **models):
        self.models = models
        names = {k: [f"./img_{k}_{i + 1}.jpg" for i in range(len(v))] for k, v in images.items()}
        self.prompts.append(prompt_for(names))
        self.images.append(images)
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return B.parse_answer(json.dumps(a))


class Inbox:
    def __init__(self):
        self.posts, self.n = [], 500

    async def post(self, text, image=None):
        self.n += 1
        self.posts.append((self.n, text, image))
        return self.n


def msg(i, text="", reply_to=None, image=False, t=0):
    return B.Msg(id=i, chat=CHAT, topic=TOPIC, time=T0 + t, text=text, reply_to=reply_to,
                 images=[(b"IMG%d" % i, "image/jpeg")] if image else [])


def pipeline(think, inbox, fetch=None, models=None):
    return Pipeline(Memory(":memory:", clock=lambda: T0), think, inbox.post, P, fetch=fetch,
                    clock=lambda: T0, models=models)


def run(coro):
    return asyncio.run(coro)


def test_one_image_two_setups_then_reply_cancels_the_right_one():
    think = FakeThink(
        {"aksi": [gold(dari_pesan=[100]),
                  gold(dari_pesan=[100], arah="SELL", entry=["4.050,0"], sl="4.060,0", tp=["4.040,0"])],
         "gaya": "Entry garis biru, SL merah, TP hitam."},
        {"aksi": [{"jenis": "update", "dari_pesan": [102], "konteks": "", "aksi": "CANCEL",
                   "yakin": "tinggi", "alasan": "trader bilang cancel"}],
         "tutup": [{"id": "K2", "alasan": "dibatalkan trader"}]})
    inbox = Inbox()
    pipe = pipeline(think, inbox)
    run(pipe.handle([msg(100, "dua setup gold hari ini", image=True), msg(101, "gas", t=2)], SRC))
    (n1, s1, img1), (n2, s2, img2) = inbox.posts
    assert img1 == b"IMG100" and img2 is None          # gambar hanya ikut postingan pertama
    assert parse_setup(s1)[0].side == "BUY" and parse_setup(s2)[0].side == "SELL"
    assert parse_setup(s1)[0].code == "S01" and parse_setup(s2)[0].code == "S01"   # kode sumber
    assert "[gambar: ./img_100_1.jpg]" in think.prompts[0]
    mem = pipe.memory
    assert mem.profile(CHAT, TOPIC).startswith("Entry garis biru")
    assert mem.message(CHAT, 101)["result"] == "diabaikan"

    # trader me-reply gambar tadi: "cancel yang sell". Claude tidak menyebut konteks, tapi
    # menutup K2; reply menunjuk pesan 100 (milik K1 dan K2) -> konteks terbaru, K2.
    run(pipe.handle([msg(102, "cancel yang sell", reply_to=100, t=60)], SRC))
    prompt = think.prompts[1]
    assert "OPEN SETUPS:\nK1" in prompt and "K2" in prompt and "balas #100 (milik K2)" in prompt
    u = parse_update(inbox.posts[-1][1])
    assert u["action"] == "CANCEL" and u["ref"] == n2 and u["pair"] == "XAUUSD" and u["code"] == "S01"
    assert mem.get(2)["status"] == "closed" and mem.get(1)["status"] == "open"
    assert "update K2 CANCEL" in mem.message(CHAT, 102)["result"]


def test_reply_to_unknown_message_is_fetched_with_its_image():
    async def fetch(chat, msg_id):
        return msg(msg_id, "BUY gold 3990 SL 3980 TP 4000", image=True)

    think = FakeThink({"aksi": [{"jenis": "info", "dari_pesan": [300], "teks": "TP1 setup lama kena"}]})
    inbox = Inbox()
    run(pipeline(think, inbox, fetch).handle([msg(300, "tp1 ✅", reply_to=250)], SRC))
    assert "REPLIED-TO MESSAGES:\n#250: \"BUY gold 3990 SL 3980 TP 4000\" [gambar: ./img_r250_1.jpg]" \
        in think.prompts[0]
    assert "r250" in think.images[0]
    assert parse_update(inbox.posts[0][1])["action"] == "INFO"


def test_setup_refused_by_code_is_reported_and_remembered():
    think = FakeThink({"aksi": [gold(arah="SELL", dari_pesan=[400])]})
    inbox = Inbox()
    pipe = pipeline(think, inbox)
    run(pipe.handle([msg(400, image=True)], SRC))
    text = inbox.posts[0][1]
    assert parse_update(text)["action"] == "INFO" and "tidak dikirim" in text
    assert "tidak dikirim ke bot" in pipe.memory.open_contexts(CHAT, TOPIC)[0]["summary"]


def test_models_come_from_the_source():
    think = FakeThink({"aksi": []})
    run(pipeline(think, Inbox(), models=lambda src: {"image_model": "opus", "text_model": "sonnet"})
        .handle([msg(700, "halo")], SRC))
    assert think.models == {"image_model": "opus", "text_model": "sonnet"}


def test_stamp_adds_code_once():
    assert B.stamp("SETUP\nPair: X", "S01") == "SETUP\nKode: S01\nPair: X"
    assert B.stamp("UPDATE\nKode: S02\nTeks: x", "S01") == "UPDATE\nKode: S02\nTeks: x"
    assert B.stamp("SETUP\nPair: X", "") == "SETUP\nPair: X"


def test_quota_error_is_reported_and_messages_are_marked():
    think = FakeThink(RuntimeError("claude: You've hit your limit · resets 5pm"))
    inbox = Inbox()
    pipe = pipeline(think, inbox)
    run(pipe.handle([msg(500, "BUY gold sekarang")], SRC))
    assert "Kuota Claude habis" in inbox.posts[0][1] and "#500" in inbox.posts[0][1]
    assert pipe.memory.message(CHAT, 500)["result"] == "gagal dibaca"


def test_notes_and_close_only_touch_this_channel():
    inbox = Inbox()
    pipe = pipeline(FakeThink({"aksi": [], "tutup": [{"id": "K1", "alasan": "x"}],
                               "catatan": [{"id": "K1", "teks": "hold TP3"}]}), inbox)
    other = pipe.memory.new_context(-100999, 1, "channel lain")
    run(pipe.handle([msg(600, "halo semua")], SRC))
    assert pipe.memory.get(other)["status"] == "open" and pipe.memory.get(other)["note"] == ""
    assert inbox.posts == []


# --- penampung, saringan, reply ------------------------------------------------------
def test_collector_groups_messages_until_the_channel_is_quiet():
    async def go():
        got = []

        async def flush(key, items):
            got.append((key, items))

        c = Collector(flush, quiet=0.05, max_wait=1.0)
        c.add("a", 1)
        await asyncio.sleep(0.02)
        c.add("a", 2)
        c.add("b", 9)
        await asyncio.sleep(0.15)
        c.add("a", 3)
        await asyncio.sleep(0.15)
        return got

    got = run(go())
    assert ("a", [1, 2]) in got and ("b", [9]) in got and ("a", [3]) in got


def test_filter_skips_gif_sticker_and_emoji_only():
    base = dict(action=None, gif=None, sticker=None, video=None, voice=None, video_note=None,
                poll=None, photo=None, document=None, file=None, raw_text="")
    m = lambda **kw: SimpleNamespace(**{**base, **kw})
    assert worth_reading(m(raw_text="TP1"))
    assert worth_reading(m(photo=object()))
    assert not worth_reading(m(raw_text="🔥🔥🚀"))
    assert not worth_reading(m(gif=object(), document=object(), raw_text="lol"))
    assert not worth_reading(m(sticker=object(), document=object()))
    assert worth_reading(m(document=object(), file=SimpleNamespace(mime_type="image/png")))


def test_reply_target_ignores_the_forum_topic_link():
    rt = lambda **kw: SimpleNamespace(reply_to=SimpleNamespace(**kw))
    assert reply_target(rt(forum_topic=True, reply_to_msg_id=78395, reply_to_top_id=None)) is None
    assert reply_target(rt(forum_topic=True, reply_to_msg_id=90001, reply_to_top_id=78395)) == 90001
    assert reply_target(rt(forum_topic=False, reply_to_msg_id=12)) == 12
    assert reply_target(SimpleNamespace(reply_to=None)) is None


# --- memanggil Claude CLI ------------------------------------------------------------
def test_cli_call_text_only_uses_light_model_without_tools(tmp_path):
    seen = {}

    def fake_run(cmd, input=None, cwd=None, **kw):
        seen.update(cmd=cmd, input=input)
        return SimpleNamespace(stdout=json.dumps({"result": '{"aksi": []}'}), stderr="")

    b = B.ClaudeBrain(workdir=str(tmp_path), run=fake_run)
    assert b.think(lambda names: "NEW MESSAGES: tp1", {}) == \
        {"aksi": [], "tutup": [], "catatan": [], "gaya": None}
    cmd = seen["cmd"]
    assert cmd[cmd.index("--model") + 1] == "haiku" and cmd[cmd.index("--tools") + 1] == ""
    assert "--system-prompt" in cmd and seen["input"] == "NEW MESSAGES: tp1"


def test_cli_call_with_image_writes_files_and_uses_image_model(tmp_path):
    seen = {}

    def fake_run(cmd, input=None, cwd=None, **kw):
        import os
        seen.update(cmd=cmd, input=input, files=sorted(os.listdir(cwd)))
        return SimpleNamespace(stdout=json.dumps({"result": '{"aksi": []}'}), stderr="")

    b = B.ClaudeBrain(image_model="sonnet", workdir=str(tmp_path), run=fake_run)
    b.think(lambda names: f"lihat {names[7][0]}", {7: [(b"x", "image/png")]})
    assert seen["files"] == ["img_7_1.png"] and seen["input"] == "lihat ./img_7_1.png"
    cmd = seen["cmd"]
    assert cmd[cmd.index("--model") + 1] == "sonnet" and cmd[cmd.index("--tools") + 1] == "Read"
    assert list(tmp_path.iterdir()) == []   # folder sementara dihapus lagi


def test_cli_error_is_raised():
    def fake_run(cmd, **kw):
        return SimpleNamespace(stdout=json.dumps({"is_error": True, "result": "usage limit"}), stderr="")

    with pytest.raises(RuntimeError) as e:
        B.ClaudeBrain(run=fake_run).think(lambda n: "x", {})
    assert B.is_quota_error(str(e.value))


# --- di bot: Ref dan Ragu ---------------------------------------------------------------
SETUP_A = "SETUP\nPair: XAUUSD\nArah: BUY\nEntry: 3990\nSL: 3980\nTP1: 4000\nSumber: trabar"
SETUP_B = "SETUP\nPair: XAUUSD\nArah: BUY\nEntry: 3992\nSL: 3982\nTP1: 4002\nSumber: trabar"


def test_bot_ref_targets_the_exact_setup_not_the_newest():
    app, mt5, bot, clock = make()
    post(app, SETUP_A)
    post(app, SETUP_B)
    first, second = app.journal.by_status("awaiting_confirm")
    post(app, f"UPDATE\nSumber: trabar\nAksi: CANCEL\nRef: {first['msg_id']}\nTeks: cancel yang pertama")
    assert f"tidak:{first['id']}" in str(bot.sent[-1]["buttons"])
    post(app, "UPDATE\nSumber: trabar\nAksi: CANCEL\nTeks: tanpa ref")   # cara lama: yang terbaru
    assert f"tidak:{second['id']}" in str(bot.sent[-1]["buttons"])


def test_bot_ref_to_finished_setup_only_informs():
    app, mt5, bot, clock = make()
    post(app, SETUP_A)
    row = app.journal.by_status("awaiting_confirm")[0]
    press(app, f"tidak:{row['id']}")
    post(app, f"UPDATE\nSumber: trabar\nAksi: CLOSE\nRef: {row['msg_id']}\nTeks: close")
    assert "sudah declined" in bot.last and bot.sent[-1]["buttons"] is None
    post(app, f"UPDATE\nSumber: trabar\nAksi: INFO\nRef: {row['msg_id']}\nTeks: TP1 kena")
    assert f"Setup terkait: #{row['id']}" in bot.last


def test_bot_asks_when_reader_is_in_doubt_even_in_auto_mode():
    app, mt5, bot, clock = make({"gerbang": {"konfirmasi_wajib": False}})
    post(app, SETUP_A + "\nRagu: angka SL buram")
    assert "Pembaca ragu: angka SL buram" in bot.last
    assert app.journal.by_status("awaiting_confirm")
