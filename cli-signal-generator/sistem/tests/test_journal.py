import json
from dataclasses import replace
from datetime import datetime

import pytest
from openpyxl import load_workbook

from generator import claude, journal, main, telegram
from generator.settings import GeneratorError, WIB

from test_generator import CONFIG, SECRETS

SKILL = replace(CONFIG, skill_name="xau-scalp", model="claude-opus-5-5", effort="high")
WHEN = datetime(2026, 11, 14, 1, 3, tzinfo=WIB)
BLOCK = """SETUP
Kode: TES
Pair: XAUUSD
Arah: sell
Entry: 3889.6
SL: 3895.3
TP1: 3879.4
TP2: 3,876.60
Berlaku: 2026-11-14 05:00 WIB
Ke: mt5
Sumber: cli_xau-scalp"""
LOW_BLOCK = """SETUP RENDAH
Kode: TES
Pair: XAUUSD
Arah: BUY
Order: STOP
Entry: 3900
SL: 3894
TP1: 3910"""
META = {"model": "claude-opus-5-5", "giliran": 20, "durasi": 119, "tulis": 48739, "baca": 344289,
        "output": 6946, "biaya": 0.6123, "file": "20261114-010558.txt"}


def answer_with(*parts):
    text = claude.Answer("\n\n".join(parts))
    text.meta = dict(META)
    return text


def column(sheet, key):
    index = [k for k, _, _ in journal.COLUMNS].index(key) + 1
    return [sheet.cell(row=row, column=index).value for row in range(2, sheet.max_row + 1)]


def test_parse_block_numbers_and_defaults():
    fields = journal.parse_block(BLOCK)
    assert fields["arah"] == "SELL" and fields["order"] == "LIMIT"
    assert fields["entry"] == 3889.6 and fields["tp2"] == 3876.6 and fields["sl"] == 3895.3
    assert fields["berlaku"] == "2026-11-14 05:00 WIB" and fields["sumber"] == "cli_xau-scalp"
    assert journal.parse_block(LOW_BLOCK)["order"] == "STOP"


@pytest.mark.parametrize(("text", "count", "expected"), [
    ("Conviction ke TP1: ~55% — searah", 1, [55]),
    ("Conviction: 60%\nlain\nConviction ke TP1: 45%", 2, [60, 45]),
    ("Conviction 60% dan conviction 50% dan conviction 40%", 2, [None, None]),   # tidak jelas milik siapa
    ("tanpa angka", 1, [None]),
])
def test_convictions(text, count, expected):
    assert journal.convictions(text, ["SETUP\nKode: X"] * count) == expected


def test_conviction_inside_block_is_fallback():
    block = BLOCK + "\nCatatan: conviction ~55% | alasan"
    assert journal.convictions("analisa tanpa angka\n" + block, [block]) == [55]


def test_record_builds_excel_rows_and_summary():
    answer = answer_with("Peta HTF...\nConviction ke TP1: ~55%", BLOCK)
    assert journal.record(SKILL, journal.BOT, answer, [BLOCK], started=WHEN)
    book = load_workbook(journal.XLSX)
    assert book.sheetnames[:3] == ["Ringkasan", "Jurnal", "Analisa"]
    sheet = book["Jurnal"]
    assert column(sheet, "id") == ["20261114-010558/1"]
    assert column(sheet, "status") == [journal.BOT]
    assert column(sheet, "arah") == ["SELL"] and column(sheet, "entry") == [3889.6]
    assert column(sheet, "conviction") == [0.55] and column(sheet, "model") == ["claude-opus-5-5"]
    assert column(sheet, "tulis") == [48739] and column(sheet, "file") == ["20261114-010558.txt"]
    assert column(sheet, "risk")[0].startswith("=IF(") and column(sheet, "hari") == ["Sab"]
    analysis = book["Analisa"]
    assert analysis["A2"].value == "20261114-010558" and "Peta HTF" in analysis["H2"].value
    summary = book["Ringkasan"]
    assert "xau-scalp" in [cell.value for cell in summary["A"]]
    assert book.calculation.fullCalcOnLoad


def test_user_columns_survive_next_sync():
    journal.record(SKILL, journal.BOT, answer_with("Conviction 55%", BLOCK), [BLOCK], started=WHEN)
    book = load_workbook(journal.XLSX)
    sheet = book["Jurnal"]
    hasil = [k for k, _, _ in journal.COLUMNS].index("hasil") + 1
    sheet.cell(row=2, column=hasil, value="TP2")
    sheet.cell(row=2, column=hasil + 1, value=1.8)
    book.save(journal.XLSX)

    later = answer_with("NO SETUP - harga di tengah range")
    later.meta["file"] = "20261114-030000.txt"
    journal.record(SKILL, journal.NONE, later, detail="NO SETUP - harga di tengah range",
                   started=WHEN.replace(hour=3))
    sheet = load_workbook(journal.XLSX)["Jurnal"]
    assert column(sheet, "hasil") == ["TP2", None]
    assert column(sheet, "r")[0] == 1.8 and column(sheet, "r")[1].startswith("=IF(")
    assert column(sheet, "status") == [journal.BOT, journal.NONE]
    assert "tengah range" in column(sheet, "keterangan")[1]
    text = journal.summary_text()
    assert "2 analisa" in text and "1 TP" in text and "total R +1.80" in text


def test_r_value_matches_formula_rules():
    fields = journal.parse_block(BLOCK)   # entry 3889.6, SL 3895.3 (risk 5.7), TP2 3876.6
    assert journal.r_value("TP2", fields) == pytest.approx(13 / 5.7)
    assert journal.r_value("SL", fields) == -1 and journal.r_value("BE", fields) == 0
    assert journal.r_value("TP4", fields) is None          # TP4 tidak ada di blok
    assert journal.r_value("Tidak kebuka", fields) is None
    assert journal.r_value("TP1", {"order": "MARKET", "sl": 1, "tp1": 2}) is None   # tanpa Entry


def test_r_formula_on_new_rows_and_summary_uses_it():
    journal.record(SKILL, journal.BOT, answer_with("Conviction 55%", BLOCK), [BLOCK], started=WHEN)
    book = load_workbook(journal.XLSX)
    sheet = book["Jurnal"]
    assert column(sheet, "r")[0].startswith('=IF(AI2="SL",-1')
    hasil = [k for k, _, _ in journal.COLUMNS].index("hasil") + 1
    sheet.cell(row=2, column=hasil, value="TP2")
    book.save(journal.XLSX)
    assert f"total R +{13 / 5.7:.2f}" in journal.summary_text()


def test_old_rows_get_r_formula_without_touching_typed_r():
    journal.record(SKILL, journal.BOT, answer_with("Conviction 55%", BLOCK), [BLOCK], started=WHEN)
    later = answer_with("Conviction 50%", BLOCK)
    later.meta["file"] = "20261114-030000.txt"
    journal.record(SKILL, journal.BOT, later, [BLOCK], started=WHEN.replace(hour=3))
    book = load_workbook(journal.XLSX)
    sheet = book["Jurnal"]
    r = [k for k, _, _ in journal.COLUMNS].index("r") + 1
    sheet.cell(row=2, column=r, value=None)               # bentuk file versi lama
    sheet.cell(row=3, column=r, value=1.3)                # R ketikan user
    sheet.cell(row=1, column=r).fill = journal._xl().USER_HEAD_FILL
    book.save(journal.XLSX)
    assert journal.sync() == 0
    sheet = load_workbook(journal.XLSX)["Jurnal"]
    assert column(sheet, "r")[0].startswith("=IF(") and column(sheet, "r")[1] == 1.3
    assert sheet.cell(row=1, column=r).fill.fgColor.rgb == journal._xl().HEAD_FILL.fgColor.rgb


def test_two_blocks_usage_only_on_first_row():
    journal.record(SKILL, journal.BOT, answer_with("Conviction 60%\nConviction 45%", BLOCK, LOW_BLOCK),
                   [BLOCK], [LOW_BLOCK], started=WHEN)
    sheet = load_workbook(journal.XLSX)["Jurnal"]
    assert column(sheet, "status") == [journal.BOT, journal.LOW]
    assert column(sheet, "conviction") == [0.6, 0.45]
    assert column(sheet, "tulis") == [48739, None]


def test_locked_excel_keeps_entry_and_catches_up(monkeypatch):
    def locked(_):
        raise journal.JournalLocked()

    save = journal.save_book
    monkeypatch.setattr(journal, "save_book", locked)
    assert journal.record(SKILL, journal.BOT, answer_with(BLOCK), [BLOCK], started=WHEN)
    assert not journal.XLSX.exists()
    assert len(journal.read_entries()) == 1 and journal.pending() == 1
    monkeypatch.setattr(journal, "save_book", save)   # Excel sudah ditutup
    assert journal.sync() == 1
    assert journal.pending() == 0


def test_test_mode_is_not_recorded():
    assert not journal.record(CONFIG, journal.BOT, "SETUP\nKode: TES", ["SETUP\nKode: TES"])
    assert not journal.LOG.exists()


def test_broken_journal_never_breaks_analysis(monkeypatch):
    monkeypatch.setattr(journal, "append_log", lambda _: (_ for _ in ()).throw(OSError("disk penuh")))
    assert not journal.record(SKILL, journal.BOT, "x", [])


def test_run_analysis_records_success_and_failure(monkeypatch):
    class FakeSender:
        def send(self, chat, text):
            pass

    monkeypatch.setattr(telegram, "BotSender", lambda _: FakeSender())
    monkeypatch.setattr(main, "ensure_tradingview", lambda *_: None)
    monkeypatch.setattr(main, "analyze", lambda *_: answer_with("Conviction ke TP1: 55%", BLOCK))
    assert main.run_analysis(SKILL, SECRETS)

    def fail(*_):
        error = GeneratorError("Claude gagal. You've hit your session limit")
        error.meta = dict(META, file="20261114-020000.txt")
        raise error

    monkeypatch.setattr(main, "analyze", fail)
    assert not main.run_analysis(SKILL, SECRETS)
    entries = journal.read_entries()
    assert [entry["status"] for entry in entries] == [journal.BOT, journal.FAIL]
    assert entries[1]["id"] == "20261114-020000" and "session limit" in entries[1]["keterangan"]
    assert "Gagal" in journal.recent_text().splitlines()[0]


def test_usage_meta_reads_cli_json():
    result = json.loads("""{"num_turns": 20, "total_cost_usd": 0.6123456,
        "usage": {"cache_creation_input_tokens": 48739, "cache_read_input_tokens": 344289, "output_tokens": 6946},
        "modelUsage": {"claude-haiku-5-5": {"costUSD": 0.014}, "claude-opus-5-5": {"costUSD": 0.598}}}""")
    meta = claude.usage_meta(result, 119.4, "a.txt")
    assert meta == {"model": "claude-opus-5-5", "giliran": 20, "durasi": 119, "tulis": 48739, "baca": 344289,
                    "output": 6946, "biaya": 0.6123, "file": "a.txt"}
    assert claude.usage_meta({}, 1, "b.txt")["model"] == ""


def test_old_journal_name_is_renamed_with_user_data():
    journal.record(SKILL, journal.BOT, answer_with("Conviction 55%", BLOCK), [BLOCK], started=WHEN)
    old = journal.XLSX.with_name(journal.OLD_NAME)
    journal.XLSX.rename(old)
    journal.sync()
    assert journal.XLSX.exists() and not old.exists()
    assert column(load_workbook(journal.XLSX)["Jurnal"], "status") == [journal.BOT]
