import logging
import os
import sys
from dataclasses import replace
from datetime import datetime
from tempfile import TemporaryDirectory
import psutil
import pytest

from generator import claude, main, settings, telegram
from generator.settings import Config, GeneratorError, REQUIRED_DENY, WIB

CONFIG = Config(None, "sonnet", 10, (REQUIRED_DENY, "mcp__tradingview__tv_update"),
                "bot", "-1001234", "5678", True, frozenset({"15:00"}))
SECRETS = {"GEN_TG_BOT_TOKEN": "test:secret", "GEN_TG_API_ID": "12981273", "GEN_TG_API_HASH": "fake-secret-hash"}


@pytest.mark.parametrize(("answer", "blocks"), [
    ("analisa\n\x60\x60\x60text\nSETUP\nKode: TES\n\x60\x60\x60\nakhir", ["SETUP\nKode: TES"]),
    ("SETUP\r\n  A:  satu  \r\nB: dua\r\n\r\nakhir", ["SETUP\r\n  A:  satu  \r\nB: dua"]),
    ("SETUP\nsatu\n\nSETUP\ndua", ["SETUP\nsatu", "SETUP\ndua"]),
    ("SETUP\nsatu\nSETUP\ndua", ["SETUP\nsatu", "SETUP\ndua"]),
    ("teks acak berisi SETUP", []),
    ("NO SETUP - alasan", []),
    ("NO ENTRY: alasan", []),
])
def test_extract(answer, blocks):
    assert claude.extract_setups(answer) == blocks


def test_extract_low_conviction_kept_apart():
    answer = "SETUP\nKode: TES\nSETUP RENDAH\nKode: TES\nArah: BUY\n\nSETUP\nKode: B"
    assert claude.extract_setups(answer) == ["SETUP\nKode: TES", "SETUP\nKode: B"]
    assert claude.extract_setups(answer, claude.LOW_LABEL) == ["SETUP RENDAH\nKode: TES\nArah: BUY"]


@pytest.mark.parametrize(("answer", "inbox", "status"), [
    ("analisa\nSETUP RENDAH\nKode: TES\n\nSETUP\nKode: TES", 1, "✅"),
    ("analisa\nSETUP RENDAH\nKode: TES", 0, "⚪"),   # cuma blok rendah: bukan "tidak dikenali"
])
def test_low_conviction_goes_to_pm_only(monkeypatch, answer, inbox, status):
    calls = []

    class FakeSender:
        def send(self, chat, text):
            calls.append((chat, text))

    monkeypatch.setattr(telegram, "BotSender", lambda _: FakeSender())
    monkeypatch.setattr(main, "ensure_tradingview", lambda *_: None)
    monkeypatch.setattr(main, "analyze", lambda *_: answer)
    assert main.run_analysis(replace(CONFIG, send_analysis=False), SECRETS)
    assert [text for chat, text in calls if chat == CONFIG.inbox] == ["SETUP\nKode: TES"] * inbox
    pm = [text for chat, text in calls if chat == CONFIG.report]
    assert "SETUP RENDAH\nKode: TES" in pm                           # blok rendah jadi pesan sendiri di PM
    assert pm[-1].startswith(status) and "1 setup conviction rendah" in pm[-1]


@pytest.mark.parametrize(("answer", "expected"), [
    ("NO SETUP - alasan", True), ("Pembuka\nNO ENTRY: alasan", True),
    ("teks acak", False), ("Kode: NO SETUP", False),
])
def test_no_setup(answer, expected):
    assert claude.no_setup(answer) is expected


def test_deny_and_command():
    config = replace(CONFIG, denied_tools=("mcp__tradingview__tv_update",))
    command = claude.build_command("claude.exe", config)
    assert command[:2] == ["claude.exe", "-p"]
    assert "--strict-mcp-config" in command
    assert command[command.index("--mcp-config") + 1] == str(settings.SYSTEM / "mcp-tradingview.json")
    assert "--dangerously-skip-permissions" in command
    assert set(command[command.index("--disallowed-tools") + 1].split(",")) == {
        REQUIRED_DENY, "mcp__tradingview__tv_update",
    }


def test_config_restores_required_deny(tmp_path, monkeypatch, caplog):
    content = (settings.SYSTEM / "config.example.yaml").read_text(encoding="utf-8")
    updated = "\n".join(line for line in content.splitlines() if REQUIRED_DENY not in line)
    (tmp_path / "config.yaml").write_text(updated, encoding="utf-8")
    monkeypatch.setattr(settings, "SYSTEM", tmp_path)
    assert REQUIRED_DENY in settings.read_config().denied_tools
    assert "otomatis ditambahkan" in caplog.text


@pytest.mark.parametrize(("program", "error"), [
    ('assert "OANDA:XAUUSD" in sys.stdin.buffer.read().decode("utf-8"); print(json.dumps({"result":"SETUP\\nKode: TES","is_error":False}))', None),
    ('print(json.dumps({"result":"kuota habis","is_error":True}))', "kuota habis"),
    ('sys.stderr.write("keluar gagal"); sys.exit(5)', "keluar gagal"),
    ('print("teks acak")', "tidak dikenali"),
])
def test_fake_claude(program, error, tmp_path, monkeypatch):
    monkeypatch.setattr(claude, "DATA", tmp_path)
    monkeypatch.setattr(claude, "TemporaryDirectory", lambda **kw: TemporaryDirectory(dir=tmp_path, **kw))
    monkeypatch.setattr(claude.shutil, "which", lambda _: sys.executable)
    monkeypatch.setattr(claude, "build_command", lambda executable, config: [
        executable, "-c", "import sys,json; " + program,
    ])
    if error:
        with pytest.raises(GeneratorError, match=error):
            claude.analyze(CONFIG, SECRETS)
    else:
        assert claude.analyze(CONFIG, SECRETS) == "SETUP\nKode: TES"
    assert len(list((tmp_path / "hasil").glob("*.txt"))) == 1


def test_timeout_kills_tree(tmp_path):
    marker = tmp_path / "child.pid"
    if os.name != "nt":
        pytest.skip("taskkill khusus Windows")
    parent = """
import subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
Path(sys.argv[1]).write_text(str(child.pid))
time.sleep(60)
"""
    with pytest.raises(GeneratorError, match="batas waktu"):
        claude.execute([sys.executable, "-c", parent, str(marker)], "", 2, tmp_path)
    pid = int(marker.read_text())
    try:
        child = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    child.wait(timeout=3)


def test_sender_selection(monkeypatch):
    class FakeAccount:
        def __init__(self, secrets):
            self.secrets = secrets
            self.closed = False

        def close(self):
            self.closed = True

    monkeypatch.setattr(telegram, "AccountSender", FakeAccount)
    with telegram.open_sender(CONFIG, SECRETS) as sender:
        assert isinstance(sender, telegram.BotSender)
    with telegram.open_sender(replace(CONFIG, sender="akun"), SECRETS) as sender:
        assert sender.secrets is SECRETS
    assert sender.closed


def test_retry_and_routing(monkeypatch):
    calls = []
    failures = iter([True, False])

    class FakeSender:
        def send(self, chat, text):
            calls.append((chat, text))
            if chat == CONFIG.inbox and next(failures):
                raise telegram.RetryableSend()

    setup = "SETUP\nKode: TES\nCatatan:  asli"
    monkeypatch.setattr(telegram, "BotSender", lambda _: FakeSender())
    monkeypatch.setattr(main, "ensure_tradingview", lambda *_: None)
    monkeypatch.setattr(main, "analyze", lambda *_: setup)
    monkeypatch.setattr(main, "send_with_retry", lambda *args: telegram.send_with_retry(*args, sleep=lambda _: None))
    assert main.run_analysis(CONFIG, SECRETS)
    assert calls[:3] == [
        (CONFIG.report, "CLI mulai analisa."),
        (CONFIG.inbox, setup), (CONFIG.inbox, setup),
    ]
    assert calls[3][0] == CONFIG.report and calls[3][1].startswith("📊 Analisa CLI") and setup in calls[3][1]
    assert calls[4] == (CONFIG.report, "✅ CLI terkirim. 1 blok SETUP masuk ke kotak masuk.")
    calls.clear()
    failures = iter([False])
    assert main.run_analysis(replace(CONFIG, send_analysis=False), SECRETS)
    assert all(not text.startswith("📊") for _, text in calls)


def test_analysis_sent_to_pm_even_when_no_setup(monkeypatch):
    calls = []

    class FakeSender:
        def send(self, chat, text):
            calls.append((chat, text))

    monkeypatch.setattr(telegram, "BotSender", lambda _: FakeSender())
    monkeypatch.setattr(main, "ensure_tradingview", lambda *_: None)
    monkeypatch.setattr(main, "analyze", lambda *_: "Harga di tengah range.\nNO ENTRY - tunggu zona")
    assert main.run_analysis(CONFIG, SECRETS)
    assert [chat for chat, _ in calls] == [CONFIG.report] * 3        # mulai, analisa, status
    assert "NO ENTRY" in calls[1][1] and calls[2][1].startswith("⚪")
    assert calls[2][1].endswith("NO ENTRY - tunggu zona")                # alasan ikut di status
    calls.clear()
    monkeypatch.setattr(main, "analyze", lambda *_: "Rilis 20:30.\nNO SETUP - HIGH IMPACT: NFP 20:30 WIB")
    assert main.run_analysis(replace(CONFIG, send_analysis=False), SECRETS)
    assert calls[-1][1].endswith("NO SETUP - HIGH IMPACT: NFP 20:30 WIB")   # tetap sampai walau analisa ke PM mati


def test_split_message_respects_limit():
    text = "\n".join(f"baris {i} " + "x" * 50 for i in range(300)) + "\n" + "y" * 9000
    parts = main.split_message(text, limit=4000)
    assert all(len(part) <= 4000 for part in parts)
    assert "".join(parts).replace("\n", "") == text.replace("\n", "")
    assert main.split_message("pendek") == ["pendek"]


def test_report_chat_detected_and_saved(tmp_path, monkeypatch):
    system = make_project(tmp_path)
    monkeypatch.setattr(settings, "SYSTEM", system)
    settings.set_config_value("telegram", "laporan_ke", "")
    config = settings.read_config()
    assert config.report == ""
    bot = telegram.BotSender("x:y")
    monkeypatch.setattr(bot, "call", lambda method, **f: [
        {"message": {"chat": {"id": -100555, "type": "channel"}}},
        {"message": {"chat": {"id": 777, "type": "private"}}},
        {"my_chat_member": {}},
    ])
    resolved = main.resolve_report(config, bot)
    assert resolved.report == "777"
    text = (system / "config.yaml").read_text(encoding="utf-8")
    assert 'laporan_ke: "777"' in text and "dideteksi otomatis" in text
    assert settings.read_config().report == "777"


def test_report_chat_not_started(tmp_path, monkeypatch):
    bot = telegram.BotSender("x:y")
    monkeypatch.setattr(bot, "call", lambda method, **f: [])
    with pytest.raises(GeneratorError, match="tekan Start"):
        main.resolve_report(replace(CONFIG, report=""), bot)
    assert main.resolve_report(replace(CONFIG, report=""), object()).report == "me"   # mode akun


def test_effort_flag_only_when_set():
    assert "--effort" not in claude.build_command("claude.exe", CONFIG)
    command = claude.build_command("claude.exe", replace(CONFIG, model="opus", effort="xhigh"))
    assert command[command.index("--model") + 1] == "opus"
    assert command[command.index("--effort") + 1] == "xhigh"
    assert "--fallback-model" not in command    # keputusan user: tanpa model cadangan


def test_claude_settings_menu(tmp_path, monkeypatch, capsys):
    system = make_project(tmp_path)
    monkeypatch.setattr(settings, "SYSTEM", system)

    saved = {"model": "sonnet", "effort": ""}

    def run(*answers):
        replies = iter(answers)
        current = replace(settings.read_config(), **saved)
        return main.pick_claude_settings(current, ask=lambda _: next(replies),
                                         save=lambda model, effort: saved.update(model=model, effort=effort))

    assert run("2", "4")                                  # opus, xhigh
    assert saved == {"model": "opus", "effort": "xhigh"}
    assert run("5", "claude-opus-5-5", "6")               # manual, effort bawaan
    assert saved == {"model": "claude-opus-5-5", "effort": ""}
    assert run("3", "")                                   # ganti model, Enter = effort tetap (model tetap tersimpan)
    assert saved == {"model": "sonnet", "effort": ""}
    assert run("", "2")                                   # Enter = model tetap, ganti effort saja
    assert saved == {"model": "sonnet", "effort": "medium"}
    assert run("1", "0")                                  # 0 di langkah effort = batal, tidak ada yang berubah
    assert run("0")                                       # 0 di langkah model = batal
    assert saved == {"model": "sonnet", "effort": "medium"}
    assert not run("5", "nama salah!")                    # nama tidak valid -> tidak diubah
    assert not run("9")                                   # nomor tidak ada
    assert not run("1", "7")                              # effort tidak ada
    assert saved == {"model": "sonnet", "effort": "medium"}


def test_invalid_effort_rejected(tmp_path, monkeypatch):
    content = (settings.SYSTEM / "config.example.yaml").read_text(encoding="utf-8").replace('effort: ""', 'effort: "super"')
    monkeypatch.setattr(settings, "SYSTEM", make_project(tmp_path, content))
    with pytest.raises(GeneratorError):
        settings.read_config()


def test_refusal_messages_are_clear():
    assert "tekan Start" in telegram.explain_refusal(403, "Forbidden: bot can't initiate conversation with a user")
    assert "admin" in telegram.explain_refusal(400, "Bad Request: not enough rights to send text messages to the chat")
    assert "Token" in telegram.explain_refusal(401, "Unauthorized")


def test_tradingview_chrome_already_open(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "tradingview_ready", lambda: True)
    monkeypatch.setattr(main.subprocess, "run", lambda *a, **k: pytest.fail("Chrome tidak perlu dibuka"))
    main.ensure_tradingview(replace(CONFIG, tv_launcher=tmp_path / "tidak-ada.bat"))


def test_tradingview_chrome_launched_without_pipes(monkeypatch, tmp_path):
    launcher = tmp_path / "launch_tv_chrome.bat"
    launcher.write_text("@echo off", encoding="utf-8")
    states = iter([False, False, True])
    calls = []
    monkeypatch.setattr(main, "tradingview_ready", lambda: next(states))
    monkeypatch.setattr(main.subprocess, "run", lambda cmd, **kw: calls.append((cmd, kw)))
    main.ensure_tradingview(replace(CONFIG, tv_launcher=launcher), sleep=lambda _: None)
    assert calls[0][0] == ["cmd", "/c", str(launcher)]
    assert all(calls[0][1][name] is main.subprocess.DEVNULL for name in ("stdin", "stdout", "stderr"))


def test_tradingview_chrome_not_ready(monkeypatch, tmp_path):
    launcher = tmp_path / "launch_tv_chrome.bat"
    launcher.write_text("@echo off", encoding="utf-8")
    ticks = iter(range(0, 1000, 5))
    monkeypatch.setattr(main, "tradingview_ready", lambda: False)
    monkeypatch.setattr(main.subprocess, "run", lambda *a, **k: None)
    with pytest.raises(GeneratorError, match="TradingView tidak siap"):
        main.ensure_tradingview(replace(CONFIG, tv_launcher=launcher, tv_wait=20),
                                sleep=lambda _: None, clock=lambda: next(ticks))


def test_tradingview_launcher_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "tradingview_ready", lambda: False)
    with pytest.raises(GeneratorError, match="peluncur TradingView tidak ditemukan"):
        main.ensure_tradingview(replace(CONFIG, tv_launcher=tmp_path / "tidak-ada.bat"))


def test_config_reads_tradingview_desktop_launcher(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SYSTEM", make_project(tmp_path))
    config = settings.read_config()
    assert config.tv_launcher.name == "launch_tv_debug.bat"
    assert "mcp__tradingview__tv_launch" in config.denied_tools


def make_project(tmp_path, config_text=None):
    """Proyek tiruan: sistem\\config.yaml + skill\\ dengan beberapa skill."""
    system = tmp_path / "sistem"
    system.mkdir()
    content = config_text or (settings.SYSTEM / "config.example.yaml").read_text(encoding="utf-8")
    if "\nskill:" not in "\n" + content:
        content += '\nskill:\n  aktif: ""  # Nama folder di skill\\.\n  kode: TES\n'
    (system / "config.yaml").write_text(content, encoding="utf-8")
    skills = tmp_path / "skill"
    for name, body in (("contoh", "---\nname: contoh\ndescription: Skill uji.\n---\n# Contoh\nNO SETUP"),
                       ("xau-a", "# A"), ("_draft", "# draft")):
        (skills / name).mkdir(parents=True)
        (skills / name / "SKILL.md").write_text(body, encoding="utf-8")
    (skills / "tanpa-file").mkdir()
    (skills / "FORMAT_SETUP.md").write_text("FORMAT ISI", encoding="utf-8")
    return system


def test_list_skills_hides_drafts_and_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SYSTEM", make_project(tmp_path))
    assert settings.list_skills() == ["contoh", "xau-a"]


def test_legacy_folder_key_still_read(tmp_path, monkeypatch):
    content = (settings.SYSTEM / "config.example.yaml").read_text(encoding="utf-8")
    legacy = content.replace('aktif: ""', 'folder: "skill/xau-a"') if "\nskill:" in "\n" + content else (
        content + '\nskill:\n  folder: "skill/xau-a"\n  kode: TES\n'
    )
    system = make_project(tmp_path, legacy)
    monkeypatch.setattr(settings, "SYSTEM", system)
    assert settings.read_config().skill_name == "xau-a"


def test_prompt_inlines_skill_format_and_code(tmp_path, monkeypatch):
    system = make_project(tmp_path)
    (system / "prompt").mkdir()
    (system / "prompt" / "mode_otomatis.md").write_text("ATURAN OTOMATIS", encoding="utf-8")
    monkeypatch.setattr(settings, "SYSTEM", system)
    monkeypatch.setattr(claude, "SYSTEM", system)
    config = replace(CONFIG, skill=tmp_path / "skill" / "xau-a", skill_name="xau-a", setup_code="TES")
    prompt = claude.build_prompt(config)
    order = [prompt.index(part) for part in ("ATURAN OTOMATIS", "FORMAT ISI", "# A", "Kode: TES", "Waktu sekarang")]
    assert order == sorted(order)
    assert "Sumber: cli_xau-a" in prompt
    with pytest.raises(GeneratorError, match="SKILL.md tidak ditemukan"):
        claude.build_prompt(replace(config, skill=tmp_path / "skill" / "tanpa-file"))


def test_ready_lists_skills(tmp_path, monkeypatch, capsys):
    system = make_project(tmp_path)
    monkeypatch.setattr(settings, "SYSTEM", system)
    (tmp_path / "skill" / "contoh" / "setelan.yaml").write_text(
        'nyala: true\njadwal:\n  jam: ["15:00"]\n  hari: [sen, sel, rab, kam, jum]\nkode: S04\n', encoding="utf-8")
    (tmp_path / "skill" / "xau-a" / "setelan.yaml").write_text("nyala: true\njadwal:\n  jam: []\n", encoding="utf-8")
    checks = main.skill_status(settings.read_config(), lambda ok, message: print(message) or bool(ok))
    output = capsys.readouterr().out
    assert checks == []
    assert "Skill contoh [NYALA] · 15:00 Sen–Jum" in output and "bukan TES" in output
    assert "xau-a NYALA tapi jadwalnya kosong" in output
    assert "_draft" not in output and "tanpa-file" not in output and "mode uji fungsi" not in output


def test_bot_plain_text(monkeypatch):
    sender = telegram.BotSender(SECRETS["GEN_TG_BOT_TOKEN"])
    calls = []
    monkeypatch.setattr(sender, "call", lambda method, **fields: calls.append((method, fields)))
    sender.send(CONFIG.inbox, "SETUP\n*literal*")
    assert calls == [("sendMessage", {"chat_id": int(CONFIG.inbox), "text": "SETUP\n*literal*"})]


def free_port():
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_lock_skips_concurrent_analysis(monkeypatch, caplog):
    port = free_port()
    monkeypatch.setattr(main, "WORK_PORT", port)
    monkeypatch.setattr(main, "LOCK_WAIT_SECONDS", 0.3)
    monkeypatch.setattr(main, "run_analysis", lambda *_: pytest.fail("Analisa bentrok tetap dipanggil"))
    with main.local_lock(port) as acquired:
        assert acquired
        with caplog.at_level(logging.WARNING):
            assert main.once(CONFIG, SECRETS) == main.SKIPPED
    assert "Analisa dilewati" in caplog.text


def test_analysis_waits_for_brief_lock(monkeypatch):
    """Kunci yang dipegang sebentar (cek status Dashboard) tidak lagi membuat analisa dilewati."""
    import threading

    port = free_port()
    monkeypatch.setattr(main, "WORK_PORT", port)
    monkeypatch.setattr(main, "run_analysis", lambda *_: True)
    held, release = threading.Event(), threading.Event()

    def holder():
        with main.local_lock(port) as acquired:
            assert acquired
            held.set()
            release.wait(5)

    thread = threading.Thread(target=holder)
    thread.start()
    held.wait(5)
    threading.Timer(0.4, release.set).start()
    assert main.once(CONFIG, SECRETS) is True
    thread.join(5)


def test_skipped_analysis_keeps_message_file(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "MESSAGE_DIR", tmp_path / "pesan")
    monkeypatch.setattr(main, "read_secrets", lambda: SECRETS)
    monkeypatch.setattr(main, "read_config", lambda: CONFIG)
    monkeypatch.setattr(main, "setup_logging", lambda secrets: None)
    monkeypatch.setattr(main.skills, "migrate", lambda: None)
    path = tmp_path / "pesan" / "pesan-uji.txt"
    path.parent.mkdir()
    for result, code, kept in ((main.SKIPPED, main.EXIT_SKIPPED, True), (True, 0, False), (False, 1, False)):
        path.write_text("fokus 4100", encoding="utf-8")
        monkeypatch.setattr(main, "once", lambda config, secrets, result=result: result)
        monkeypatch.setattr(sys, "argv", ["generator", "once", "--pesan-file", str(path)])
        assert main.main() == code
        assert path.exists() is kept


def test_no_secret_in_output(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(main, "LOGS", tmp_path)
    monkeypatch.setattr(claude, "DATA", tmp_path)
    isolated = logging.Logger("test")
    monkeypatch.setattr(logging, "basicConfig", lambda **kw: [isolated.addHandler(h) for h in kw["handlers"]])
    main.setup_logging(SECRETS)
    secret_text = " ".join(SECRETS.values())
    isolated.warning("error %s", secret_text)
    claude.save_result(secret_text, SECRETS)
    for handler in isolated.handlers:
        handler.close()
    output = capsys.readouterr().err + (tmp_path / "generator.log").read_text(encoding="utf-8")
    output += next((tmp_path / "hasil").glob("*.txt")).read_text(encoding="utf-8")
    assert "[disamarkan]" in output
    assert all(secret not in output for secret in SECRETS.values())
