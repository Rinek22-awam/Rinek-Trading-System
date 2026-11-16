
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from generator import claude, main, menu, settings, skills
from generator.settings import GeneratorError, REQUIRED_DENY, WIB


@pytest.fixture
def project(tmp_path, monkeypatch):
    system = tmp_path / "sistem"
    (system / "prompt").mkdir(parents=True)
    original = settings.SYSTEM
    for name in ("kerangka_skill.md", "mode_otomatis.md"):
        (system / "prompt" / name).write_bytes((original / "prompt" / name).read_bytes())
    (system / "config.yaml").write_text("""claude:
  model: sonnet
  effort: high
  batas_waktu_menit: 10
  tools_ditolak:
    - mcp__tradingview__ui_evaluate # terkunci
    - mcp__tradingview__tv_update # komentar update
telegram:
  pengirim: bot
  kotak_masuk: ""
  laporan_ke: ""
""", encoding="utf-8")
    monkeypatch.setattr(settings, "SYSTEM", system)
    monkeypatch.setattr(claude, "SYSTEM", system)
    return system


def test_toggle_multiple_and_create_manual(project, monkeypatch):
    opened = []
    monkeypatch.setattr(menu, "ask", lambda _: "xau-intraday")
    monkeypatch.setattr(menu, "open_path", opened.append)
    menu.Menu().add(settings.read_config())
    manual = project.parent / "skill" / "xau-scalp"
    manual.mkdir()
    (manual / "SKILL.md").write_text("Skill manual", encoding="utf-8")
    base = settings.read_config()
    names = ["xau-intraday", "xau-scalp"]
    assert settings.list_skills() == names
    assert opened == [manual.with_name("xau-intraday")]
    assert "name: xau-intraday" in (opened[0] / "SKILL.md").read_text(encoding="utf-8")
    assert not any(skills.read_skill(name, base).enabled for name in names)
    assert (manual / "setelan.yaml").is_file()
    assert all(skills.toggle_skill(name, base) for name in names)
    assert all(skills.read_skill(name, base).enabled for name in names)
    assert not skills.toggle_skill(names[0], base)
    target = skills.archive_skill(names[1])
    assert target.parent.name == "_terhapus"
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "Skill manual"
    assert settings.list_skills() == names[:1]


def test_per_skill_config_and_real_confirmation(project, monkeypatch):
    folder = skills.add_skill("uji")
    base = settings.read_config()
    default = skills.read_skill("uji", base).config
    assert (default.model, default.effort, default.timeout, default.setup_code) == ("sonnet", "high", 600, "TES")
    assert not skills.change_code("uji", "S04", lambda _: "real")
    assert skills.read_skill("uji", base).config.setup_code == "TES"
    assert skills.change_code("uji", "S04", lambda _: "REAL")
    replies = iter(["2", "4"])
    monkeypatch.setattr(menu, "ask", lambda _: next(replies))
    menu.Menu().model(skills.read_skill("uji", base))
    skills.change_skill("uji", {("", "batas_waktu_menit"): 3})
    config = skills.read_skill("uji", base).config
    command = claude.build_command("claude", config)
    assert command[command.index("--model") + 1] == "opus"
    assert command[command.index("--effort") + 1] == "xhigh"
    assert config.timeout == 180
    assert "Kode: S04" in claude.build_prompt(config)
    assert "Sumber: cli_uji" in claude.build_prompt(config)
    assert "# Kode sumber" in (folder / "setelan.yaml").read_text(encoding="utf-8")
    assert skills.change_code("uji", "TES", lambda _: pytest.fail("TES tidak butuh konfirmasi"))


def test_due_queue_reloads_and_keeps_order(project, monkeypatch):
    names = ["a", "b"]
    for name in names:
        skills.add_skill(name)
        skills.change_skill(name, {("", "nyala"): True, ("jadwal", "jam"): ["15:00"],
                                   ("jadwal", "hari"): ["jum"]})
    friday = datetime(2026, 11, 13, 15, 0, tzinfo=WIB)
    assert not main.load_due(friday.replace(day=14))
    assert not main.load_due(friday.replace(minute=1))
    seen = []
    monkeypatch.setattr(main, "run_analysis", lambda config, _: seen.append(config.skill_name))
    main.run_queue(main.load_due(friday), {})
    assert seen == names
    skills.toggle_skill("a", settings.read_config())
    assert [config.skill_name for config in main.load_due(friday)] == ["b"]


def test_queue_survives_unexpected_errors(project, monkeypatch):
    for name in ("a", "b", "c"):
        skills.add_skill(name)
    base = settings.read_config()
    configs = [skills.read_skill(name, base).config for name in ("a", "b", "c")]
    seen, reported = [], []

    def fake_analysis(config, _):
        seen.append(config.skill_name)
        if config.skill_name == "a":
            raise KeyError("rahasia-jangan-dicatat")
        if config.skill_name == "b":
            raise GeneratorError("Token bot ditolak Telegram.")

    monkeypatch.setattr(main, "run_analysis", fake_analysis)
    monkeypatch.setattr(main, "report_failure", lambda config, _, detail: reported.append((config.skill_name, detail)))
    main.run_queue(configs, {})
    assert seen == ["a", "b", "c"]                       # skill berikutnya tetap jalan
    assert reported == [("a", "Gangguan tak terduga, cek log generator."), ("b", "Token bot ditolak Telegram.")]

    # Pekerja antrean tidak mati walaupun run_queue sendiri error.
    class Stop(BaseException):
        pass

    jobs = iter([([], {}), ([], {})])
    calls = []

    def fake_get():
        try:
            return next(jobs)
        except StopIteration:
            raise Stop from None

    def broken_queue(*_):
        calls.append(1)
        raise ValueError("rusak")

    monkeypatch.setattr(main, "run_queue", broken_queue)
    with pytest.raises(Stop):
        main.consume_queue(type("Q", (), {"get": staticmethod(fake_get)})())
    assert len(calls) == 2                               # pekerjaan kedua tetap diambil


def test_report_failure_never_raises(project, monkeypatch):
    config = skills.read_skill(skills.add_skill("a").name, settings.read_config()).config
    monkeypatch.setattr(main, "open_sender", lambda *_: (_ for _ in ()).throw(RuntimeError("putus")))
    main.report_failure(config, {}, "detail")            # tidak boleh melempar error


def test_migration_keeps_backup_and_is_idempotent(project):
    path = project / "config.yaml"
    skills.add_skill("lama")
    old = path.read_text(encoding="utf-8") + (
        "\nskill:\n  aktif: lama  # lama\n  kode: S04\n"
        "uji_coba_jam:\n  nyala: true\n  jam: ['15:00']\n"
    )
    path.write_text(old, encoding="utf-8")
    skills.migrate()
    skill = skills.read_skill("lama", settings.read_config())
    assert skill.enabled and skill.times == ("15:00",) and skill.days == skills.DAYS
    assert skill.config.setup_code == "S04"
    assert "skill" not in yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "uji_coba_jam" not in yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "# lama" in path.read_text(encoding="utf-8")
    skills.toggle_skill("lama", settings.read_config())
    skills.migrate()
    backups = list((project / "data").glob("cadangan-setelan-*/config.yaml"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == old
    assert not skills.read_skill("lama", settings.read_config()).enabled


def test_secret_edit_preserves_unrelated_lines(project, capsys, caplog):
    path = project.parent / ".env"
    path.write_bytes(b"# komentar\r\nOTHER=foo # tetap\r\nGEN_TG_BOT_TOKEN=\r\n")
    dummy = "123:test-token"
    settings.save_secrets({"GEN_TG_BOT_TOKEN": dummy, "GEN_TG_API_ID": "17"})
    assert path.read_bytes().startswith(b"# komentar\r\nOTHER=foo # tetap\r\n")
    assert settings.read_secrets()["GEN_TG_BOT_TOKEN"] == dummy
    assert dummy not in capsys.readouterr().out + caplog.text


def test_deny_locked_and_comments_preserved(project, monkeypatch):
    instance = menu.Menu()
    monkeypatch.setattr(menu, "ask", lambda _: "1")
    with pytest.raises(GeneratorError, match="terkunci"):
        instance.remove_deny(settings.read_config())
    monkeypatch.setattr(menu, "ask", lambda _: "2")
    instance.remove_deny(settings.read_config())
    assert settings.read_config().denied_tools == (REQUIRED_DENY,)
    text = (project / "config.yaml").read_text(encoding="utf-8")
    assert "# komentar update" in text
    assert "# terkunci" in text


def test_channel_discovery_shares_updates_without_offset(monkeypatch):
    channel = {"id": -100555, "type": "channel", "title": "Uji"}
    updates = [
        {"my_chat_member": {"chat": channel, "new_chat_member": {"status": "administrator"}}},
        {"message": {"chat": {"id": 15, "type": "private"}, "forward_origin": {"chat": channel}}},
    ]
    bot = menu.BotSender("fake")
    calls = []
    monkeypatch.setattr(bot, "call", lambda method, **fields: calls.append((method, fields)) or updates)
    assert bot.find_private_chat() == 15
    assert menu.candidate_channels(bot.updates(), False) == [channel]
    assert menu.candidate_channels(bot.updates(), True) == [channel]
    assert all(method == "getUpdates" and "offset" not in fields for method, fields in calls)
