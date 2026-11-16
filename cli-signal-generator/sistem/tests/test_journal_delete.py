"""Hapus analisa dari jurnal (Dashboard/menu) dan reset jurnal (menu)."""
import json
from datetime import datetime

import pytest
from openpyxl import load_workbook

from generator import actions, journal, main, menu
from generator.settings import Config, GeneratorError, REQUIRED_DENY, WIB

CONFIG = Config(None, "sonnet", 10, (REQUIRED_DENY,), "bot", "-1001234", "5678", True, frozenset(),
                skill_name="xau-a")
BLOCK = "SETUP\nKode: TES\nPair: XAUUSD\nArah: BUY\nEntry: {e}\nSL: {sl}\nTP1: {tp}\nTP2: {tp2}"


def add(name, minute, blocks=1):
    """Satu analisa tercatat, lengkap dengan file hasilnya."""
    journal.HASIL.mkdir(parents=True, exist_ok=True)
    (journal.HASIL / f"{name}.txt").write_text("jawaban mentah", encoding="utf-8")
    items = [BLOCK.format(e=4000 + i, sl=3990 + i, tp=4010 + i, tp2=4020 + i) for i in range(blocks)]
    journal.record(CONFIG, journal.BOT, "analisa\n" + "\n\n".join(items), items,
                   meta={"file": f"{name}.txt"}, started=datetime(2026, 11, 14, 15, minute, tzinfo=WIB))


def sheet_rows(sheet):
    return [row for row in sheet.iter_rows(min_row=2, values_only=False) if row[0].value is not None]


@pytest.fixture
def three():
    add("a", 0)
    add("b", 10, blocks=2)
    add("c", 20)
    book = load_workbook(journal.XLSX)
    sheet = book["Jurnal"]
    keys = [key for key, _, _ in journal.COLUMNS]
    for row in sheet_rows(sheet):
        if row[0].value == "c/1":
            row[keys.index("hasil")].value = "TP2"            # diisi user
            row[keys.index("catatan")].value = "mantap"
        if row[0].value == "a/1":
            row[keys.index("r")].value = 0.7                  # R diketik sendiri (partial)
    book.save(journal.XLSX)
    return keys


def test_delete_keeps_other_rows_and_fixes_formulas(three):
    keys = three
    removed = journal.delete_entries(["b"])
    assert [entry["id"] for entry in removed] == ["b"]
    assert [entry["id"] for entry in journal.read_entries()] == ["a", "c"]
    book = load_workbook(journal.XLSX)
    rows = {row[0].value: row for row in sheet_rows(book["Jurnal"])}
    assert set(rows) == {"a/1", "c/1"}                        # dua baris setup milik b ikut hilang
    assert [row[0].value for row in sheet_rows(book["Analisa"])] == ["a", "c"]
    c = rows["c/1"]
    number = c[0].row
    assert number == 3                                        # naik dari baris 5 ke baris 3
    assert c[keys.index("hasil")].value == "TP2" and c[keys.index("catatan")].value == "mantap"
    assert f"{journal.COL['entry']}{number}" in c[keys.index("risk")].value      # rumus menunjuk barisnya sendiri
    assert f"{journal.COL['hasil']}{number}" in c[keys.index("r")].value
    assert rows["a/1"][keys.index("r")].value == 0.7           # R ketikan user tidak disentuh
    backup = [json.loads(line) for line in journal.DELETED.read_text(encoding="utf-8").splitlines()]
    assert [entry["id"] for entry in backup] == ["b"] and backup[0]["dihapus"]
    assert not (journal.HASIL / "b.txt").exists() and (journal.HASIL / "terhapus" / "b.txt").exists()
    assert (journal.HASIL / "a.txt").exists()
    journal.sync()                                            # sinkron berikutnya tidak memunculkan b lagi
    assert "b/1" not in {row[0].value for row in sheet_rows(load_workbook(journal.XLSX)["Jurnal"])}


def test_delete_stops_when_excel_open(three, monkeypatch):
    def locked():
        raise journal.JournalLocked()

    monkeypatch.setattr(journal, "ensure_closed", locked)
    with pytest.raises(journal.JournalLocked):
        journal.delete_entries(["b"])
    assert [entry["id"] for entry in journal.read_entries()] == ["a", "b", "c"]   # tidak ada yang berubah
    assert (journal.HASIL / "b.txt").exists()
    with pytest.raises(journal.JournalError):
        journal.delete_entries(["tidak-ada"])


def test_reset_archives_everything(three):
    journal.delete_entries(["b"])
    (journal.HASIL / "uji-fungsi.txt").write_text("mode uji, tidak di jurnal", encoding="utf-8")
    assert journal.reset_counts() == (2, 1)
    archive = journal.reset()
    assert archive.parent == journal.FOLDER / "arsip"
    assert not journal.XLSX.exists() and not journal.LOG.exists() and not journal.DELETED.exists()
    assert journal.read_entries() == [] and journal.summary_numbers() is None
    assert {path.name for path in archive.iterdir()} >= {"Jurnal CLI.xlsx", "jurnal.jsonl", "jurnal-terhapus.jsonl"}
    assert {path.name for path in (archive / "hasil").iterdir()} == {"a.txt", "c.txt"}
    assert (archive / "hasil-terhapus" / "b.txt").exists()
    assert (journal.HASIL / "uji-fungsi.txt").exists()        # file di luar jurnal tidak ikut
    assert journal.reset() is None                            # sudah kosong
    add("d", 30)                                              # jurnal baru mulai dari nol
    assert [entry["id"] for entry in journal.read_entries()] == ["d"]


def test_actions_refuse_while_analysing(three, monkeypatch):
    monkeypatch.setattr(actions, "ACTION_WAIT_SECONDS", 0.1)
    with main.local_lock(main.WORK_PORT) as acquired:
        assert acquired
        with pytest.raises(GeneratorError, match="sedang menganalisa"):
            actions.delete_analyses(["a"])
        with pytest.raises(GeneratorError, match="sedang menganalisa"):
            actions.reset_journal()
    assert len(actions.delete_analyses(["a"])) == 1


@pytest.mark.parametrize(("text", "expected"), [
    ("1", [1]), ("1,3", [1, 3]), ("2-4", [2, 3, 4]), ("1, 3-4, 3", [1, 3, 4]),
])
def test_parse_numbers(text, expected):
    assert menu.parse_numbers(text, 5) == expected


@pytest.mark.parametrize("text", ["0", "6", "4-2", "a", "1;2", "1-"])
def test_parse_numbers_rejects(text):
    with pytest.raises(GeneratorError):
        menu.parse_numbers(text, 5)
