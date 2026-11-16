from contextlib import closing, nullcontext
from pathlib import Path

import pytest

from copier.journal import Journal
from copier.models import Setup
from panel import reset_bot


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(reset_bot, "claim", lambda _: nullcontext())
    Path("logs").mkdir()
    Path("data").mkdir()
    for config, name in (("config.yaml", "jurnal"), ("config-bybit.yaml", "jurnal-bybit")):
        Path(config).write_text(f"bot:\n  jurnal: data/{name}.db\n", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(("index", "name", "other", "logs"), [
    (0, "jurnal", "jurnal-bybit", ("bot.log", "desktop-bot.log")),
    (1, "jurnal-bybit", "jurnal", ("bot-bybit.log",)),
])
def test_executor_reset_erases_only_its_own_history(workspace, index, name, other, logs):
    journal_path = f"data/{name}.db"
    setup = Setup("XAUUSD", "BUY", 3990, 3980, [4000])
    journal = Journal(journal_path)
    with closing(journal.db):
        journal.add_setup(setup, "running")
        journal.add_setup(setup, "pending")
        journal.set_kv("paused", True)
        journal.set_kv("button_generation", 8)
    removed = [Path(f"logs/{log}{suffix}") for log in logs for suffix in ("", ".1")]
    removed += [Path(f"data/{name}-cadangan-20260101.db{suffix}") for suffix in ("", "-wal")]
    kept = [Path(f"data/{other}-cadangan-20260101.db"), Path("logs/pengawas.log"),
            Path("logs/pembaca.log"), Path("data/telegram-mt5-test.db"),
            Path("data/telegram-bybit-test.session"), Path("data/akun-mt5.json")]
    for file in removed + kept:
        file.write_text("fixture", encoding="utf-8")

    reset_bot.reset_bot(reset_bot.BOTS[index])

    assert all(not file.exists() for file in removed)
    assert all(file.read_text(encoding="utf-8") == "fixture" for file in kept)
    journal = Journal(journal_path)
    with closing(journal.db):
        assert not journal.since(0)
        assert journal.get_kv("paused", False) is False
        assert journal.get_kv("button_generation") not in (None, 0, 8)
        assert journal.add_setup(setup, "pending") == 1


def test_reader_reset_preserves_session_and_executor_files(workspace):
    removed = ["logs/pembaca.log", "logs/desktop-reader.log.1", "data/ingatan.db", "data/ingatan.db-shm"]
    kept = ["pembaca.session", "sumber.yaml", "logs/bot.log", "data/jurnal.db"]
    for file in removed + kept:
        Path(file).write_text("fixture", encoding="utf-8")

    reset_bot.reset_bot(reset_bot.BOTS[2])

    assert all(not Path(file).exists() for file in removed)
    assert all(Path(file).read_text(encoding="utf-8") == "fixture" for file in kept)


def test_reset_requires_selected_process_to_be_stopped(workspace, monkeypatch):
    monkeypatch.setattr(reset_bot, "claim", lambda _: None)
    log = Path("logs/bot.log")
    log.touch()
    with pytest.raises(RuntimeError, match="Hentikan bot MT5"):
        reset_bot.reset_bot(reset_bot.BOTS[0])
    assert log.is_file()


def test_configured_path_cannot_replace_unrelated_file(workspace):
    path = Path("data/jurnal.db")
    path.write_text("fixture", encoding="utf-8")
    backup = Path("data/jurnal-cadangan-20260101.db")
    backup.touch()

    with pytest.raises(ValueError, match="diverifikasi sebagai jurnal"):
        reset_bot.reset_bot(reset_bot.BOTS[0])

    assert path.read_text(encoding="utf-8") == "fixture"
    assert backup.is_file()


def test_terminal_reset_requires_confirmation(monkeypatch, capsys):
    picks = iter((0, None))
    monkeypatch.setattr(reset_bot, "choose", lambda _: next(picks))
    monkeypatch.setattr(reset_bot, "ask", lambda _: "batal")
    monkeypatch.setattr(reset_bot, "clear", lambda: None)
    monkeypatch.setattr(reset_bot, "pause", lambda: None)
    monkeypatch.setattr(reset_bot, "reset_bot", lambda _: pytest.fail("reset without confirmation"))
    reset_bot.screen()
    assert "Reset dibatalkan." in capsys.readouterr().out
