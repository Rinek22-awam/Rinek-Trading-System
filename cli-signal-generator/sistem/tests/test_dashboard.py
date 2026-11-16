import os
import tkinter as tk

import pytest

from generator import actions, dashboard, main, settings, skills
from generator.settings import GeneratorError

from test_menu import project  # noqa: F401  (fixture folder proyek sementara)


def test_format_log_lines():
    assert dashboard.format_log("2026-11-14 01:03:44,242 INFO Analisa dimulai.") == (
        "INFO", "14/11 01:03:44  [INFO]     Analisa dimulai.")
    assert dashboard.format_log("2026-11-14 01:06:00,158 INFO ✅ CLI terkirim.")[0] == "AKTIF"
    assert dashboard.format_log("2026-11-14 01:02:01,125 ERROR ❌ gagal")[0] == "ERROR"
    assert dashboard.format_log("baris lanjutan tanpa jam") == ("DEBUG", "baris lanjutan tanpa jam")


def test_log_tail_reads_new_lines_and_handles_rotation(tmp_path):
    path = tmp_path / "generator.log"
    path.write_text("".join(f"baris {n}\n" for n in range(10)), encoding="utf-8")
    tail = dashboard.LogTail(path, first=3)
    assert tail.read() == ["baris 7", "baris 8", "baris 9"]
    assert tail.read() == []
    with path.open("a", encoding="utf-8") as file:
        file.write("baru\n")
    assert tail.read() == ["baru"]
    path.write_text("setelah diputar\n", encoding="utf-8")   # file lebih kecil = log baru
    assert tail.read() == ["setelah diputar"]
    assert dashboard.LogTail(tmp_path / "tidak-ada.log").read() == []
    fresh = dashboard.LogTail(path)                           # bawaan Dashboard: log sesi sebelumnya tidak tampil
    assert fresh.read() == []
    with path.open("a", encoding="utf-8") as file:
        file.write("sesi ini\n")
    assert fresh.read() == ["sesi ini"]
    later = tmp_path / "belum-ada.log"                        # log dibuat setelah Dashboard dibuka: tampil semua
    waiting = dashboard.LogTail(later)
    assert waiting.read() == []
    later.write_text("pertama\nkedua\n", encoding="utf-8")
    assert waiting.read() == ["pertama", "kedua"]


def test_stop_only_kills_real_generator(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PID_FILE", tmp_path / "generator.pid")
    assert actions.generator_process() is None
    main.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")   # proses pytest, bukan generator
    assert actions.generator_process() is None
    monkeypatch.setattr(actions, "generator_running", lambda: False)
    assert actions.stop_generator() is False
    monkeypatch.setattr(actions, "generator_running", lambda: True)
    with pytest.raises(GeneratorError, match="Tutup jendela"):
        actions.stop_generator()


def test_pid_file_written_and_cleared(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PID_FILE", tmp_path / "generator.pid")
    main.write_pid()
    assert main.PID_FILE.read_text(encoding="utf-8") == str(os.getpid())
    main.clear_pid()
    assert not main.PID_FILE.exists()


def test_shared_action_validation(project, tmp_path):
    with pytest.raises(GeneratorError, match="token"):
        actions.save_token("bukan token")
    with pytest.raises(GeneratorError, match="API ID"):
        actions.api_changes("abc", "")
    with pytest.raises(GeneratorError, match="heksadesimal"):
        actions.api_changes("", "xyz")
    with pytest.raises(GeneratorError, match="-100"):
        actions.check_inbox("12345")
    with pytest.raises(GeneratorError, match="angka positif"):
        actions.save_report("abc", "akun")
    actions.save_report("5678", "akun")
    assert settings.read_config().report == "5678"
    with pytest.raises(GeneratorError, match=".bat"):
        actions.save_launcher(tmp_path / "tidak-ada.bat")
    script = tmp_path / "buka.bat"
    script.write_text("@echo off", encoding="utf-8")
    assert actions.save_launcher(f'"{script}"') == script.resolve()
    with pytest.raises(GeneratorError, match="Lama tunggu"):
        actions.save_tv_wait("0")
    actions.save_tv_wait("45")
    assert settings.read_config().tv_wait == 45
    assert settings.read_config().tv_launcher == script.resolve()


@pytest.fixture
def app(project, monkeypatch, tmp_path):
    monkeypatch.setattr(main, "tradingview_ready", lambda: False)
    monkeypatch.setattr(settings, "LOGS", tmp_path / "logs")
    errors = []
    monkeypatch.setattr(dashboard.messagebox, "showerror", lambda title, message, **_: errors.append(message))
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tidak ada layar untuk Tkinter.")
    root.withdraw()
    instance = dashboard.App(root)
    instance.errors = errors
    yield instance
    root.destroy()


def test_dashboard_builds_and_saves_skill(app):
    skills.add_skill("scalp")
    app.refresh_skills(select="scalp")
    page = app.skill
    assert page.selected() == "scalp"
    assert page.hours.get() == ""
    assert "Simpan kosong = ikut global (09:00, 14:00, 20:00 WIB)" in page.hours_hint.cget("text")
    page.copy_global_times()
    assert page.hours.get() == "09:00, 14:00, 20:00"
    assert page.every.get() == "60"
    page.mode.set("interval")
    page.every.set("90")
    for widget, value in ((page.begin, "20:00"), (page.end, "04:00")):
        widget.delete(0, "end")
        widget.insert(0, value)
    page.on.set(True)
    page.model.set("claude-opus-5-5")
    page.effort.set("high")
    page.limit.insert(0, "12")
    page.save()
    skill = skills.read_skill("scalp", settings.read_config())
    assert (skill.enabled, skill.mode, skill.every, skill.start, skill.end) == (True, "interval", 90, "20:00", "04:00")
    assert skill.times == ()
    assert (skill.config.model, skill.config.effort, skill.config.timeout) == ("claude-opus-5-5", "high", 720)
    page.model.set(page.model["values"][0])   # ikut global
    page.effort.set(page.effort["values"][1])   # bawaan Claude Code
    page.limit.delete(0, "end")
    page.save()
    skill = skills.read_skill("scalp", settings.read_config())
    assert (skill.config.model, skill.config.effort, skill.config.timeout) == ("sonnet", "", 600)
    app.home.refresh()
    app.refresh_skills()
    assert app.home.tree.item(app.home.tree.get_children()[0], "text") == "scalp"
    assert "tiap 90 menit 20:00–04:00" in app.home.tree.item(app.home.tree.get_children()[0], "values")[1]
    page.mode.set("jam")
    page.save()
    skill = skills.read_skill("scalp", settings.read_config())
    assert skill.use_global_times and skill.times == ("09:00", "14:00", "20:00")
    assert not app.errors


def test_dashboard_tool_and_routine_pages(app):
    tool = app.tool
    tool.reset()
    tool.save()
    config = settings.read_config()
    assert config.builtin_tools == ("WebSearch", "WebFetch") and len(config.tv_tools) == 13
    page = app.telegram
    page.routine_hours.delete(0, "end")
    page.routine_hours.insert(0, "19:00, 07:00")
    page.routine_on.set(False)
    page.save_routine()
    assert dashboard.heartbeat.read() == dashboard.heartbeat.Routine(False, ("07:00", "19:00"))
    page.routine_hours.delete(0, "end")
    page.routine_hours.insert(0, "7 pagi")
    with pytest.raises(GeneratorError):
        page.save_routine()
    assert "Kirim lewat: bot" in app.status_line()


def test_dashboard_reports_skipped_analysis_and_keeps_message(app, monkeypatch, tmp_path):
    monkeypatch.setattr(main, "MESSAGE_DIR", tmp_path / "pesan")
    home = app.home
    path = actions.save_message("fokus 4100")
    home.process_message = ("fokus 4100", path)
    home.finish_analysis(main.EXIT_SKIPPED)
    assert "dilewati" in home.run_state.cget("text") and "disimpan" in home.run_state.cget("text")
    assert home.leftover == "fokus 4100" and not path.exists()
    launched = []
    monkeypatch.setattr(actions, "analysis_running", lambda: False)
    monkeypatch.setattr(actions, "launch", lambda *args: launched.append(args) or object())
    home.start_analysis(None, "fokus 4100")       # jalan lagi: pesan sisa dipakai, lalu dilupakan
    assert home.leftover == "" and launched[0][:3] == ("once", "--pesan-file", str(home.process_message[1]))
    home.finish_analysis(0)
    assert home.run_state.cget("text") == "Analisa selesai." and home.process is None
    assert "Kirim lewat: bot" in home.info.cget("text")   # baris status diisi refresh_skills


def test_generator_follows_dashboard(monkeypatch):
    import subprocess
    import sys
    assert not main.parent_gone(None) and not main.parent_gone(os.getpid())
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    assert main.parent_gone(child.pid)
    launched = []
    monkeypatch.setattr(actions, "generator_running", lambda: False)
    monkeypatch.setattr(actions, "launch", lambda *args, window: launched.append((args, window)))
    actions.start_generator(window=False, parent=1234)
    actions.start_generator()
    assert launched == [(("daemon", "--induk", "1234"), False), (("daemon",), True)]
