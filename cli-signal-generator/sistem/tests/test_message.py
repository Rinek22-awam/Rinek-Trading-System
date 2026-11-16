"""Pesan user untuk "Analisa sekarang" (menu dan Dashboard)."""
import json
import sys
from dataclasses import replace

import pytest

from generator import actions, claude, journal, main, telegram
from generator.settings import Config, GeneratorError, REQUIRED_DENY

CONFIG = Config(None, "sonnet", 10, (REQUIRED_DENY,), "bot", "-1001234", "5678", True, frozenset(),
                skill_name="xau-a", send_analysis=True)
SECRETS = {"GEN_TG_BOT_TOKEN": "test:secret", "GEN_TG_API_ID": "", "GEN_TG_API_HASH": ""}
MESSAGE = "Perhatikan level 4100, kemarin ditolak 3 kali.\nBaris kedua info panjang."


@pytest.fixture
def skill_dir(tmp_path, monkeypatch):
    system = tmp_path / "sistem"
    (system / "prompt").mkdir(parents=True)
    (system / "prompt" / "mode_otomatis.md").write_text("ATURAN OTOMATIS", encoding="utf-8")
    (system / "prompt" / "uji_fungsi.md").write_text("UJI FUNGSI", encoding="utf-8")
    skill = tmp_path / "skill" / "xau-a"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("ISI SKILL", encoding="utf-8")
    monkeypatch.setattr(claude, "SYSTEM", system)
    monkeypatch.setattr(claude, "skills_dir", lambda: tmp_path / "skill")
    return skill


def test_prompt_carries_message_before_program_rules(skill_dir):
    config = replace(CONFIG, skill=skill_dir, setup_code="TES")
    assert "PESAN USER" not in claude.build_prompt(config)
    prompt = claude.build_prompt(replace(config, message=MESSAGE))
    order = [prompt.index(part) for part in ("ISI SKILL", "PESAN USER", MESSAGE, "ATURAN DARI PROGRAM")]
    assert order == sorted(order)                        # aturan Kode/Sumber dari program tetap paling akhir
    assert "BOLEH mengalahkan aturan skill" in prompt
    test_mode = claude.build_prompt(replace(config, skill=None, message=MESSAGE))
    assert "UJI FUNGSI" in test_mode and MESSAGE in test_mode


def test_result_file_keeps_full_message(tmp_path, monkeypatch):
    monkeypatch.setattr(claude, "DATA", tmp_path)
    name = claude.save_result('{"result": "x"}', SECRETS, MESSAGE)
    text = (tmp_path / "hasil" / name).read_text(encoding="utf-8")
    assert text.startswith("=== PESAN USER ===\n" + MESSAGE) and '{"result": "x"}' in text
    plain = claude.save_result('{"result": "y"}', SECRETS)
    assert "PESAN USER" not in (tmp_path / "hasil" / plain).read_text(encoding="utf-8")


def run_fake(monkeypatch, answer, config):
    calls = []

    class FakeSender:
        def send(self, chat, text):
            calls.append((chat, text))

    monkeypatch.setattr(telegram, "BotSender", lambda _: FakeSender())
    monkeypatch.setattr(main, "ensure_tradingview", lambda *_: None)
    monkeypatch.setattr(main, "analyze", lambda *_: answer)
    assert main.run_analysis(config, SECRETS)
    return calls


def test_message_sends_every_block_to_pm_not_bot(monkeypatch):
    answer = "analisa\nSETUP\nKode: TES\nArah: BUY\n\nSETUP RENDAH\nKode: TES\nArah: SELL"
    calls = run_fake(monkeypatch, answer, replace(CONFIG, message=MESSAGE))
    assert not [text for chat, text in calls if chat == CONFIG.inbox]          # tidak ada yang ke bot
    pm = [text for chat, text in calls if chat == CONFIG.report]
    assert pm[0] == "CLI mulai analisa (dengan pesan)."
    assert "SETUP RENDAH\nKode: TES\nArah: BUY" in pm and "SETUP RENDAH\nKode: TES\nArah: SELL" in pm
    assert any(text.startswith("📊 Analisa CLI · xau-a · dengan pesan") for text in pm)
    assert pm[-1].startswith("⚪ CLI selesai (dengan pesan)") and "2 setup (analisa dengan pesan" in pm[-1]
    assert all(MESSAGE not in text for _, text in calls)                       # isi pesan tidak ke Telegram
    entry = journal.read_entries()[-1]
    assert entry["pesan"] is True and entry["status"] == journal.LOW
    assert MESSAGE not in json.dumps(entry, ensure_ascii=False)                # isi pesan tidak di jurnal
    assert all(row["keterangan"].startswith("dengan pesan") for row in journal.rows_for(entry))


def test_without_message_nothing_changes(monkeypatch):
    calls = run_fake(monkeypatch, "analisa\nSETUP\nKode: TES", CONFIG)
    assert [text for chat, text in calls if chat == CONFIG.inbox] == ["SETUP\nKode: TES"]
    assert calls[0][1] == "CLI mulai analisa."
    entry = journal.read_entries()[-1]
    assert entry["pesan"] is False and journal.rows_for(entry)[0]["keterangan"] == ""


def test_no_setup_with_message_keeps_tag(monkeypatch):
    calls = run_fake(monkeypatch, "NO SETUP - harga di tengah", replace(CONFIG, message=MESSAGE))
    assert calls[-1][1].startswith("⚪ CLI selesai (dengan pesan). Tidak ada setup.")
    assert journal.rows_for(journal.read_entries()[-1])[0]["keterangan"] == "dengan pesan · NO SETUP - harga di tengah"


def test_message_file_read_and_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "MESSAGE_DIR", tmp_path / "pesan")
    path = actions.save_message("  " + MESSAGE + "\n")
    assert path.parent == tmp_path / "pesan" and main.read_message(path) == MESSAGE
    assert actions.save_message("   \n ") is None
    main.discard_message(path)
    assert not path.exists()
    outside = tmp_path / "bukan-pesan.txt"
    outside.write_text("x", encoding="utf-8")
    main.discard_message(outside)
    assert outside.exists()                                                    # hanya file di data/pesan yang dihapus
    with pytest.raises(GeneratorError, match="terlalu panjang"):
        actions.save_message("x" * (main.MESSAGE_LIMIT + 1))


def test_notepad_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "MESSAGE_DIR", tmp_path / "pesan")
    writer = [sys.executable, "-c", "import sys; open(sys.argv[1], 'w', encoding='utf-8').write('fokus sesi NY')"]
    path, length = actions.message_from_notepad(editor=writer)
    assert main.read_message(path) == "fokus sesi NY" and length == len("fokus sesi NY")
    empty, length = actions.message_from_notepad(editor=[sys.executable, "-c", "pass"])
    assert empty is None and length == 0
    assert list((tmp_path / "pesan").iterdir()) == [path]                      # file kosong langsung dihapus


def test_menu_offers_message_from_skipped_analysis(tmp_path, monkeypatch):
    from generator import menu
    monkeypatch.setattr(main, "MESSAGE_DIR", tmp_path / "pesan")
    screen = menu.Menu()
    assert screen.take_leftover() is None                         # belum ada pesan sisa: tidak bertanya
    screen.leftover = actions.save_message("fokus 4100")
    monkeypatch.setattr(menu, "ask", lambda _: "y")
    kept = screen.take_leftover()
    assert kept.exists() and screen.leftover is None
    screen.leftover = kept
    monkeypatch.setattr(menu, "ask", lambda _: "")
    assert screen.take_leftover() is None and not kept.exists()   # ditolak: file pesan dibuang


def test_analysis_args():
    assert actions.analysis_args() == ("once",)
    assert actions.analysis_args("xau-a") == ("once", "--skill", "xau-a")
    assert actions.analysis_args("xau-a", "C:/p.txt") == ("once", "--skill", "xau-a", "--pesan-file", "C:/p.txt")
