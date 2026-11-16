from datetime import datetime, timedelta

import pytest

from generator import heartbeat, journal, main, settings, skills
from generator.settings import GeneratorError, WIB

from test_menu import project  # noqa: F401  (fixture folder proyek sementara)

FRIDAY = datetime(2026, 11, 13, 0, 0, tzinfo=WIB)   # Jumat


def at(day_offset, hour, minute=0):
    return FRIDAY + timedelta(days=day_offset, hours=hour, minutes=minute)


def interval_skill(project, every=1, start="20:00", end="04:00", days=("sen", "sel", "rab", "kam", "jum")):
    skills.add_skill("scalp")
    skills.change_skill("scalp", {("", "nyala"): True, ("jadwal", "mode"): "interval",
                                  ("jadwal", "tiap_jam"): every, ("jadwal", "mulai"): start,
                                  ("jadwal", "sampai"): end, ("jadwal", "hari"): list(days)})
    return skills.read_skill("scalp", settings.read_config())


def test_interval_crosses_midnight_and_follows_start_day(project):
    skill = interval_skill(project)
    assert skill.describe() == "tiap 60 menit 20:00–04:00 Sen–Jum"
    assert skill.slots() == ["20:00", "21:00", "22:00", "23:00", "00:00", "01:00", "02:00", "03:00", "04:00"]
    assert skill.due(at(0, 20)) and skill.due(at(0, 23))       # Jumat malam
    assert skill.due(at(1, 0)) and skill.due(at(1, 4))         # Sabtu dini hari: jendela mulai Jumat
    assert not skill.due(at(1, 5)) and not skill.due(at(1, 20))   # Sabtu bukan hari aktif
    assert not skill.due(at(3, 1))                             # Senin 01:00: jendela Minggu tidak aktif
    assert skill.due(at(3, 21)) and not skill.due(at(0, 20, 30))


def test_interval_fraction_and_same_day_window(project):
    skill = interval_skill(project, every=1.5, start="14:00", end="19:00")
    assert skill.slots() == ["14:00", "15:30", "17:00", "18:30"]
    assert skill.today(at(0, 0)) == ["14:00", "15:30", "17:00", "18:30"]
    assert skill.describe().startswith("tiap 90 menit")


@pytest.mark.parametrize(("hours", "expected"), [(0.25, 15), (0.5, 30), (2, 120), (0.1, None), (25, None),
                                                 ("1", None), (True, None)])
def test_interval_limits(hours, expected):
    assert skills.interval_minutes(hours) == expected


def test_bad_interval_rejected(project):
    skills.add_skill("x")
    skills.change_skill("x", {("jadwal", "mode"): "interval", ("jadwal", "tiap_jam"): 0.1})
    with pytest.raises(GeneratorError):
        skills.read_skill("x", settings.read_config())


def test_old_settings_default_to_hour_mode(project):
    skills.add_skill("lama")
    path = project.parent / "skill" / "lama" / "setelan.yaml"
    text = path.read_text(encoding="utf-8")
    path.write_text("\n".join(line for line in text.splitlines()
                              if not line.strip().startswith(("mode:", "tiap_jam:", "mulai:", "sampai:"))),
                    encoding="utf-8")
    skills.change_skill("lama", {("jadwal", "jam"): ["15:00"], ("jadwal", "hari"): ["jum"], ("", "nyala"): True})
    skill = skills.read_skill("lama", settings.read_config())
    assert skill.mode == "jam" and skill.due(at(0, 15)) and not skill.due(at(1, 15))


def test_menu_sets_interval_mode(project, monkeypatch):
    from generator import menu
    skills.add_skill("scalp")
    replies = iter(["2", "60", "20:00", "04:00", "sen, sel, rab, kam, jum"])
    monkeypatch.setattr(menu, "ask", lambda _: next(replies))
    menu.Menu().schedule(skills.read_skill("scalp", settings.read_config()))
    skill = skills.read_skill("scalp", settings.read_config())
    assert (skill.mode, skill.every, skill.start, skill.end) == ("interval", 60, "20:00", "04:00")
    replies = iter(["1", "15:00, 20:30", ""])
    monkeypatch.setattr(menu, "ask", lambda _: next(replies))
    menu.Menu().schedule(skill)
    skill = skills.read_skill("scalp", settings.read_config())
    assert skill.mode == "jam" and skill.times == ("15:00", "20:30")


def test_menu_global_skill_settings_update_only_inheriting_schedules(project, monkeypatch):
    from generator import menu

    for name in ("global", "custom", "legacy"):
        skills.add_skill(name)
        skills.change_skill(name, {("", "nyala"): True})
    skills.change_skill("custom", {("jadwal", "jam"): ["15:00"], ("", "model"): "haiku",
                                   ("", "effort"): "low", ("", "batas_waktu_menit"): 5})
    base = settings.read_config()
    editor = menu.Menu()
    replies = iter(["1", "", ""])
    monkeypatch.setattr(menu, "ask", lambda _: next(replies))
    editor.schedule(skills.read_skill("global", base))
    assert [config.skill_name for config in main.load_due(at(0, 9))] == ["global"]

    replies = iter(["opus", "high", "12", "10:30, 20:00"])
    editor.global_skill(base)
    base = settings.read_config()
    inherited = skills.read_skill("global", base)
    assert inherited.use_global_times and inherited.times == ("10:30", "20:00")
    assert (inherited.config.model, inherited.config.effort, inherited.config.timeout) == ("opus", "high", 720)
    custom = skills.read_skill("custom", base)
    assert custom.times == ("15:00",)
    assert (custom.config.model, custom.config.effort, custom.config.timeout) == ("haiku", "low", 300)
    assert skills.read_skill("legacy", base).times == ()
    assert not main.load_due(at(0, 9))
    assert [config.skill_name for config in main.load_due(at(0, 10, 30))] == ["global"]



def test_routine_settings_default_and_save(project):
    assert heartbeat.read() == heartbeat.Routine(True, ("07:00",))   # belum ada di config.yaml
    heartbeat.save(enabled=False, times=["19:00", "07:00"])
    routine = heartbeat.read()
    assert routine == heartbeat.Routine(False, ("07:00", "19:00")) and routine.describe() == "MATI"
    assert not routine.due(at(0, 7))
    heartbeat.save(enabled=True, times=["07:00", "19:00"])
    assert heartbeat.read().due(at(0, 19)) and not heartbeat.read().due(at(0, 8))
    assert heartbeat.read().describe() == "NYALA · jam 07:00, 19:00"


def test_routine_message_lists_schedule_and_last_day(project, monkeypatch):
    interval_skill(project)
    skills.add_skill("mati")   # skill MATI tidak disebut
    journal.append_log({"id": "a", "waktu": at(0, 3).isoformat(), "skill": "scalp", "status": journal.NONE,
                        "blok": [], "meta": {}})
    journal.append_log({"id": "b", "waktu": at(-3, 3).isoformat(), "skill": "scalp", "status": journal.FAIL,
                        "blok": [], "meta": {}})
    text = heartbeat.message(settings.read_config(), at(0, 7))
    assert text.startswith("🟢 CLI masih nyala · Jum 13/11 07:00 WIB")
    assert "- scalp (kode TES): tiap 60 menit 20:00–04:00 Sen–Jum · berikutnya 20:00" in text
    assert "mati" not in text.split("Jadwal analisa:")[1].split("\n\n")[0]
    assert "24 jam terakhir: 1 analisa · 0 setup ke bot · 1 tidak ada setup · 0 gagal" in text
    late = heartbeat.message(settings.read_config(), at(0, 23, 30))
    assert "berikutnya besok 00:00" in late   # Sabtu 00:00, jendela yang mulai Jumat


def test_pulse_and_label(tmp_path, monkeypatch):
    monkeypatch.setattr(heartbeat, "PULSE", tmp_path / "detak.txt")
    monkeypatch.setattr(heartbeat, "DATA", tmp_path)
    assert heartbeat.last_active_label(True) == ""
    heartbeat.pulse(at(0, 10, 5))
    assert heartbeat.last_active_label(True, at(0, 10, 6)) == " (terakhir aktif 10:05)"
    assert "tidak berdetak" in heartbeat.last_active_label(True, at(0, 10, 20))
    assert heartbeat.last_active_label(False, at(1, 9)) == " (terakhir aktif 13/11 10:05)"


def minute(day_offset, hour, minute_=0):
    return at(day_offset, hour, minute_)


def test_pending_minutes_normal_lag_sleep_and_clock_back():
    last = minute(0, 19, 59)
    assert main.pending_minutes(last, at(0, 19, 59) + timedelta(seconds=30)) == ([], [])     # menit yang sama
    assert main.pending_minutes(last, at(0, 20) + timedelta(seconds=1)) == ([minute(0, 20)], [])
    # Tersendat 5 detik melewati pergantian menit: dulu jadwal 20:00 hilang, sekarang tetap jalan.
    assert main.pending_minutes(last, at(0, 20) + timedelta(seconds=5)) == ([minute(0, 20)], [])
    # Tersendat 2 menit: dua menit yang lewat dikejar.
    assert main.pending_minutes(last, at(0, 20, 1) + timedelta(seconds=40)) == ([minute(0, 20), minute(0, 20, 1)], [])
    # PC sleep 10 menit: 3 menit terakhir dikejar, sisanya dicatat sebagai terlewat.
    run, missed = main.pending_minutes(last, at(0, 20, 9) + timedelta(seconds=20))
    assert run == [minute(0, 20, 7), minute(0, 20, 8), minute(0, 20, 9)]
    assert missed == [minute(0, 20, m) for m in range(0, 7)]
    # Jam mundur: tidak ada menit yang diulang.
    assert main.pending_minutes(minute(0, 20, 10), at(0, 20, 9) + timedelta(seconds=50)) == ([], [])
    # Lewat tengah malam.
    assert main.pending_minutes(minute(0, 23, 59), at(1, 0) + timedelta(seconds=2)) == ([minute(1, 0)], [])


def test_pending_minutes_checks_at_most_one_day_back():
    run, missed = main.pending_minutes(minute(0, 0), at(3, 0))   # PC sleep 3 hari
    assert len(run) == main.CATCH_UP_MINUTES and run[-1] == minute(3, 0)
    assert len(missed) == main.MISSED_CHECK_MINUTES and missed[-1] == run[0] - timedelta(minutes=1)


def test_missed_schedules_are_logged_not_run(project, caplog):
    skills.add_skill("pagi")
    skills.change_skill("pagi", {("", "nyala"): True, ("jadwal", "jam"): ["20:00"], ("jadwal", "hari"): ["jum"]})
    heartbeat.save(enabled=True, times=["20:02"])
    with caplog.at_level("INFO"):
        main.report_missed([minute(0, 20, m) for m in range(0, 5)], minute(0, 20))
    assert "Jadwal terlewat" in caplog.text
    assert "pagi 13/11 20:00" in caplog.text and "pesan rutin 13/11 20:02" in caplog.text
    caplog.clear()
    with caplog.at_level("INFO"):
        main.report_missed([minute(0, 21, m) for m in range(0, 5)], minute(0, 21))
    assert "Tidak ada jadwal terlewat" in caplog.text


def test_daemon_catches_up_after_lag_and_sleep(monkeypatch):
    """Putaran generator dengan jam palsu: tersendat 5 detik, PC sleep, lalu jam mundur."""
    times = iter([
        at(0, 19, 59) + timedelta(seconds=58),   # generator nyala
        at(0, 19, 59) + timedelta(seconds=59),
        at(0, 20) + timedelta(seconds=5),       # tersendat melewati detik 0–2
        at(0, 20) + timedelta(seconds=6),
        at(0, 20, 10) + timedelta(seconds=30),  # bangun dari sleep
        at(0, 20, 9) + timedelta(seconds=50),   # jam Windows mundur sedikit
        at(0, 20, 10) + timedelta(seconds=55),
        at(0, 20, 11) + timedelta(seconds=1),
    ])

    class Stop(Exception):
        pass

    class Clock:
        @staticmethod
        def now(_zone=None):
            try:
                return next(times)
            except StopIteration:
                raise Stop from None

    queued, missed = [], []
    monkeypatch.setattr(main, "datetime", Clock)
    monkeypatch.setattr(main.time, "sleep", lambda _: None)
    monkeypatch.setattr(main, "consume_queue", lambda pending: None)
    monkeypatch.setattr(main.heartbeat, "pulse", lambda now: None)
    monkeypatch.setattr(main, "queue_minute", lambda pending, item, now, secrets: queued.append(item))
    monkeypatch.setattr(main, "report_missed", lambda minutes, since: missed.append((minutes, since)))
    with pytest.raises(Stop):
        main.run_daemon({})
    assert queued == [minute(0, 20), minute(0, 20, 8), minute(0, 20, 9), minute(0, 20, 10), minute(0, 20, 11)]
    assert queued.count(minute(0, 20, 10)) == 1
    assert [(len(minutes), since) for minutes, since in missed if minutes] == [(7, minute(0, 20, 1))]


def test_queue_minute_uses_schedule_minute_not_seconds(project, monkeypatch):
    skills.add_skill("pagi")
    skills.change_skill("pagi", {("", "nyala"): True, ("jadwal", "jam"): ["20:00"], ("jadwal", "hari"): ["jum"]})
    heartbeat.save(enabled=True, times=["20:00"])
    items = []

    class Pending:
        def put(self, item):
            items.append(item)

    late = at(0, 20, 1) + timedelta(seconds=30)   # dikejar 90 detik kemudian
    main.queue_minute(Pending(), minute(0, 20), late, {})
    assert items[0] == (main.ROUTINE, late)
    assert [config.skill_name for config in items[1][0]] == ["pagi"]


def test_queue_sends_routine_without_running_analysis(monkeypatch):
    sent, ran = [], []
    monkeypatch.setattr(main, "send_routine", lambda now=None: sent.append(now))
    monkeypatch.setattr(main, "run_queue", lambda *args: ran.append(args))

    class Stop(Exception):
        pass

    items = iter([(main.ROUTINE, at(0, 7)), ([], {})])

    class Queue:
        def get(self):
            try:
                return next(items)
            except StopIteration:
                raise Stop from None

    with pytest.raises(Stop):
        main.consume_queue(Queue())
    assert sent == [at(0, 7)] and len(ran) == 1
