"""Daftar sumber sinyal: simpan/baca, kode, aturan topik, wizard tambah/ganti channel,
menu hapus, label Ke: dan Kode:."""
import asyncio
from contextlib import nullcontext
import os
from types import SimpleNamespace as NS

import pytest

from copier import accounts, sopfiles
from copier.journal import Journal
from copier.models import Setup
from copier.setup_format import dest_of, parse_update
from panel import sources_menu
from reader import sources, wizard
from reader.main import info, pick

GOLD = sources.Source(nama="outlook", channel="-1001", judul="Grup A", topik=[476], olah="claude",
                      ke="mt5", kode="S01")
CRYPTO = sources.Source(nama="traderawam", channel="-1002", judul="Grup B", topik="semua",
                        abaikan=[9], olah="traderawam", ke="bybit", kode="S02", akun="Utama")


def test_save_and_load_roundtrip(tmp_path):
    path = str(tmp_path / "sumber.yaml")
    sources.save([GOLD, CRYPTO], path)
    assert sources.load(path) == [GOLD, CRYPTO]
    assert open(path, encoding="utf-8").read().startswith("# Daftar sumber")


def test_old_jenis_field_still_reads(tmp_path):
    path = tmp_path / "sumber.yaml"
    path.write_text("sumber:\n  - {nama: a, channel: '1', jenis: gambar, ke: mt5}\n"
                    "  - {nama: b, channel: '2', jenis: teks, ke: bybit}\n", encoding="utf-8")
    a, b = sources.load(str(path))
    assert (a.olah, b.olah, a.kode, a.jenis, b.jenis) == ("claude", "traderawam", "", "gambar", "teks")


def test_topic_rules_and_description():
    assert GOLD.matches(476) and not GOLD.matches(1)
    assert CRYPTO.matches(1) and CRYPTO.matches(500) and not CRYPTO.matches(9)
    assert CRYPTO.topic_text() == "semua topik kecuali 9"
    d = CRYPTO.describe()
    assert d.startswith("S02") and "-> Bybit" in d and "TraderAwam" in d and "akun Utama" in d


def test_bad_values_rejected(tmp_path):
    path = tmp_path / "sumber.yaml"
    path.write_text("sumber:\n  - {nama: x, channel: '1', olah: video, ke: mt5}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        sources.load(str(path))


def test_codes():
    assert sources.next_code([]) == "S01"
    assert sources.next_code([GOLD, CRYPTO]) == "S03"
    assert sources.by_code([GOLD, CRYPTO], "s02") is CRYPTO and sources.by_code([GOLD], "") is None


def test_old_config_source_used_when_no_list(tmp_path):
    cfg = {"pembaca": {"channel_sumber": "-1009", "topik_sumber": 476, "nama_sumber": "outlook"}}
    (s,) = sources.load(str(tmp_path / "tidak-ada.yaml"), cfg)
    assert (s.channel, s.topik, s.olah, s.ke) == ("-1009", [476], "claude", "mt5")
    assert sources.load(str(tmp_path / "tidak-ada.yaml"), {"pembaca": {"channel_sumber": ""}}) == []


def test_pick_first_matching_source_for_chat_and_topic():
    same_group = sources.Source(nama="b", channel="-1001", topik="semua", abaikan=[476], olah="traderawam",
                                ke="bybit", kode="S03")
    by_chat = {-1001: [GOLD, same_group]}
    assert pick(by_chat, -1001, 476) is GOLD
    assert pick(by_chat, -1001, 12) is same_group
    assert pick(by_chat, -1003, 476) is None


def test_reader_info_posts_carry_destination_and_code():
    text = info(CRYPTO, "x")
    assert dest_of(text) == "bybit" and parse_update(text)["code"] == "S02"


# --- wizard -------------------------------------------------------------------------------
class FakeClient:
    def __init__(self, forum=True):
        ent = NS(forum=forum)
        self.dialogs = [NS(is_channel=True, id=-1001, name="Grup Sinyal", entity=ent),
                        NS(is_channel=False, id=5, name="Teman", entity=NS())]

    async def iter_dialogs(self):
        for d in self.dialogs:
            yield d


@pytest.fixture
def root(tmp_path, monkeypatch):
    from copier import accounts_mt5

    monkeypatch.setattr(accounts_mt5, "FILE", tmp_path / "akun-mt5.env")
    async def topics(client, entity):
        return {1: "General", 476: "Gold", 9: "Watcher", 12: "Swing"}

    monkeypatch.setattr(wizard, "list_topics", topics)
    for f, ke in (("config.yaml", "mt5"), ("config-bybit.yaml", "bybit")):
        (tmp_path / f).write_text(sopfiles.universal_text({}, ke), encoding="utf-8")
    return tmp_path


def run_wizard(root, answers, forum=True):
    feed = iter(answers)
    path = str(root / "sumber.yaml")
    return asyncio.run(wizard.add_source(FakeClient(forum), path, ask=lambda _: next(feed), root=str(root),
                                         accounts_folder=str(root))), path


def test_wizard_claude_source_to_mt5_gets_code_and_sop(root):
    # channel 1, hanya topik 476, Claude, MT5, SOP bawaan, nama gold, simpan
    src, path = run_wizard(root, ["1", "3", "999", "476", "1", "1", "1", "gold", "y"])
    assert (src.kode, src.topik, src.olah, src.ke, src.nama, src.akun) == ("S01", [476], "claude", "mt5", "gold", "")
    assert sources.load(path) == [src] and os.path.exists(sopfiles.sop_path("S01", str(root)))


def test_wizard_bybit_source_with_new_account_and_copied_sop(root):
    # semua topik, TraderAwam, Bybit, + API baru (Utama, demo, key, secret), SOP bawaan, nama awal
    run_wizard(root, ["1", "1", "2", "2", "1", "Utama", "1", "KEY123456789", "SECRET", "1", "awal", "y"])
    sop = sopfiles.sop_path("S01", str(root))
    from panel.yamledit import set_value
    text = set_value(open(sop, encoding="utf-8").read(), ("risiko", "persen"), 4.0)
    with open(sop, "w", encoding="utf-8") as f:
        f.write(text)
    # sumber kedua: semua topik kecuali 9, TraderAwam, Bybit, akun yang sudah ada, salin SOP S01
    src, path = run_wizard(root, ["1", "2", "9", "2", "2", "1", "2", "", "y"])
    assert (src.kode, src.abaikan, src.olah, src.akun, src.nama) == ("S02", [9], "traderawam", "Utama", "grup_sinyal")
    (acc,) = accounts.load(str(root))
    assert (acc.nama, acc.mode, acc.key) == ("Utama", "demo", "KEY123456789")
    cfg = sopfiles.effective(str(root / "config-bybit.yaml"), "S02", str(root))
    assert cfg["risiko"]["persen"] == 4.0


def test_wizard_cancel_and_duplicate_name(root):
    src, path = run_wizard(root, ["0"])
    assert src is None and not os.path.exists(path)
    sources.save([GOLD], path)
    src, _ = run_wizard(root, ["1", "1", "1", "1", "outlook", "baru", "y"], forum=False)
    assert src.nama == "baru" and src.topik == "semua" and src.kode == "S02" and len(sources.load(path)) == 2


def test_change_channel_keeps_code(root):
    path = str(root / "sumber.yaml")
    sources.save([GOLD], path)
    feed = iter(["1", "2", "9"])
    assert asyncio.run(wizard.change_channel(FakeClient(), "S01", path, ask=lambda _: next(feed)))
    (s,) = sources.load(path)
    assert (s.kode, s.channel, s.topik, s.abaikan, s.judul) == ("S01", "-1001", "semua", [9], "Grup Sinyal")


# --- menu ---------------------------------------------------------------------------------
def test_change_source_to_custom_code(root, monkeypatch):
    monkeypatch.setattr(sources_menu, "claim", lambda _: nullcontext())
    path = root / "sumber.yaml"
    sources.save([GOLD, CRYPTO], path)
    old_sop = root / "sumber" / "S01.yaml"
    old_sop.parent.mkdir(exist_ok=True)
    original = b"# SOP khusus\nrisiko:\n  dolar: 23\n"
    old_sop.write_bytes(original)

    with pytest.raises(ValueError, match="sudah pernah dipakai"):
        sources_menu.change_code("S01", "s02", root)
    assert sources_menu.change_code("S01", " ayam ", root) == "AYAM"

    renamed, unchanged = sources.load(path)
    assert renamed == sources.Source(**{**vars(GOLD), "kode": "AYAM"})
    assert unchanged == CRYPTO
    assert parse_update(info(renamed, "x"))["code"] == "AYAM"
    assert (old_sop.parent / "AYAM.yaml").read_bytes() == original
    assert not old_sop.exists()
    assert next((old_sop.parent / "terhapus").glob("S01-*.yaml")).read_bytes() == original


def test_menu_remove_blocks_busy_source_and_archives_sop(root, monkeypatch):
    path = str(root / "sumber.yaml")
    sources.save([GOLD, CRYPTO], path)
    for s in (GOLD, CRYPTO):
        sopfiles.write_source(s.kode, s.ke, s.nama, None, str(root))
    assert sources_menu.summary(sources.load(path)) == "2 sumber (1 ke MT5, 1 ke Bybit)"
    j = Journal(str(root / "data" / "jurnal-bybit.db"))
    sid = j.add_setup(Setup("BTCUSDT", "BUY", 1, 0.5, [2], code="S02"), "running")
    answers = iter(["2", "y", "2", "y"])
    monkeypatch.setattr(sources_menu, "choose", lambda items, **k: int(next(answers)) - 1)
    monkeypatch.setattr(sources_menu, "ask", lambda _: next(answers))
    assert not sources_menu.remove(sources.load(path), path, str(root))   # masih ada setup aktif
    next(answers)
    j.update(sid, status="done")
    j.db.close()
    assert sources_menu.remove(sources.load(path), path, str(root))
    assert [s.kode for s in sources.load(path)] == ["S01"]
    assert not os.path.exists(sopfiles.sop_path("S02", str(root)))
    assert os.listdir(root / "sumber" / "terhapus")
    assert sources_menu.summary([]) == "belum ada"
