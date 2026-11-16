import pytest

from generator import journal


@pytest.fixture(autouse=True)
def journal_in_temp(tmp_path, monkeypatch):
    """Semua tes menulis jurnal ke folder sementara, bukan ke jurnal asli user."""
    monkeypatch.setattr(journal, "FOLDER", tmp_path / "jurnal")
    monkeypatch.setattr(journal, "XLSX", tmp_path / "jurnal" / "Jurnal CLI.xlsx")
    monkeypatch.setattr(journal, "LOG", tmp_path / "data" / "jurnal.jsonl")
    monkeypatch.setattr(journal, "DELETED", tmp_path / "data" / "jurnal-terhapus.jsonl")
    monkeypatch.setattr(journal, "HASIL", tmp_path / "data" / "hasil")
